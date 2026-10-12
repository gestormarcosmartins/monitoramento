import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .auth import Identity, current_identity
from .db import connect, tenant_connection
from .settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/push", tags=["push"])
PUSH_ROLES = ("admin", "manager", "operator")
PRIORITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}
MIN_PRIORITY = 3  # alta e crítica
EVENT_LABELS = {"incident.created": "Novo incidente", "incident.resolved": "Normalizado"}


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=256)
    auth: str = Field(min_length=10, max_length=128)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048, pattern=r"^https://")
    keys: Keys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)


def _user(identity: Identity) -> dict[str, Any]:
    with connect() as connection:
        user = connection.execute("SELECT id,active FROM cm_users WHERE external_subject=%s", (identity.subject,)).fetchone()
    if not user or not user["active"]:
        raise HTTPException(status_code=403, detail="User is inactive or unknown")
    return user


@router.get("/public-key")
def public_key(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    key = get_settings().vapid_public_key
    if not key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Push notifications not configured")
    return {"public_key": key}


@router.post("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def subscribe(payload: SubscriptionIn, identity: Identity = Depends(current_identity)) -> None:
    user = _user(identity)
    with connect() as connection:
        connection.execute(
            """INSERT INTO cm_push_subscriptions(user_id,endpoint,p256dh,auth)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (endpoint) DO UPDATE SET user_id=excluded.user_id,p256dh=excluded.p256dh,auth=excluded.auth,last_error=NULL""",
            (user["id"], payload.endpoint, payload.keys.p256dh, payload.keys.auth),
        )


@router.post("/subscriptions/delete", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(payload: UnsubscribeIn, identity: Identity = Depends(current_identity)) -> None:
    user = _user(identity)
    with connect() as connection:
        connection.execute("DELETE FROM cm_push_subscriptions WHERE endpoint=%s AND user_id=%s", (payload.endpoint, user["id"]))


def _webpush(subscription: dict[str, Any], payload: dict[str, Any]) -> None:
    from pywebpush import webpush
    settings = get_settings()
    webpush(
        subscription_info={"endpoint": subscription["endpoint"], "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]}},
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        ttl=3600,
        timeout=10,
    )


def deliver_push(tenant_id: str, incident_id: str, event: str, incident: dict[str, Any]) -> None:
    """Best effort: nunca levanta exceção para não atrasar e-mail/WhatsApp nem refazer o outbox."""
    settings = get_settings()
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return
    if event not in EVENT_LABELS and not event.startswith("sla."):
        return
    if event == "incident.created" and PRIORITY.get(incident.get("priority"), 2) < MIN_PRIORITY:
        return
    try:
        with tenant_connection(tenant_id) as connection:
            users = [row["user_id"] for row in connection.execute(
                """SELECT DISTINCT g.user_id FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id
                WHERE g.tenant_id=%s AND g.active AND u.active AND g.role=ANY(%s)
                  AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())""",
                (tenant_id, list(PUSH_ROLES)),
            ).fetchall()]
        if not users:
            return
        with connect() as connection:
            subscriptions = list(connection.execute("SELECT * FROM cm_push_subscriptions WHERE user_id=ANY(%s)", (users,)).fetchall())
        label = EVENT_LABELS.get(event, "Alerta de SLA")
        payload = {"title": f"Nexora · {label}", "body": incident["title"], "tag": f"{incident_id}:{event}",
                   "url": f"/m/?tenant={tenant_id}&incident={incident_id}"}
        for subscription in subscriptions:
            with connect() as connection:
                first = connection.execute(
                    "INSERT INTO cm_push_deliveries(incident_id,event,subscription_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1",
                    (incident_id, event, subscription["id"]),
                ).fetchone()
            if not first:
                continue
            try:
                _webpush(subscription, payload)
                with connect() as connection:
                    connection.execute("UPDATE cm_push_subscriptions SET last_success_at=now(),last_error=NULL WHERE id=%s", (subscription["id"],))
            except Exception as error:
                code = getattr(getattr(error, "response", None), "status_code", None)
                with connect() as connection:
                    if code in (404, 410):  # inscrição expirada ou revogada
                        connection.execute("DELETE FROM cm_push_subscriptions WHERE id=%s", (subscription["id"],))
                    else:
                        connection.execute("UPDATE cm_push_subscriptions SET last_error=%s WHERE id=%s", (str(error)[:500], subscription["id"]))
                logger.warning("push_delivery_failed", extra={"subscription_id": str(subscription["id"]), "status": code})
    except Exception:
        logger.exception("push_delivery_error", extra={"incident_id": incident_id})
