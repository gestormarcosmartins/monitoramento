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
    target = target.strip()
    number = target if target.endswith("@g.us") else re.sub(r"\D", "", target)
    if not target.endswith("@g.us") and (len(number) < 10 or len(number) > 15):
        raise RuntimeError("Número de WhatsApp inválido. Informe somente números, incluindo país e DDD; exemplo: 5511999999999")
    url = f"{config['evolution_api_url'].rstrip('/')}/message/sendText/{config['evolution_instance']}"
    response = httpx.post(url, headers={"apikey": config["evolution_api_key"]}, json={"number": number, "text": body}, timeout=20)
    if response.status_code == 400:
        raise RuntimeError("A Evolution recusou o número informado. Use somente números com país e DDD; exemplo: 5511999999999")
    if response.status_code == 404:
        raise RuntimeError("A instância do WhatsApp não foi encontrada na Evolution API")
    if response.status_code >= 500:
        raise RuntimeError("A Evolution API não conseguiu enviar. Verifique se a instância do WhatsApp está conectada")
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
    failures = []
    try:
        _deliver_responsible_contacts(tenant_id, incident_id, event, incident)
    except Exception as error:
        failures.append(str(error))
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
            failures.append(str(error))
    if failures:
        raise RuntimeError("; ".join(failures))


def _deliver_responsible_contacts(tenant_id: str, incident_id: str, event: str, incident: dict[str, Any]) -> None:
    """Deliver service alerts to its team and/or technician with retry-safe deduplication."""
    with tenant_connection(tenant_id) as connection:
        route = connection.execute(
            """SELECT m.responsible_team_id,m.responsible_user_id,m.notify_on_failure,m.notify_on_recovery,
                t.emails,t.whatsapp_numbers,t.whatsapp_group_id,
                ru.email AS responsible_email,ru.whatsapp_number AS responsible_whatsapp
            FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id
            JOIN cm_monitors m ON m.id=a.monitor_id
            LEFT JOIN cm_teams t ON t.id=m.responsible_team_id AND t.active
            LEFT JOIN cm_users ru ON ru.id=m.responsible_user_id AND ru.active
            WHERE i.tenant_id=%s AND i.id=%s""",
            (tenant_id,incident_id),
        ).fetchone()
        if not route:
            return
        members = list(connection.execute(
            """SELECT u.email,u.whatsapp_number FROM cm_team_members tm JOIN cm_users u ON u.id=tm.user_id
            WHERE tm.tenant_id=%s AND tm.team_id=%s AND u.active""",
            (tenant_id,route["responsible_team_id"]),
        ).fetchall()) if route["responsible_team_id"] else []
    if event == "incident.created" and not route["notify_on_failure"]:
        return
    if event == "incident.resolved" and not route["notify_on_recovery"]:
        return
    emails = set(route["emails"] or [])
    whatsapp = set(route["whatsapp_numbers"] or [])
    if route["whatsapp_group_id"]:
        whatsapp.add(route["whatsapp_group_id"])
    if route["responsible_email"]:
        emails.add(route["responsible_email"])
    if route["responsible_whatsapp"]:
        whatsapp.add(route["responsible_whatsapp"])
    for member in members:
        if member["email"]: emails.add(member["email"])
        if member["whatsapp_number"]: whatsapp.add(member["whatsapp_number"])
    label = "FALHA" if event == "incident.created" else "NORMALIZADO"
    message = f"Nexora - {label}\n{incident['title']}\nPrioridade: {incident['priority']}\nEstado: {incident['status']}"
    failures = []
    for channel, targets in (("email",emails),("whatsapp",whatsapp)):
        for target in targets:
            with tenant_connection(tenant_id) as connection:
                delivery = connection.execute(
                    """INSERT INTO cm_routing_deliveries(tenant_id,incident_id,event,channel,target,status,attempts)
                    VALUES (%s,%s,%s,%s,%s,'pending',1)
                    ON CONFLICT(incident_id,event,channel,target) DO UPDATE SET attempts=cm_routing_deliveries.attempts+1
                    RETURNING *""",(tenant_id,incident_id,event,channel,target)).fetchone()
            if delivery["status"] == "sent":
                continue
            try:
                if channel == "email": send_email(tenant_id,target,f"Nexora - {label}: {incident['title']}",message)
                else: send_whatsapp(tenant_id,target,message)
                with tenant_connection(tenant_id) as connection:
                    connection.execute("UPDATE cm_routing_deliveries SET status='sent',sent_at=now(),last_error=NULL WHERE tenant_id=%s AND id=%s",(tenant_id,delivery["id"]))
            except Exception as error:
                failures.append(f"{channel}:{target}: {error}")
                with tenant_connection(tenant_id) as connection:
                    connection.execute("UPDATE cm_routing_deliveries SET status='failed',last_error=%s WHERE tenant_id=%s AND id=%s",(str(error)[:1000],tenant_id,delivery["id"]))
    if failures:
        raise RuntimeError("; ".join(failures))
