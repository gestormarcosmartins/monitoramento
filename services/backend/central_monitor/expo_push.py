"""Push Notifications Nexora via Expo Push Service (especificação v1.0, PUSH-001..008).

Fluxo: evento -> create_notification (notificação + destinatários + entregas na mesma transação)
-> worker (process_push_deliveries) -> Expo /push/send -> ticket -> process_push_receipts.
Estados técnicos de entrega ficam em nx_push_deliveries; abertura e confirmação humana ficam
em nx_notification_recipients e nunca sobrescrevem a trilha técnica.
"""
import hashlib
import logging
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import connect, tenant_connection
from .push import _user
from .secrets import decrypt_secret, encrypt_secret
from .settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notificacoes", tags=["notificacoes"])

EXPO_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
TOKEN_PATTERN = re.compile(r"^(Exponent|Expo)PushToken\[[A-Za-z0-9_\-]{10,100}\]$")
MAX_ATTEMPTS = 6
BATCH_SIZE = 100
RECEIPT_DELAY = timedelta(minutes=15)
RECEIPT_GIVE_UP = timedelta(hours=24)
PROCESSING_TIMEOUT = timedelta(minutes=5)
INFRA_ROLES = ("admin", "manager", "operator")
PRIORITY_SEVERITY = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}
TTL_BY_SEVERITY = {"P0": timedelta(hours=4), "P1": timedelta(hours=8), "P2": timedelta(hours=24), "P3": timedelta(hours=48)}


# ---------------------------------------------------------------- funções puras (testáveis)
def valid_expo_token(token: str) -> bool:
    return bool(TOKEN_PATTERN.match(token or ""))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_token(token: str) -> str:
    return f"{token[:16]}…{token[-4:]}" if len(token) > 24 else "…"


def backoff_seconds(attempt: int, jitter: float | None = None) -> int:
    base = min(1800, 30 * 2 ** max(attempt - 1, 0))
    return int(base * (0.5 + (random.random() if jitter is None else jitter) * 0.5))


def severity_for_priority(priority: str | None) -> str:
    return PRIORITY_SEVERITY.get(priority or "", "P2")


def build_event(event: str, incident: dict[str, Any]) -> dict[str, Any] | None:
    """Traduz um evento de incidente na notificação INFRA; devolve None quando não gera push."""
    severity = severity_for_priority(incident.get("priority"))
    if event.startswith("sla."):
        if severity in ("P2", "P3"):
            return None
        return {"category": "sla", "severity": severity, "require_ack": False,
                "title": "Nexora · Prazo de SLA", "body_safe": "Um prazo de atendimento precisa de atenção."}
    if event == "incident.created":
        if severity not in ("P0", "P1"):
            return None
        return {"category": "indisponibilidade", "severity": severity, "require_ack": severity == "P0",
                "title": f"Nexora · Incidente {severity}", "body_safe": "Novo incidente exige atenção. Toque para ver."}
    if event == "incident.resolved":
        if severity not in ("P0", "P1"):
            return None
        return {"category": "recuperacao", "severity": severity, "require_ack": False,
                "title": "Nexora · Normalizado", "body_safe": "Um incidente foi normalizado."}
    return None


# ---------------------------------------------------------------- produção (PUSH-004, PUSH-007)
DOMAINS = ("INFRA", "BUSINESS", "SERVICE")
SEVERITY_ALIASES = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P3"}


def normalize_severity(value: str) -> str:
    value = SEVERITY_ALIASES.get(value, value)
    if value not in TTL_BY_SEVERITY:
        raise ValueError("Severidade inválida")
    return value


def has_domain_access(db: Any, tenant_id: str, user_id: Any, domain: str) -> bool:
    """Autorização atual do usuário no domínio. INFRA e BUSINESS são independentes: um não implica o outro."""
    if domain == "INFRA":
        return bool(db.execute(
            """SELECT 1 FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id WHERE g.tenant_id=%s AND g.user_id=%s
            AND g.active AND u.active AND g.role=ANY(%s) AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())""",
            (tenant_id, user_id, list(INFRA_ROLES))).fetchone())
    base = """SELECT 1 FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id WHERE g.tenant_id=%s AND g.user_id=%s
        AND g.active AND u.active AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())"""
    if domain == "BUSINESS":
        return bool(db.execute(
            base + """ AND EXISTS (SELECT 1 FROM cm_business_grants b WHERE b.tenant_id=g.tenant_id AND b.user_id=g.user_id
            AND b.capability='business.view' AND b.active)""", (tenant_id, user_id)).fetchone())
    if domain == "SERVICE":
        return bool(db.execute(base, (tenant_id, user_id)).fetchone())
    return False


