import hashlib
import logging
import signal
import socket
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from .db import connect, migrate, tenant_connection
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
            connection.execute(
                """
                INSERT INTO cm_incidents(tenant_id,alert_id,title)
                VALUES (%s,%s,%s) ON CONFLICT (tenant_id,alert_id) DO NOTHING
                """,
                (tenant_id, alert["id"], alert["title"]),
            )
        else:
            alerts = connection.execute(
                """UPDATE cm_alerts SET status='resolved',resolved_at=now()
                WHERE tenant_id=%s AND fingerprint=%s AND status='open' RETURNING id""",
                (tenant_id, alert_fingerprint),
            ).fetchall()
            for alert in alerts:
                connection.execute(
                    """UPDATE cm_incidents SET status='resolved',version=version+1,updated_at=now()
                    WHERE tenant_id=%s AND alert_id=%s AND status NOT IN ('resolved','ignored')""",
                    (tenant_id, alert["id"]),
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


def process_outbox() -> None:
    with connect() as connection:
        rows = list(
            connection.execute(
                """SELECT id,topic FROM cm_outbox WHERE processed_at IS NULL
                AND topic <> 'action.validated'
                ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 100"""
            ).fetchall()
        )
        for row in rows:
            logger.info("outbox_event", extra={"topic": row["topic"], "outbox_id": row["id"]})
            connection.execute("UPDATE cm_outbox SET processed_at=now() WHERE id=%s", (row["id"],))


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

