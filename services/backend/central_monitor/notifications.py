import smtplib
from email.message import EmailMessage
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import tenant_connection
from .settings import get_settings

PRIORITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _send_email(target: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        raise RuntimeError("SMTP is not configured")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, target, subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def _send_webhook(target: str, payload: dict[str, Any]) -> None:
    if not target.startswith(("https://", "http://")):
        raise RuntimeError("Webhook target must be HTTP or HTTPS")
    response = httpx.post(target, json=payload, timeout=15)
    response.raise_for_status()


def deliver_notifications(tenant_id: str, incident_id: str, event: str) -> None:
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            "SELECT id,title,status,priority,created_at FROM cm_incidents WHERE tenant_id=%s AND id=%s",
            (tenant_id,incident_id),
        ).fetchone()
        if not incident:
            return
        rules = list(connection.execute(
            "SELECT * FROM cm_notification_rules WHERE tenant_id=%s AND active AND %s=ANY(events)",
            (tenant_id,event),
        ).fetchall())
    for rule in rules:
        if PRIORITY.get(incident["priority"],2) < PRIORITY.get(rule["minimum_priority"],3):
            continue
        with tenant_connection(tenant_id) as connection:
            delivery = connection.execute(
                """INSERT INTO cm_notification_deliveries(tenant_id,rule_id,incident_id,event,channel,target,status)
                VALUES (%s,%s,%s,%s,%s,%s,'pending') ON CONFLICT(rule_id,incident_id,event) DO UPDATE
                SET attempts=cm_notification_deliveries.attempts+1 RETURNING *""",
                (tenant_id,rule["id"],incident_id,event,rule["channel"],rule["target"]),
            ).fetchone()
        payload = {"event":event,"incident_id":incident_id,"title":incident["title"],"status":incident["status"],"priority":incident["priority"]}
        try:
            if rule["channel"] == "email":
                _send_email(rule["target"],f"Nexora: {incident['title']}",f"Evento: {event}\nPrioridade: {incident['priority']}\nEstado: {incident['status']}")
            else:
                _send_webhook(rule["target"],payload)
            with tenant_connection(tenant_id) as connection:
                connection.execute("UPDATE cm_notification_deliveries SET status='sent',sent_at=now(),last_error=NULL WHERE tenant_id=%s AND id=%s",(tenant_id,delivery["id"]))
        except Exception as error:
            with tenant_connection(tenant_id) as connection:
                connection.execute("UPDATE cm_notification_deliveries SET status='failed',last_error=%s WHERE tenant_id=%s AND id=%s",(str(error)[:1000],tenant_id,delivery["id"]))
            raise
