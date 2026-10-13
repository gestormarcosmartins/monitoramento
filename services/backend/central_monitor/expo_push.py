"""Mobile push through the Expo Push Service.

Flow: event -> enqueue_notification() (notification + recipients + deliveries in the caller's transaction)
-> process_expo_pushes() worker (send, tickets) -> check_receipts() -> app opens -> backend re-authorizes.

Rules from the NexoraPush spec: the tenant is never taken from the app; every delivery re-checks that the
user is still active and still has access to the company/domain; tokens are encrypted at rest and never
logged; a ticket/receipt is a provider hand-off, never proof of reading; opened/acknowledged are user events.
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import Identity, current_identity, tenant_header
from .db import connect, tenant_connection
from .settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/notificacoes", tags=["notificacoes-push"])

TOKEN_RE = re.compile(r"^(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]{8,}\]$")
MAX_ATTEMPTS = 6
BATCH = 100
RECEIPT_BATCH = 300
RECEIPT_GIVE_UP = timedelta(hours=24)  # Expo keeps receipts for 24 h
DOMAINS = ("INFRA", "BUSINESS", "SERVICE")


# ---------------------------------------------------------------- pure helpers (unit tested)
def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_hint(token: str) -> str:
    return "…" + token[-5:-1]


def _fernet() -> Fernet:
    key = get_settings().push_token_key
    if not key:
        raise RuntimeError("PUSH_TOKEN_KEY is not configured")
    return Fernet(key.encode())


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(cipher: str) -> str:
    return _fernet().decrypt(cipher.encode()).decode()


def allowed_project_ids() -> set[str]:
    return {x.strip() for x in get_settings().expo_project_ids.split(",") if x.strip()}


def retry_delay_seconds(attempt: int) -> float:
    base = min(900, 2 ** min(max(attempt, 1), 9))
    return base + random.uniform(0, base * 0.2)  # jitter avoids synchronized retry storms


def classify_ticket(entry: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Expo send response item -> (state, ticket_id, error_code)."""
    if entry.get("status") == "ok" and entry.get("id"):
        return "SENT_TO_EXPO", str(entry["id"]), None
    code = (entry.get("details") or {}).get("error") or "UnknownProviderError"
    if code == "DeviceNotRegistered":
        return "DEVICE_INVALID", None, code
    if code == "MessageRateExceeded":
        return "RETRY_WAIT", None, code
    return "FAILED_FINAL", None, code


def classify_receipt(entry: dict[str, Any]) -> tuple[str, str | None]:
    """Expo receipt item -> (state, error_code). 'ok' only means FCM/APNs accepted it."""
    if entry.get("status") == "ok":
        return "PROVIDER_ACCEPTED", None
    code = (entry.get("details") or {}).get("error") or "UnknownProviderError"
    return ("DEVICE_INVALID" if code == "DeviceNotRegistered" else "FAILED_FINAL"), code


def build_message(token: str, row: dict[str, Any], tenant_id: str, now: datetime) -> dict[str, Any]:
    urgent = row["severity"] in ("high", "critical")
    ttl = max(60, int((row["expires_at"] - now).total_seconds()))
    return {
        "to": token, "title": row["title"], "body": row["body_safe"], "sound": "default",
        "priority": "high" if urgent else "default", "channelId": "alerts" if urgent else "default", "ttl": ttl,
        # Ids only: the app must call the API, and the backend authorizes the object at that moment.
        "data": {"type": row["action_type"], "notificationId": str(row["notification_id"]), "tenantId": tenant_id,
                 "domain": row["domain"], "actionRef": row["action_ref"]},
    }


# ---------------------------------------------------------------- models / auth helpers
class DeviceIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    expo_push_token: str = Field(alias="expoPushToken", min_length=20, max_length=200)
    platform: Literal["android", "ios"]
    installation_id: str = Field(alias="installationId", min_length=8, max_length=128)
    expo_project_id: str = Field(alias="expoProjectId", min_length=8, max_length=64)
    app_version: str | None = Field(default=None, alias="appVersion", max_length=32)
    permission_status: Literal["granted", "denied", "undetermined"] = Field(default="granted", alias="permissionStatus")

    @field_validator("expo_push_token")
    @classmethod
    def valid_token(cls, value: str) -> str:
        if not TOKEN_RE.fullmatch(value):
            raise ValueError("Not an Expo push token")
        return value