def visible_domains(db: Any, tenant_id: str, user_id: Any) -> list[str]:
    return [domain for domain in DOMAINS if has_domain_access(db, tenant_id, user_id, domain)]


def enqueue_notification(db: Any, tenant_id: str, *, domain: str, source_type: str, source_id: str, event_key: str,
                         severity: str, category: str, title: str, body: str, action_type: str, action_ref: str | None,
                         recipients: list[Any], requires_ack: bool = False, ttl_seconds: int | None = None,
                         correlation_key: str | None = None) -> Any | None:
    """Ponto único de produção de push. Chamar dentro da transação que gerou o evento.

    Devolve None quando o evento já foi enfileirado (deduplicação por empresa + event_key).
    Destinatários sem acesso atual ao domínio são descartados aqui e revalidados a cada envio.
    """
    if domain not in DOMAINS:
        raise ValueError("Domínio de notificação desconhecido")
    severity = normalize_severity(severity)
    expires_at = datetime.now(UTC) + (timedelta(seconds=ttl_seconds) if ttl_seconds else TTL_BY_SEVERITY[severity])
    notification = db.execute(
        """INSERT INTO nx_notifications(tenant_id,domain,source_type,source_id,event_key,correlation_key,
            severity,category,title,body_safe,action_type,action_ref,require_ack,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (tenant_id,event_key) DO NOTHING RETURNING id""",
        (tenant_id, domain, source_type, source_id, event_key, correlation_key, severity, category,
         title[:160], body[:300], action_type, action_ref, requires_ack, expires_at)).fetchone()
    if not notification:
        return None
    users = [u for u in dict.fromkeys(recipients) if has_domain_access(db, tenant_id, u, domain)]
    muted = {row["user_id"] for row in db.execute(
        """SELECT user_id FROM nx_notification_preferences WHERE tenant_id=%s AND user_id=ANY(%s)
        AND domain=%s AND category=%s AND NOT push_enabled""", (tenant_id, users, domain, category)).fetchall()} if users else set()
    for user_id in users:
        recipient = db.execute(
            "INSERT INTO nx_notification_recipients(tenant_id,notification_id,user_id) VALUES (%s,%s,%s) RETURNING id",
            (tenant_id, notification["id"], user_id)).fetchone()
        if user_id in muted:  # P0 só ignora preferência com política administrativa (backlog P1)
            continue
        db.execute(
            """INSERT INTO nx_push_deliveries(tenant_id,recipient_id,device_id)
            SELECT %s::uuid,%s::uuid,d.id FROM nx_push_devices d WHERE d.user_id=%s AND d.enabled AND d.permission_status='granted'
            ON CONFLICT DO NOTHING""", (tenant_id, recipient["id"], user_id))
    db.execute("INSERT INTO nx_push_audit(tenant_id,action,target,metadata) VALUES (%s,'notification.created',%s,%s)",
               (tenant_id, str(notification["id"]), Jsonb({"domain": domain, "event": event_key.split(":")[-1], "recipients": len(users)})))
    return notification["id"]


def create_infra_notification(tenant_id: str, incident_id: str, event: str, incident: dict[str, Any]) -> None:
    """Chamado pelo outbox de notificações. Nunca levanta: push não pode travar e-mail/WhatsApp."""
    try:
        spec = build_event(event, incident)
        if not spec:
            return
        with tenant_connection(tenant_id) as db:
            users = [row["user_id"] for row in db.execute(
                "SELECT DISTINCT g.user_id FROM cm_access_grants g WHERE g.tenant_id=%s AND g.active AND g.role=ANY(%s)",
                (tenant_id, list(INFRA_ROLES))).fetchall()]
            enqueue_notification(
                db, tenant_id, domain="INFRA", source_type="incident", source_id=incident_id,
                event_key=f"incident:{incident_id}:{event}", correlation_key=f"incident:{incident_id}",
                severity=spec["severity"], category=spec["category"], title=spec["title"], body=spec["body_safe"],
                action_type="INCIDENT", action_ref=incident_id, recipients=users, requires_ack=spec["require_ack"])
    except Exception:
        logger.exception("expo_push_create_failed", extra={"incident_id": incident_id})


