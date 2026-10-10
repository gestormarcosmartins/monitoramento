import hashlib
import logging
import signal
import socket
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import connect, migrate, tenant_connection
from .glpi import GlpiClient
from .settings import get_settings

logger = logging.getLogger("central-monitor-worker")
running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def check_http(configuration: dict[str, Any]) -> tuple[bool, str]:
    url = configuration["url"]
    expected = configuration.get("expected_status", [200, 201, 202, 204, 301, 302])
    timeout = min(float(configuration.get("timeout_seconds", 10)), 30)
    started = time.monotonic()
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        ok = response.status_code in expected
        return ok, f"HTTP {response.status_code} in {elapsed_ms} ms"
    except httpx.HTTPError as error:
        return False, f"HTTP check failed: {type(error).__name__}"


def check_tcp(configuration: dict[str, Any]) -> tuple[bool, str]:
    host = configuration["host"]
    port = int(configuration["port"])
    timeout = min(float(configuration.get("timeout_seconds", 5)), 30)
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return True, f"TCP {host}:{port} connected in {elapsed_ms} ms"
    except OSError as error:
        return False, f"TCP check failed: {type(error).__name__}"


def check_deadman(tenant_id: str, configuration: dict[str, Any]) -> tuple[bool, str]:
    source = configuration["source"]
    event_type = configuration["event_type"]
    max_silence = int(configuration["max_silence_seconds"])
    with tenant_connection(tenant_id) as connection:
        row = connection.execute(
            """SELECT max(occurred_at) AS last_event FROM cm_events
            WHERE tenant_id=%s AND source=%s AND event_type=%s""",
            (tenant_id, source, event_type),
        ).fetchone()
    if not row["last_event"]:
        return False, "No matching event has been received"
    age = (datetime.now(UTC) - row["last_event"]).total_seconds()
    return age <= max_silence, f"Last event received {int(age)} seconds ago"


def execute_check(monitor: dict[str, Any]) -> tuple[bool, str]:
    monitor_type = monitor["monitor_type"]
    if monitor_type == "http":
        return check_http(monitor["configuration"])
    if monitor_type == "tcp":
        return check_tcp(monitor["configuration"])
    if monitor_type == "event_deadman":
        return check_deadman(str(monitor["tenant_id"]), monitor["configuration"])
    return True, "Delegated to Zabbix"


def fingerprint(monitor_id: str) -> str:
    return hashlib.sha256(f"monitor:{monitor_id}:down".encode()).hexdigest()


def record_result(monitor: dict[str, Any], ok: bool, message: str) -> None:
    tenant_id = str(monitor["tenant_id"])
    state = "up" if ok else "down"
    alert_fingerprint = fingerprint(str(monitor["id"]))
    with tenant_connection(tenant_id) as connection:
        connection.execute(
            "UPDATE cm_monitors SET last_checked_at=now(),last_status=%s,last_message=%s WHERE id=%s",
            (state, message, monitor["id"]),
        )
        if not ok:
            alert = connection.execute(
                """
                INSERT INTO cm_alerts(tenant_id,monitor_id,fingerprint,severity,title,message)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,fingerprint,status) DO UPDATE SET message=excluded.message
                RETURNING id,title
                """,
                (tenant_id, monitor["id"], alert_fingerprint, monitor["severity"], f"{monitor['name']} indisponivel", message),
            ).fetchone()
            incident = connection.execute(
                """
                INSERT INTO cm_incidents(tenant_id,alert_id,title)
                VALUES (%s,%s,%s) ON CONFLICT (tenant_id,alert_id) DO NOTHING
                RETURNING id
                """,
                (tenant_id, alert["id"], alert["title"]),
            ).fetchone()
            if incident:
                connection.execute(
                    "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.created',%s)",
                    (tenant_id, Jsonb({"incident_id": str(incident["id"])})),
                )
        else:
            alerts = connection.execute(
                """UPDATE cm_alerts SET status='resolved',resolved_at=now()
                WHERE tenant_id=%s AND fingerprint=%s AND status='open' RETURNING id""",
                (tenant_id, alert_fingerprint),
            ).fetchall()
            for alert in alerts:
                incidents = connection.execute(
                    """UPDATE cm_incidents SET status='resolved',version=version+1,updated_at=now(),sync_status='pending'
                    WHERE tenant_id=%s AND alert_id=%s AND status NOT IN ('resolved','ignored') RETURNING id""",
                    (tenant_id, alert["id"]),
                ).fetchall()
                for incident in incidents:
                    connection.execute(
                        "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.status_changed',%s)",
                        (tenant_id, Jsonb({"incident_id": str(incident["id"]), "status": "resolved"})),
                    )