def _user(identity: Identity) -> dict[str, Any]:
    from .main import ensure_user  # lazy: main includes this router
    user = ensure_user(identity)
    if not user["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is inactive")
    return user


def _tenant_user(identity: Identity, tenant_id: str, roles: tuple[str, ...] | None = None) -> dict[str, Any]:
    from .main import TENANT_ROLES, authorize_tenant
    return authorize_tenant(identity, tenant_id, roles or TENANT_ROLES)


def _has_access(db: Any, tenant_id: str, user_id: Any, domain: str) -> bool:
    """Same rule at enqueue, at every delivery and when the user opens the item."""
    member = db.execute("""SELECT 1 FROM cm_users u WHERE u.id=%s AND u.active AND (u.is_master OR EXISTS(
        SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=%s AND g.user_id=u.id AND g.active
          AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())))""",
                        (user_id, tenant_id)).fetchone()
    if not member:
        return False
    if domain != "BUSINESS":
        return True
    # Infrastructure rights never imply Business rights (spec section 4).
    return bool(db.execute("""SELECT 1 FROM cm_business_grants g JOIN cm_feature_flags f
        ON f.tenant_id=g.tenant_id AND f.feature='business_monitor' AND f.mode IN ('enabled','write_pilot')
        WHERE g.tenant_id=%s AND g.user_id=%s AND g.capability='business.view' AND g.active""",
                      (tenant_id, user_id)).fetchone())