# ---------------------------------------------------------------- worker de envio (PUSH-005)
def _expo_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = get_settings().expo_access_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _claim(connection: Any, tenant_id: str) -> list[dict[str, Any]]:
    connection.execute(
        "UPDATE nx_push_deliveries SET state='RETRY_WAIT',updated_at=now() WHERE tenant_id=%s AND state='PROCESSING' AND updated_at<now()-%s::interval",
        (tenant_id, f"{int(PROCESSING_TIMEOUT.total_seconds())} seconds"))
    return list(connection.execute(
        """UPDATE nx_push_deliveries d SET state='PROCESSING',attempt_no=d.attempt_no+1,updated_at=now()
        WHERE d.id IN (SELECT id FROM nx_push_deliveries WHERE tenant_id=%s AND state IN ('PENDING','RETRY_WAIT')
            AND next_attempt_at<=now() ORDER BY next_attempt_at LIMIT %s FOR UPDATE SKIP LOCKED)
        RETURNING d.*""", (tenant_id, BATCH_SIZE)).fetchall())


def _finish(connection: Any, delivery_id: Any, state: str, **fields: Any) -> None:
    connection.execute(
        """UPDATE nx_push_deliveries SET state=%s,expo_ticket_id=COALESCE(%s,expo_ticket_id),
        accepted_at=COALESCE(%s,accepted_at),provider_error_code=%s,last_error=%s,
        next_attempt_at=COALESCE(%s,next_attempt_at),updated_at=now() WHERE id=%s""",
        (state, fields.get("ticket"), fields.get("accepted_at"), fields.get("code"),
         (fields.get("error") or None) and str(fields["error"])[:500], fields.get("next_attempt_at"), delivery_id))


def _invalidate_device(device_id: Any, reason: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE nx_push_devices SET enabled=false,invalidated_at=now(),reason=%s,updated_at=now() WHERE id=%s",
            (reason, device_id))