def run_monitors() -> None:
    with connect() as connection:
        tenant_ids = [str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active")]
    for tenant_id in tenant_ids:
        with tenant_connection(tenant_id) as connection:
            monitors = list(
                connection.execute(
                    """
                    SELECT * FROM cm_monitors
                    WHERE tenant_id=%s AND active
                    AND (last_checked_at IS NULL OR last_checked_at + interval_seconds * interval '1 second' <= now())
                    ORDER BY last_checked_at NULLS FIRST LIMIT 50
                    """,
                    (tenant_id,),
                ).fetchall()
            )
        for monitor in monitors:
            try:
                ok, message = execute_check(monitor)
                record_result(monitor, ok, message)
            except Exception:
                logger.exception("monitor_check_failed", extra={"monitor_id": str(monitor["id"])})


def sync_incident(tenant_id: str, incident_id: str) -> None:
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            """SELECT i.*,a.severity,a.message,t.name AS tenant_name
            FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id
            JOIN cm_tenants t ON t.id=i.tenant_id
            WHERE i.tenant_id=%s AND i.id=%s""",
            (tenant_id, incident_id),
        ).fetchone()
    if not incident:
        return
    client = GlpiClient()
    ticket_id = incident["glpi_ticket_id"]
    if ticket_id is None:
        ticket_id = client.create_ticket(incident)
    client.update_ticket_status(int(ticket_id), incident["status"])
    with tenant_connection(tenant_id) as connection:
        connection.execute(
            """UPDATE cm_incidents SET glpi_ticket_id=%s,sync_status='synced',sync_error=NULL,synced_at=now()
            WHERE tenant_id=%s AND id=%s""",
            (ticket_id, tenant_id, incident_id),
        )


def process_outbox() -> None:
    with connect() as connection:
        rows = list(
            connection.execute(
                """SELECT id,tenant_id,topic,payload,attempts FROM cm_outbox WHERE processed_at IS NULL
                AND topic <> 'action.validated'
                AND attempts < 10
                ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 100"""
            ).fetchall()
        )
    for row in rows:
        try:
            if row["topic"] in ("incident.created", "incident.status_changed"):
                sync_incident(str(row["tenant_id"]), row["payload"]["incident_id"])
            logger.info("outbox_event", extra={"topic": row["topic"], "outbox_id": row["id"]})
            with connect() as connection:
                connection.execute("UPDATE cm_outbox SET processed_at=now(),last_error=NULL WHERE id=%s", (row["id"],))
        except Exception as error:
            logger.exception("outbox_event_failed", extra={"topic": row["topic"], "outbox_id": row["id"]})
            with connect() as connection:
                connection.execute(
                    "UPDATE cm_outbox SET attempts=attempts+1,last_error=%s WHERE id=%s",
                    (str(error)[:1000], row["id"]),
                )
            if row["tenant_id"] and row["payload"].get("incident_id"):
                with tenant_connection(str(row["tenant_id"])) as connection:
                    connection.execute(
                        """UPDATE cm_incidents SET sync_status='failed',sync_error=%s
                        WHERE tenant_id=%s AND id=%s""",
                        (str(error)[:1000], row["tenant_id"], row["payload"]["incident_id"]),
                    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    migrate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker_started")
    while running:
        process_outbox()
        run_monitors()
        time.sleep(settings.monitor_interval_seconds)
    logger.info("worker_stopped")


if __name__ == "__main__":
    main()