# ---------------------------------------------------------------- device endpoints (PUSH-002)
@router.post("/dispositivos-push")
def register_device(payload: DeviceIn, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = _user(identity)
    if payload.expo_project_id not in allowed_project_ids():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expo project is not allowed in this environment")
    try:
        cipher = encrypt_token(payload.expo_push_token)
    except RuntimeError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push is not configured") from error
    digest = token_hash(payload.expo_push_token)
    with connect() as db:
        # A token has one active owner: release it from any other user/installation first.
        db.execute("""UPDATE cm_push_devices SET enabled=false,invalidated_at=now(),invalid_reason='token_reassigned',
            updated_at=now() WHERE token_hash=%s AND enabled AND NOT (user_id=%s AND installation_id=%s)""",
                   (digest, user["id"], payload.installation_id))
        row = db.execute("""INSERT INTO cm_push_devices(user_id,installation_id,platform,token_cipher,token_hash,
              token_hint,expo_project_id,app_version,permission_status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,installation_id) DO UPDATE SET platform=excluded.platform,
              token_cipher=excluded.token_cipher,token_hash=excluded.token_hash,token_hint=excluded.token_hint,
              expo_project_id=excluded.expo_project_id,app_version=excluded.app_version,
              permission_status=excluded.permission_status,enabled=true,invalidated_at=NULL,invalid_reason=NULL,
              last_seen_at=now(),updated_at=now()
            RETURNING id,enabled""",
                         (user["id"], payload.installation_id, payload.platform, cipher, digest,
                          token_hint(payload.expo_push_token), payload.expo_project_id, payload.app_version,
                          payload.permission_status)).fetchone()
    return {"id": row["id"], "enabled": row["enabled"]}


@router.get("/dispositivos-push")
def list_devices(identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    user = _user(identity)
    with connect() as db:
        return list(db.execute("""SELECT id,platform,installation_id,app_version,permission_status,enabled,
              token_hint,last_seen_at,invalid_reason FROM cm_push_devices WHERE user_id=%s
            ORDER BY last_seen_at DESC""", (user["id"],)).fetchall())


@router.delete("/dispositivos-push/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(device_id: UUID, identity: Identity = Depends(current_identity)) -> None:
    user = _user(identity)
    with connect() as db:
        row = db.execute("""UPDATE cm_push_devices SET enabled=false,invalidated_at=now(),invalid_reason='user_logout',
            updated_at=now() WHERE id=%s AND user_id=%s RETURNING id""", (device_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")


# ---------------------------------------------------------------- producing notifications (PUSH-004)
def enqueue_notification(db: Any, tenant_id: str, *, domain: str, source_type: str, source_id: str, event_key: str,
                         severity: str, category: str, title: str, body: str, action_type: str, action_ref: str,
                         recipients: list[Any], requires_ack: bool = False, ttl_seconds: int = 3600,
                         correlation_key: str | None = None) -> Any | None:
    """Call inside the transaction that produced the event. Returns None when the event was already queued."""
    if domain not in DOMAINS:
        raise ValueError("Unknown notification domain")
    notification = db.execute("""INSERT INTO cm_notifications(tenant_id,domain,source_type,source_id,event_key,
          correlation_key,severity,category,title,body_safe,action_type,action_ref,requires_ack,expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()+(%s * interval '1 second'))
        ON CONFLICT (tenant_id,domain,event_key) DO NOTHING RETURNING id""",
                              (tenant_id, domain, source_type, source_id, event_key, correlation_key, severity, category,
                               title[:160], body[:300], action_type, action_ref, requires_ack, ttl_seconds)).fetchone()
    if not notification:
        return None  # deduplicated: same tenant + domain + event
    for user_id in dict.fromkeys(recipients):
        if not _has_access(db, tenant_id, user_id, domain):
            continue
        recipient = db.execute("""INSERT INTO cm_notification_recipients(tenant_id,notification_id,user_id)
            VALUES (%s,%s,%s) RETURNING id""", (tenant_id, notification["id"], user_id)).fetchone()
        db.execute("""INSERT INTO cm_push_expo_deliveries(tenant_id,recipient_id,device_id)
            SELECT %s::uuid,%s::uuid,d.id FROM cm_push_devices d WHERE d.user_id=%s AND d.enabled
              AND d.permission_status='granted'""", (tenant_id, recipient["id"], user_id))
    return notification["id"]


INCIDENT_EVENTS = {"incident.created": ("Novo incidente", "incident"), "incident.resolved": ("Normalizado", "recovery")}
PUSH_ROLES = ("admin", "manager", "operator")
PRIORITY_SEVERITY = {"low": "low", "medium": "medium", "high": "high", "critical": "critical"}


def enqueue_incident_push(tenant_id: str, incident_id: str, event: str, incident: dict[str, Any]) -> None:
    """Infra incident -> mobile push (PUSH-007). Best effort and idempotent by event key."""
    if event not in INCIDENT_EVENTS and not event.startswith("sla."):
        return
    priority = incident.get("priority") or "medium"
    if event == "incident.created" and priority not in ("high", "critical"):
        return
    label, category = INCIDENT_EVENTS.get(event, ("Alerta de SLA", "sla"))
    try:
        with tenant_connection(tenant_id) as db:
            users = [r["user_id"] for r in db.execute(
                """SELECT DISTINCT g.user_id FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id
                WHERE g.tenant_id=%s AND g.active AND u.active AND g.role=ANY(%s)
                  AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())""",
                (tenant_id, list(PUSH_ROLES))).fetchall()]
            if users:
                enqueue_notification(
                    db, tenant_id, domain="INFRA", source_type="incident", source_id=str(incident_id),
                    event_key=f"incident:{incident_id}:{event}", correlation_key=f"incident:{incident_id}",
                    severity=PRIORITY_SEVERITY.get(priority, "medium"), category=category,
                    title=f"Nexora · {label}", body=incident["title"], action_type="incident",
                    action_ref=str(incident_id), recipients=users, requires_ack=event == "incident.created"
                    and priority == "critical")
    except Exception:
        logger.exception("expo_push_enqueue_failed", extra={"incident_id": str(incident_id)})


# ---------------------------------------------------------------- worker: send + receipts (PUSH-005/006)
def _expo_post(path: str, body: Any) -> Any:
    settings = get_settings()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.expo_access_token:
        headers["Authorization"] = f"Bearer {settings.expo_access_token}"
    response = httpx.post(f"{settings.expo_push_url.rstrip('/')}/{path}", json=body, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def _tenant_ids() -> list[str]:
    with connect() as db:
        return [str(r["id"]) for r in db.execute("SELECT id FROM cm_tenants WHERE active ORDER BY id").fetchall()]


def _set_state(db: Any, tenant_id: str, delivery_id: Any, state: str, *, code: str | None = None,
               error: str | None = None, ticket: str | None = None, retry_in: float | None = None) -> None:
    db.execute("""UPDATE cm_push_expo_deliveries SET state=%s,provider_error_code=COALESCE(%s,provider_error_code),
          last_error=%s,expo_ticket_id=COALESCE(%s,expo_ticket_id),
          sent_at=CASE WHEN %s='SENT_TO_EXPO' THEN now() ELSE sent_at END,
          next_attempt_at=CASE WHEN %s::double precision IS NOT NULL THEN now()+(%s::double precision * interval '1 second')
            ELSE next_attempt_at END,
          locked_at=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s""",
               (state, code, error[:500] if error else None, ticket, state, retry_in, retry_in, tenant_id, delivery_id))


def _disable_device(db: Any, device_id: Any, reason: str) -> None:
    db.execute("""UPDATE cm_push_devices SET enabled=false,invalidated_at=now(),invalid_reason=%s,updated_at=now()
        WHERE id=%s""", (reason, device_id))


def send_pending(tenant_id: str) -> int:
    now = datetime.now(UTC)
    with tenant_connection(tenant_id) as db:
        claimed = db.execute("""WITH c AS (SELECT id FROM cm_push_expo_deliveries WHERE tenant_id=%s AND (
              (state IN ('PENDING','RETRY_WAIT') AND next_attempt_at<=now())
              OR (state='PROCESSING' AND locked_at<now()-interval '10 minutes'))
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s)
          UPDATE cm_push_expo_deliveries d SET state='PROCESSING',locked_at=now(),attempt_no=d.attempt_no+1,
            updated_at=now() FROM c WHERE d.id=c.id RETURNING d.id""", (tenant_id, BATCH)).fetchall()
        ids = [r["id"] for r in claimed]
        if not ids:
            return 0
        rows = db.execute("""SELECT d.id AS delivery_id,d.attempt_no,d.device_id,n.id AS notification_id,n.domain,
              n.severity,n.title,n.body_safe,n.action_type,n.action_ref,n.expires_at,r.user_id,
              dev.token_cipher,dev.enabled AS device_enabled
            FROM cm_push_expo_deliveries d
            JOIN cm_notification_recipients r ON r.tenant_id=d.tenant_id AND r.id=d.recipient_id
            JOIN cm_notifications n ON n.tenant_id=r.tenant_id AND n.id=r.notification_id
            JOIN cm_push_devices dev ON dev.id=d.device_id
            WHERE d.tenant_id=%s AND d.id=ANY(%s)""", (tenant_id, ids)).fetchall()
        sendable: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            if row["expires_at"] <= now:
                _set_state(db, tenant_id, row["delivery_id"], "EXPIRED", error="notification_expired")
            elif not row["device_enabled"]:
                _set_state(db, tenant_id, row["delivery_id"], "FAILED_FINAL", error="device_disabled")
            elif not _has_access(db, tenant_id, row["user_id"], row["domain"]):
                _set_state(db, tenant_id, row["delivery_id"], "FAILED_FINAL", error="access_revoked")
            else:
                try:
                    sendable.append((row, build_message(decrypt_token(row["token_cipher"]), row, tenant_id, now)))
                except (InvalidToken, RuntimeError):
                    _set_state(db, tenant_id, row["delivery_id"], "FAILED_FINAL", error="token_undecryptable")
    if not sendable:
        return 0
    try:
        result = _expo_post("send", [message for _, message in sendable])
        tickets = result.get("data") or []
        if len(tickets) != len(sendable):
            raise RuntimeError("Expo returned an unexpected number of tickets")
    except Exception as error:  # transport/429/5xx: nothing was confirmed, retry with backoff
        logger.warning("expo_send_failed", extra={"tenant_id": tenant_id, "error_type": type(error).__name__})
        with tenant_connection(tenant_id) as db:
            for row, _ in sendable:
                if row["attempt_no"] >= MAX_ATTEMPTS:
                    _set_state(db, tenant_id, row["delivery_id"], "FAILED_FINAL", error=f"attempts_exhausted:{type(error).__name__}")
                else:
                    _set_state(db, tenant_id, row["delivery_id"], "RETRY_WAIT", error=type(error).__name__,
                               retry_in=retry_delay_seconds(row["attempt_no"]))
        return 0
    with tenant_connection(tenant_id) as db:
        for (row, _), ticket in zip(sendable, tickets, strict=True):
            state, ticket_id, code = classify_ticket(ticket)
            if state == "RETRY_WAIT" and row["attempt_no"] >= MAX_ATTEMPTS:
                state = "FAILED_FINAL"
            if state == "DEVICE_INVALID":
                _disable_device(db, row["device_id"], "DeviceNotRegistered")
            _set_state(db, tenant_id, row["delivery_id"], state, code=code, ticket=ticket_id,
                       error=(ticket.get("message") if state != "SENT_TO_EXPO" else None),
                       retry_in=retry_delay_seconds(row["attempt_no"]) if state == "RETRY_WAIT" else None)
    return len(sendable)


def check_receipts(tenant_id: str) -> int:
    delay = get_settings().expo_receipt_delay_seconds
    with tenant_connection(tenant_id) as db:
        rows = db.execute("""SELECT id,device_id,expo_ticket_id,sent_at FROM cm_push_expo_deliveries
            WHERE tenant_id=%s AND state='SENT_TO_EXPO' AND sent_at<=now()-(%s * interval '1 second')
              AND (receipt_checked_at IS NULL OR receipt_checked_at<=now()-interval '5 minutes')
            ORDER BY sent_at LIMIT %s""", (tenant_id, delay, RECEIPT_BATCH)).fetchall()
    if not rows:
        return 0
    try:
        receipts = (_expo_post("getReceipts", {"ids": [r["expo_ticket_id"] for r in rows]}).get("data")) or {}
    except Exception as error:
        logger.warning("expo_receipts_failed", extra={"tenant_id": tenant_id, "error_type": type(error).__name__})
        return 0
    now = datetime.now(UTC)
    with tenant_connection(tenant_id) as db:
        for row in rows:
            entry = receipts.get(row["expo_ticket_id"])
            if entry is None:
                if now - row["sent_at"] > RECEIPT_GIVE_UP:
                    _set_state(db, tenant_id, row["id"], "FAILED_FINAL", code="ReceiptUnavailable", error="receipt_unavailable")
                else:
                    db.execute("UPDATE cm_push_expo_deliveries SET receipt_checked_at=now() WHERE tenant_id=%s AND id=%s",
                               (tenant_id, row["id"]))
                continue
            state, code = classify_receipt(entry)
            if state == "DEVICE_INVALID":
                _disable_device(db, row["device_id"], "DeviceNotRegistered")
            _set_state(db, tenant_id, row["id"], state, code=code, error=entry.get("message") if code else None)
            db.execute("UPDATE cm_push_expo_deliveries SET receipt_checked_at=now() WHERE tenant_id=%s AND id=%s",
                       (tenant_id, row["id"]))
    return len(rows)


def process_expo_pushes() -> None:
    """Worker entry point; one tenant failing never blocks the others."""
    for tenant_id in _tenant_ids():
        for step in (send_pending, check_receipts):
            try:
                step(tenant_id)
            except Exception:
                logger.exception("expo_push_step_failed", extra={"tenant_id": tenant_id, "step": step.__name__})


# ---------------------------------------------------------------- Central endpoints (PUSH-008)
@router.get("")
def list_notifications(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity),
                       unread_only: bool = False, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    user = _tenant_user(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        rows = db.execute("""SELECT n.id,n.domain,n.severity,n.category,n.title,n.body_safe,n.action_type,n.action_ref,
              n.requires_ack,n.created_at,n.expires_at,r.opened_at,r.acknowledged_at
            FROM cm_notification_recipients r JOIN cm_notifications n ON n.tenant_id=r.tenant_id AND n.id=r.notification_id
            WHERE r.tenant_id=%s AND r.user_id=%s AND (NOT %s OR r.opened_at IS NULL)
              AND (n.domain<>'BUSINESS' OR EXISTS(SELECT 1 FROM cm_business_grants g WHERE g.tenant_id=r.tenant_id
                   AND g.user_id=r.user_id AND g.capability='business.view' AND g.active))
            ORDER BY n.created_at DESC LIMIT %s OFFSET %s""",
                          (tenant_id, user["id"], unread_only, page_size, (page - 1) * page_size)).fetchall()
    return {"page": page, "page_size": page_size, "items": list(rows)}


def _recipient_row(db: Any, tenant_id: str, notification_id: UUID, user_id: Any) -> dict[str, Any]:
    row = db.execute("""SELECT r.id,n.domain,n.action_type,n.action_ref,n.requires_ack FROM cm_notification_recipients r
        JOIN cm_notifications n ON n.tenant_id=r.tenant_id AND n.id=r.notification_id
        WHERE r.tenant_id=%s AND r.notification_id=%s AND r.user_id=%s""", (tenant_id, notification_id, user_id)).fetchone()
    # Same 404 whether it does not exist, belongs to someone else, or access was revoked.
    if not row or not _has_access(db, tenant_id, user_id, row["domain"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    return row


@router.post("/{notification_id}/abrir")
def open_notification(notification_id: UUID, tenant_id: str = Depends(tenant_header),
                      identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = _tenant_user(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = _recipient_row(db, tenant_id, notification_id, user["id"])
        db.execute("UPDATE cm_notification_recipients SET opened_at=COALESCE(opened_at,now()) WHERE tenant_id=%s AND id=%s",
                   (tenant_id, row["id"]))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'open','notification',%s)",
                   (tenant_id, user["id"], str(notification_id)))
    return {"action_type": row["action_type"], "action_ref": row["action_ref"], "domain": row["domain"]}


@router.post("/{notification_id}/confirmar")
def acknowledge_notification(notification_id: UUID, tenant_id: str = Depends(tenant_header),
                             identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = _tenant_user(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = _recipient_row(db, tenant_id, notification_id, user["id"])
        if not row["requires_ack"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "This notification does not require acknowledgement")
        db.execute("""UPDATE cm_notification_recipients SET opened_at=COALESCE(opened_at,now()),
            acknowledged_at=COALESCE(acknowledged_at,now()) WHERE tenant_id=%s AND id=%s""", (tenant_id, row["id"]))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'acknowledge','notification',%s)",
                   (tenant_id, user["id"], str(notification_id)))
    return {"acknowledged": True}


@router.get("/entregas/saude")
def delivery_health(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _tenant_user(identity, tenant_id, ("admin", "manager"))
    with tenant_connection(tenant_id) as db:
        counts = {r["state"]: r["total"] for r in db.execute(
            "SELECT state,count(*) AS total FROM cm_push_expo_deliveries WHERE tenant_id=%s GROUP BY state",
            (tenant_id,)).fetchall()}
        oldest = db.execute("""SELECT EXTRACT(EPOCH FROM now()-min(created_at))::int AS age FROM cm_push_expo_deliveries
            WHERE tenant_id=%s AND state IN ('PENDING','RETRY_WAIT')""", (tenant_id,)).fetchone()
        receipts = db.execute("""SELECT count(*) AS total FROM cm_push_expo_deliveries
            WHERE tenant_id=%s AND state='SENT_TO_EXPO'""", (tenant_id,)).fetchone()
    return {"by_state": counts, "oldest_pending_age_seconds": oldest["age"], "receipts_pending": receipts["total"]}


@router.get("/entregas")
def list_deliveries(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity),
                    state: str | None = None, limit: int = Query(100, ge=1, le=200)):
    _tenant_user(identity, tenant_id, ("admin", "manager"))
    with tenant_connection(tenant_id) as db:
        return list(db.execute("""SELECT d.id,d.state,d.attempt_no,d.provider_error_code,d.last_error,d.sent_at,
              d.receipt_checked_at,d.created_at,n.title,n.domain,n.severity,dev.platform,dev.token_hint
            FROM cm_push_expo_deliveries d JOIN cm_notification_recipients r ON r.tenant_id=d.tenant_id AND r.id=d.recipient_id
            JOIN cm_notifications n ON n.tenant_id=r.tenant_id AND n.id=r.notification_id
            JOIN cm_push_devices dev ON dev.id=d.device_id
            WHERE d.tenant_id=%s AND (%s::text IS NULL OR d.state=%s) ORDER BY d.created_at DESC LIMIT %s""",
                                 (tenant_id, state, state, limit)).fetchall())
