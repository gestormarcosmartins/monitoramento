import re
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from .db import tenant_connection
from .secrets import decrypt_secret, encrypt_secret
from .settings import get_settings

PRIORITY = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def get_notification_settings(tenant_id: str, include_secrets: bool = False) -> dict[str, Any]:
    with tenant_connection(tenant_id) as connection:
        row = connection.execute("SELECT * FROM cm_notification_settings WHERE tenant_id=%s", (tenant_id,)).fetchone()
    defaults, data = get_settings(), dict(row or {})
    result = {
        "smtp_enabled": data.get("smtp_enabled", bool(defaults.smtp_host)),
        "smtp_host": data.get("smtp_host") or defaults.smtp_host,
        "smtp_port": data.get("smtp_port") or defaults.smtp_port,
        "smtp_username": data.get("smtp_username") or defaults.smtp_username,
        "smtp_from_address": data.get("smtp_from_address") or defaults.smtp_from,
        "smtp_tls": data.get("smtp_tls", defaults.smtp_tls),
        "smtp_password_configured": bool(data.get("smtp_password_encrypted") or defaults.smtp_password),
        "evolution_enabled": data.get("evolution_enabled", False),
        "evolution_api_url": data.get("evolution_api_url") or "",
        "evolution_instance": data.get("evolution_instance") or "",
        "evolution_api_key_configured": bool(data.get("evolution_api_key_encrypted")),
        "updated_at": data.get("updated_at"),
    }
    if include_secrets:
        result["smtp_password"] = decrypt_secret(data.get("smtp_password_encrypted")) or defaults.smtp_password
        result["evolution_api_key"] = decrypt_secret(data.get("evolution_api_key_encrypted"))
    return result


def save_notification_settings(tenant_id: str, values: dict[str, Any]) -> dict[str, Any]:
    with tenant_connection(tenant_id) as connection:
        current = connection.execute("SELECT smtp_password_encrypted,evolution_api_key_encrypted FROM cm_notification_settings WHERE tenant_id=%s", (tenant_id,)).fetchone() or {}
        smtp_secret = encrypt_secret(values.get("smtp_password")) if values.get("smtp_password") else current.get("smtp_password_encrypted")
        evolution_secret = encrypt_secret(values.get("evolution_api_key")) if values.get("evolution_api_key") else current.get("evolution_api_key_encrypted")
        connection.execute(
            """INSERT INTO cm_notification_settings(
                tenant_id,smtp_enabled,smtp_host,smtp_port,smtp_username,smtp_password_encrypted,
                smtp_from_address,smtp_tls,evolution_enabled,evolution_api_url,
                evolution_api_key_encrypted,evolution_instance,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (tenant_id) DO UPDATE SET
                smtp_enabled=excluded.smtp_enabled,smtp_host=excluded.smtp_host,smtp_port=excluded.smtp_port,
                smtp_username=excluded.smtp_username,smtp_password_encrypted=excluded.smtp_password_encrypted,
                smtp_from_address=excluded.smtp_from_address,smtp_tls=excluded.smtp_tls,
                evolution_enabled=excluded.evolution_enabled,evolution_api_url=excluded.evolution_api_url,
                evolution_api_key_encrypted=excluded.evolution_api_key_encrypted,
                evolution_instance=excluded.evolution_instance,updated_at=now()""",
            (tenant_id, values["smtp_enabled"], values.get("smtp_host") or None, values["smtp_port"], values.get("smtp_username") or None, smtp_secret, values.get("smtp_from_address") or None, values["smtp_tls"], values["evolution_enabled"], values.get("evolution_api_url") or None, evolution_secret, values.get("evolution_instance") or None),
        )
    return get_notification_settings(tenant_id)


def send_email(tenant_id: str, target: str, subject: str, body: str) -> None:
    config = get_notification_settings(tenant_id, include_secrets=True)
    if not config["smtp_enabled"] or not config["smtp_host"]:
        raise RuntimeError("SMTP não está habilitado ou configurado")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = config["smtp_from_address"], target, subject
    message.set_content(body)
    with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=15) as smtp:
        if config["smtp_tls"]: smtp.starttls()
        if config["smtp_username"]: smtp.login(config["smtp_username"], config["smtp_password"])
        smtp.send_message(message)


def send_whatsapp(tenant_id: str, target: str, body: str) -> None:
    config = get_notification_settings(tenant_id, include_secrets=True)
    if not config["evolution_enabled"] or not config["evolution_api_url"] or not config["evolution_instance"]:
        raise RuntimeError("Evolution API não está habilitada ou configurada")
    if not config["evolution_api_url"].startswith(("https://", "http://")):
        raise RuntimeError("A URL da Evolution API deve usar HTTP ou HTTPS")
    number = re.sub(r"\D", "", target)
    if len(number) < 10: raise RuntimeError("Número de WhatsApp inválido; informe país e DDD")
    url = f"{config['evolution_api_url'].rstrip('/')}/message/sendText/{config['evolution_instance']}"
    response = httpx.post(url, headers={"apikey": config["evolution_api_key"]}, json={"number": number, "text": body}, timeout=20)
    response.raise_for_status()


def _send_webhook(target: str, payload: dict[str, Any]) -> None:
    if not target.startswith(("https://", "http://")): raise RuntimeError("Webhook target must be HTTP or HTTPS")
    response = httpx.post(target, json=payload, timeout=15)
    response.raise_for_status()


def deliver_notifications(tenant_id: str, incident_id: str, event: str) -> None:
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute("SELECT id,title,status,priority,created_at FROM cm_incidents WHERE tenant_id=%s AND id=%s", (tenant_id,incident_id)).fetchone()
        if not incident: return
        rules = list(connection.execute("SELECT * FROM cm_notification_rules WHERE tenant_id=%s AND active AND %s=ANY(events)", (tenant_id,event)).fetchall())
    for rule in rules:
        if PRIORITY.get(incident["priority"],2) < PRIORITY.get(rule["minimum_priority"],3): continue
        with tenant_connection(tenant_id) as connection:
            delivery = connection.execute("""INSERT INTO cm_notification_deliveries(tenant_id,rule_id,incident_id,event,channel,target,status)
                VALUES (%s,%s,%s,%s,%s,%s,'pending') ON CONFLICT(rule_id,incident_id,event) DO UPDATE
                SET attempts=cm_notification_deliveries.attempts+1 RETURNING *""", (tenant_id,rule["id"],incident_id,event,rule["channel"],rule["target"])).fetchone()
        payload = {"event":event,"incident_id":incident_id,"title":incident["title"],"status":incident["status"],"priority":incident["priority"]}
        message = f"Nexora: {incident['title']}\nEvento: {event}\nPrioridade: {incident['priority']}\nEstado: {incident['status']}"
        try:
            if rule["channel"] == "email": send_email(tenant_id, rule["target"], f"Nexora: {incident['title']}", message)
            elif rule["channel"] == "whatsapp": send_whatsapp(tenant_id, rule["target"], message)
            else: _send_webhook(rule["target"], payload)
            with tenant_connection(tenant_id) as connection:
                connection.execute("UPDATE cm_notification_deliveries SET status='sent',sent_at=now(),last_error=NULL WHERE tenant_id=%s AND id=%s",(tenant_id,delivery["id"]))
        except Exception as error:
            with tenant_connection(tenant_id) as connection:
                connection.execute("UPDATE cm_notification_deliveries SET status='failed',last_error=%s WHERE tenant_id=%s AND id=%s",(str(error)[:1000],tenant_id,delivery["id"]))
            raise