def _prepare(connection: Any, tenant_id: str, delivery: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Revalida escopo antes de cada envio. Devolve (mensagem, motivo_de_descarte)."""
    row = connection.execute(
        """SELECT n.id AS notification_id,n.domain,n.severity,n.title,n.body_safe,n.action_type,n.action_ref,n.expires_at,
        r.user_id FROM nx_notification_recipients r JOIN nx_notifications n ON n.id=r.notification_id
        WHERE r.id=%s AND r.tenant_id=%s""", (delivery["recipient_id"], tenant_id)).fetchone()
    if not row:
        return None, "recipient_missing"
    if row["expires_at"] and row["expires_at"] < datetime.now(UTC):
        return None, "expired"
    if not has_domain_access(connection, tenant_id, row["user_id"], row["domain"]):
        return None, "unauthorized"
    with connect() as devices_db:
        device = devices_db.execute("SELECT * FROM nx_push_devices WHERE id=%s", (delivery["device_id"],)).fetchone()
    if not device or not device["enabled"]:
        return None, "device_disabled"
    message = {
        "to": decrypt_secret(device["expo_push_token_encrypted"]),
        "title": row["title"], "body": row["body_safe"], "sound": "default",
        "priority": "high" if row["severity"] in ("P0", "P1") else "default",
        "channelId": "alerts", "ttl": 3600 if row["severity"] != "P0" else 14400,
        "data": {"type": row["action_type"], "notificationId": str(row["notification_id"]),
                 "domain": row["domain"], "tenantId": tenant_id, "actionRef": str(row["action_ref"]) if row["action_ref"] else None},
    }
    return message, None


def _retry(connection: Any, delivery: dict[str, Any], error: str, code: str | None = None) -> None:
    if delivery["attempt_no"] >= MAX_ATTEMPTS:
        _finish(connection, delivery["id"], "FAILED_FINAL", error=error, code=code)
    else:
        _finish(connection, delivery["id"], "RETRY_WAIT", error=error, code=code,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=backoff_seconds(delivery["attempt_no"])))


def _send_tenant(tenant_id: str, client: httpx.Client) -> None:
    with tenant_connection(tenant_id) as connection:
        deliveries = _claim(connection, tenant_id)
    if not deliveries:
        return
    ready: list[tuple[dict[str, Any], dict[str, Any]]] = []
    with tenant_connection(tenant_id) as connection:
        for delivery in deliveries:
            message, discard = _prepare(connection, tenant_id, delivery)
            if discard == "expired":
                _finish(connection, delivery["id"], "EXPIRED")
            elif discard:
                _finish(connection, delivery["id"], "FAILED_FINAL", error=discard, code=discard)
            else:
                ready.append((delivery, message))
    if not ready:
        return
    try:
        response = client.post(EXPO_SEND_URL, headers=_expo_headers(), json=[m for _, m in ready])
        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(f"expo_http_{response.status_code}")
        response.raise_for_status()
        tickets = response.json().get("data", [])
        if len(tickets) != len(ready):
            raise RuntimeError("expo_ticket_count_mismatch")
    except Exception as error:  # falha transitória de transporte/limite: tenta de novo com backoff
        with tenant_connection(tenant_id) as connection:
            for delivery, _ in ready:
                _retry(connection, delivery, str(error))
        logger.warning("expo_send_transient_failure", extra={"tenant_id": tenant_id, "error": str(error)})
        return
    now = datetime.now(UTC)
    with tenant_connection(tenant_id) as connection:
        for (delivery, _), ticket in zip(ready, tickets, strict=True):
            if ticket.get("status") == "ok":
                _finish(connection, delivery["id"], "SENT_TO_EXPO", ticket=ticket.get("id"), accepted_at=now)
                continue
            code = (ticket.get("details") or {}).get("error") or "unknown"
            if code == "DeviceNotRegistered":
                _finish(connection, delivery["id"], "DEVICE_INVALID", code=code, error=ticket.get("message"))
                _invalidate_device(delivery["device_id"], "DeviceNotRegistered")
            elif code in ("MessageRateExceeded",):
                _retry(connection, delivery, ticket.get("message") or code, code)
            else:  # MessageTooBig, InvalidCredentials, MismatchSenderId...: permanente
                _finish(connection, delivery["id"], "FAILED_FINAL", code=code, error=ticket.get("message"))


def process_push_deliveries() -> None:
    with connect() as connection:
        tenant_ids = [str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active")]
    with httpx.Client(timeout=20) as client:
        for tenant_id in tenant_ids:
            try:
                _send_tenant(tenant_id, client)
            except Exception:
                logger.exception("expo_push_tenant_failed", extra={"tenant_id": tenant_id})


# ---------------------------------------------------------------- worker de receipts (PUSH-006)
def _receipts_tenant(tenant_id: str, client: httpx.Client) -> None:
    with tenant_connection(tenant_id) as connection:
        pending = list(connection.execute(
            """SELECT id,device_id,expo_ticket_id,accepted_at FROM nx_push_deliveries
            WHERE tenant_id=%s AND state='SENT_TO_EXPO' AND expo_ticket_id IS NOT NULL AND accepted_at<=now()-%s::interval
            ORDER BY accepted_at LIMIT 300""", (tenant_id, f"{int(RECEIPT_DELAY.total_seconds())} seconds")).fetchall())
    if not pending:
        return
    response = client.post(EXPO_RECEIPTS_URL, headers=_expo_headers(), json={"ids": [p["expo_ticket_id"] for p in pending]})
    if response.status_code == 429 or response.status_code >= 500:
        raise RuntimeError(f"expo_receipts_http_{response.status_code}")
    response.raise_for_status()
    receipts = response.json().get("data", {})
    now = datetime.now(UTC)
    with tenant_connection(tenant_id) as connection:
        for delivery in pending:
            receipt = receipts.get(delivery["expo_ticket_id"])
            if receipt is None:
                if now - delivery["accepted_at"] > RECEIPT_GIVE_UP:
                    _finish(connection, delivery["id"], "FAILED_FINAL", error="receipt_unavailable", code="receipt_unavailable")
                continue
            connection.execute("UPDATE nx_push_deliveries SET receipt_checked_at=now() WHERE id=%s", (delivery["id"],))
            if receipt.get("status") == "ok":
                _finish(connection, delivery["id"], "PROVIDER_ACCEPTED")
                continue
            code = (receipt.get("details") or {}).get("error") or "unknown"
            if code == "DeviceNotRegistered":
                _finish(connection, delivery["id"], "DEVICE_INVALID", code=code, error=receipt.get("message"))
                _invalidate_device(delivery["device_id"], "DeviceNotRegistered")
            else:
                _finish(connection, delivery["id"], "FAILED_FINAL", code=code, error=receipt.get("message"))


def process_push_receipts() -> None:
    with connect() as connection:
        tenant_ids = [str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active")]
    with httpx.Client(timeout=30) as client:
        for tenant_id in tenant_ids:
            try:
                _receipts_tenant(tenant_id, client)
            except Exception:
                logger.exception("expo_receipts_tenant_failed", extra={"tenant_id": tenant_id})


# ---------------------------------------------------------------- API (PUSH-002, PUSH-008)
class DeviceIn(BaseModel):
    expoPushToken: str = Field(min_length=20, max_length=200)
    platform: str = Field(pattern="^(android|ios)$")
    installationId: str = Field(min_length=8, max_length=100)
    expoProjectId: str = Field(min_length=8, max_length=100)
    appVersion: str | None = Field(default=None, max_length=40)
    permissionStatus: str = Field(default="granted", pattern="^(granted|denied|undetermined)$")


def _allowed_projects() -> set[str]:
    return {item.strip() for item in get_settings().expo_project_ids.split(",") if item.strip()}


@router.post("/dispositivos-push")
def register_device(payload: DeviceIn, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    # Validações sem banco primeiro; sem lista de projetos configurada nenhum token é aceito (falha fechada).
    allowed = _allowed_projects()
    if not allowed:
        raise HTTPException(status_code=503, detail="Push não configurado neste ambiente (defina EXPO_PROJECT_IDS)")
    if payload.expoProjectId not in allowed:
        raise HTTPException(status_code=403, detail="Projeto Expo não autorizado neste ambiente")
    if not valid_expo_token(payload.expoPushToken):
        raise HTTPException(status_code=422, detail="Token Expo inválido")
    user = _user(identity)
    enabled = payload.permissionStatus == "granted"
    with connect() as connection:
        # troca de token: desativa os anteriores da mesma instalação
        connection.execute(
            """UPDATE nx_push_devices SET enabled=false,invalidated_at=now(),reason='token_replaced',updated_at=now()
            WHERE user_id=%s AND installation_id=%s AND enabled AND NOT (token_hash=%s AND expo_project_id=%s)""",
            (user["id"], payload.installationId, token_hash(payload.expoPushToken), payload.expoProjectId))
        device = connection.execute(
            """INSERT INTO nx_push_devices(user_id,platform,expo_push_token_encrypted,token_hash,expo_project_id,installation_id,
                app_version,enabled,permission_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (token_hash,expo_project_id) DO UPDATE SET user_id=excluded.user_id,platform=excluded.platform,
                installation_id=excluded.installation_id,app_version=excluded.app_version,enabled=excluded.enabled,
                permission_status=excluded.permission_status,invalidated_at=NULL,reason=NULL,last_seen_at=now(),updated_at=now()
            RETURNING id,platform,enabled,last_seen_at""",
            (user["id"], payload.platform, encrypt_secret(payload.expoPushToken), token_hash(payload.expoPushToken),
             payload.expoProjectId, payload.installationId, payload.appVersion, enabled, payload.permissionStatus)).fetchone()
    logger.info("expo_device_registered", extra={"device_id": str(device["id"]), "platform": payload.platform})
    return {"id": device["id"], "platform": device["platform"], "enabled": device["enabled"]}


@router.get("/dispositivos-push")
def list_devices(identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    user = _user(identity)
    with connect() as connection:
        rows = connection.execute(
            """SELECT id,platform,app_version,enabled,permission_status,last_seen_at,invalidated_at,reason,
            expo_push_token_encrypted FROM nx_push_devices WHERE user_id=%s ORDER BY last_seen_at DESC""", (user["id"],)).fetchall()
    return [{**{k: v for k, v in row.items() if k != "expo_push_token_encrypted"},
             "token": mask_token(decrypt_secret(row["expo_push_token_encrypted"]))} for row in rows]


@router.delete("/dispositivos-push/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(device_id: UUID, identity: Identity = Depends(current_identity)) -> None:
    user = _user(identity)
    with connect() as connection:
        revoked = connection.execute(
            """UPDATE nx_push_devices SET enabled=false,invalidated_at=now(),reason='user_revoked',updated_at=now()
            WHERE id=%s AND user_id=%s RETURNING id""", (device_id, user["id"])).fetchone()
    if not revoked:
        raise HTTPException(status_code=404, detail="Dispositivo não encontrado")


def _member(identity: Identity, tenant_id: str) -> dict[str, Any]:
    """Membro da empresa, com os domínios que ele pode ver AGORA (perda de acesso vale imediatamente)."""
    from .operations import authorize  # import tardio: evita ciclo com main
    user = authorize(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        user["domains"] = visible_domains(db, tenant_id, user["id"])
    if not user["domains"]:
        raise HTTPException(status_code=403, detail="Sem acesso a notificações nesta empresa")
    return user


@router.get("")
def list_notifications(
    only_unread: bool = False, severity: str | None = Query(default=None, pattern="^P[0-3]$"),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    user = _member(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        conditions, params = ["r.tenant_id=%s", "r.user_id=%s", "n.domain=ANY(%s)"], [tenant_id, user["id"], user["domains"]]
        if only_unread:
            conditions.append("r.opened_at IS NULL")
        if severity:
            conditions.append("n.severity=%s"); params.append(severity)
        where = " AND ".join(conditions)
        totals = connection.execute(
            f"""SELECT count(*) AS total,count(*) FILTER (WHERE n.severity IN ('P0','P1')) AS critical,
            count(*) FILTER (WHERE r.opened_at IS NULL) AS unread,
            count(*) FILTER (WHERE n.require_ack AND r.acknowledged_at IS NULL) AS awaiting_ack
            FROM nx_notification_recipients r JOIN nx_notifications n ON n.id=r.notification_id WHERE {where}""", params).fetchone()
        items = connection.execute(
            f"""SELECT n.id,n.domain,n.severity,n.category,n.title,n.body_safe,n.action_type,n.action_ref,n.require_ack,
            n.created_at,n.expires_at,r.opened_at,r.acknowledged_at FROM nx_notification_recipients r
            JOIN nx_notifications n ON n.id=r.notification_id WHERE {where} ORDER BY n.created_at DESC LIMIT %s OFFSET %s""",
            [*params, limit, offset]).fetchall()
    return {"totals": totals, "items": items}


def _mark(notification_id: UUID, tenant_id: str, identity: Identity, column: str) -> dict[str, Any]:
    user = _member(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        if column == "acknowledged_at":
            ack = connection.execute("SELECT require_ack FROM nx_notifications WHERE tenant_id=%s AND id=%s", (tenant_id, notification_id)).fetchone()
            if ack and not ack["require_ack"]:
                raise HTTPException(status_code=409, detail="Esta notificação não exige confirmação")
        row = connection.execute(
            f"""UPDATE nx_notification_recipients r SET {column}=COALESCE(r.{column},now())
            FROM nx_notifications n WHERE n.id=r.notification_id AND r.tenant_id=%s AND r.notification_id=%s AND r.user_id=%s
            AND n.domain=ANY(%s) RETURNING r.notification_id""",
            (tenant_id, notification_id, user["id"], user["domains"])).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notificação não encontrada")
        notification = connection.execute(
            "SELECT action_type,action_ref FROM nx_notifications WHERE tenant_id=%s AND id=%s", (tenant_id, notification_id)).fetchone()
        connection.execute(
            "INSERT INTO nx_push_audit(tenant_id,actor,action,target) VALUES (%s,%s,%s,%s)",
            (tenant_id, user["id"], "notification.opened" if column == "opened_at" else "notification.acknowledged", str(notification_id)))
    return notification


@router.post("/{notification_id}/abrir")
def open_notification(notification_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """Autoriza na hora (papel atual) e devolve o destino; o deep link nunca carrega permissão."""
    return _mark(notification_id, tenant_id, identity, "opened_at")


@router.post("/{notification_id}/confirmar")
def acknowledge_notification(notification_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    return _mark(notification_id, tenant_id, identity, "acknowledged_at")


@router.get("/entregas")
def list_deliveries(
    state: str | None = None, limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    from .operations import authorize
    authorize(identity, tenant_id, ("admin", "manager", "auditor"))
    with tenant_connection(tenant_id) as connection:
        summary = connection.execute(
            "SELECT state,count(*) AS total FROM nx_push_deliveries WHERE tenant_id=%s GROUP BY state", (tenant_id,)).fetchall()
        oldest = connection.execute(
            "SELECT EXTRACT(EPOCH FROM now()-min(created_at))::int AS queue_age_seconds FROM nx_push_deliveries WHERE tenant_id=%s AND state IN ('PENDING','RETRY_WAIT')",
            (tenant_id,)).fetchone()
        items = connection.execute(
            """SELECT id,recipient_id,state,attempt_no,provider_error_code,last_error,accepted_at,receipt_checked_at,created_at,updated_at
            FROM nx_push_deliveries WHERE tenant_id=%s AND (%s::text IS NULL OR state=%s) ORDER BY created_at DESC LIMIT %s""",
            (tenant_id, state, state, limit)).fetchall()
    return {"summary": summary, "queue_age_seconds": oldest["queue_age_seconds"] or 0, "items": items}
