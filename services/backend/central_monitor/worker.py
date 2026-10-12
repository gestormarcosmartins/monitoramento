import hashlib
import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import connect, migrate, tenant_connection
from .glpi import GlpiClient
from .notifications import deliver_notifications
from .settings import get_settings
from .sla import add_sla_minutes
from .obligations import create_ticket_obligations, escalation_level
from .zabbix import ZabbixClient

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
        response = httpx.get(url, timeout=timeout, follow_redirects=False)
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
                INSERT INTO cm_incidents(tenant_id,alert_id,title,assignee_team_id,assignee_user_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,alert_id) DO NOTHING
                RETURNING id
                """,
                (tenant_id, alert["id"], alert["title"],monitor.get("responsible_team_id"),monitor.get("responsible_user_id")),
            ).fetchone()
            if incident:
                priority = {"info": "low", "warning": "medium", "average": "high", "high": "critical", "disaster": "critical"}.get(monitor["severity"], "medium")
                policy = connection.execute(
                    "SELECT * FROM cm_sla_policies WHERE tenant_id=%s AND priority=%s AND active",
                    (tenant_id,priority),
                ).fetchone()
                response_minutes = policy["response_minutes"] if policy else 120
                resolution_minutes = policy["resolution_minutes"] if policy else 1440
                tenant=connection.execute("SELECT timezone FROM cm_tenants WHERE id=%s",(tenant_id,)).fetchone()
                holidays={row["holiday_date"] for row in connection.execute("SELECT holiday_date FROM cm_holidays WHERE tenant_id=%s",(tenant_id,)).fetchall()}
                now=datetime.now(UTC)
                response_due=add_sla_minutes(now,response_minutes,policy or {"coverage":"24x7"},tenant["timezone"],holidays)
                resolution_due=add_sla_minutes(now,resolution_minutes,policy or {"coverage":"24x7"},tenant["timezone"],holidays)
                connection.execute(
                    """UPDATE cm_incidents SET priority=%s,description=%s,
                    response_due_at=%s,resolution_due_at=%s
                    WHERE tenant_id=%s AND id=%s""",
                    (priority,message,response_due,resolution_due,tenant_id,incident["id"]),
                )
                create_ticket_obligations(connection,tenant_id,str(incident["id"]),priority,response_due,resolution_due)
                connection.execute(
                    "INSERT INTO cm_incident_history(tenant_id,incident_id,event_type,details) VALUES (%s,%s,'incident.created',%s)",
                    (tenant_id,incident["id"],Jsonb({"source":"monitoring","priority":priority})),
                )
                connection.execute(
                    "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.created',%s)",
                    (tenant_id, Jsonb({"incident_id": str(incident["id"])})),
                )
                if monitor.get("notify_on_failure", True):
                    connection.execute(
                        "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'notification.requested',%s)",
                        (tenant_id,Jsonb({"incident_id":str(incident["id"]),"event":"incident.created"})),
                    )
        else:
            if not monitor.get("auto_resolve_incident", True):
                return
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
                        "UPDATE cm_incidents SET resolved_at=COALESCE(resolved_at,now()) WHERE tenant_id=%s AND id=%s",
                        (tenant_id,incident["id"]),
                    )
                    connection.execute(
                        "INSERT INTO cm_incident_history(tenant_id,incident_id,event_type,details) VALUES (%s,%s,'incident.resolved',%s)",
                        (tenant_id,incident["id"],Jsonb({"source":"monitoring"})),
                    )
                    connection.execute(
                        "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.status_changed',%s)",
                        (tenant_id, Jsonb({"incident_id": str(incident["id"]), "status": "resolved"})),
                    )
                    if monitor.get("notify_on_recovery", True):
                        connection.execute(
                            "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'notification.requested',%s)",
                            (tenant_id,Jsonb({"incident_id":str(incident["id"]),"event":"incident.resolved"})),
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
                    WHERE tenant_id=%s AND active AND monitor_type <> 'push'
                    AND NOT EXISTS (SELECT 1 FROM cm_maintenance_windows w
                        WHERE w.tenant_id=cm_monitors.tenant_id AND w.active
                        AND (w.asset_id IS NULL OR w.asset_id=cm_monitors.asset_id)
                        AND now() BETWEEN w.starts_at AND w.ends_at)
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
            """SELECT i.*,COALESCE(a.severity,CASE i.priority WHEN 'critical' THEN 'disaster' WHEN 'high' THEN 'high' WHEN 'medium' THEN 'average' ELSE 'warning' END) AS severity,
            COALESCE(a.message,i.description) AS message,t.name AS tenant_name
            FROM cm_incidents i LEFT JOIN cm_alerts a ON a.id=i.alert_id
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


def sync_incident_comment(tenant_id: str, incident_id: str, comment_id: str) -> None:
    with tenant_connection(tenant_id) as connection:
        row = connection.execute(
            """SELECT i.glpi_ticket_id,c.body,c.is_internal FROM cm_incident_comments c
            JOIN cm_incidents i ON i.id=c.incident_id WHERE c.tenant_id=%s AND c.incident_id=%s AND c.id=%s""",
            (tenant_id,incident_id,comment_id),
        ).fetchone()
    if row and row["glpi_ticket_id"]:
        GlpiClient().add_followup(int(row["glpi_ticket_id"]),row["body"],row["is_internal"])


GLPI_TO_NEXORA_STATUS={1:"new",2:"in_progress",3:"in_progress",4:"waiting_customer",5:"resolved",6:"closed"}


def reconcile_glpi_tickets() -> None:
    """Refresh GLPI-owned ticket state without overwriting pending local commands."""
    with connect() as connection:
        tenant_ids=[str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active")]
    client=GlpiClient()
    for tenant_id in tenant_ids:
        with tenant_connection(tenant_id) as connection:
            tickets=list(connection.execute("""SELECT id,glpi_ticket_id,status FROM cm_incidents
                WHERE tenant_id=%s AND glpi_ticket_id IS NOT NULL AND sync_status='synced'
                  AND inactive_at IS NULL ORDER BY synced_at NULLS FIRST LIMIT 100""",(tenant_id,)).fetchall())
        for ticket in tickets:
            try:
                remote=client.get_ticket(int(ticket["glpi_ticket_id"]))
                remote_status=GLPI_TO_NEXORA_STATUS.get(int(remote.get("status",0)))
                if not remote_status or remote_status==ticket["status"]:
                    continue
                with tenant_connection(tenant_id) as connection:
                    connection.execute("""UPDATE cm_incidents SET status=%s,updated_at=now(),
                        resolved_at=CASE WHEN %s='resolved' THEN COALESCE(resolved_at,now()) ELSE resolved_at END,
                        closed_at=CASE WHEN %s='closed' THEN COALESCE(closed_at,now()) ELSE closed_at END
                        WHERE tenant_id=%s AND id=%s AND sync_status='synced'""",
                        (remote_status,remote_status,remote_status,tenant_id,ticket["id"]))
                    connection.execute("""INSERT INTO cm_incident_history(tenant_id,incident_id,event_type,details)
                        VALUES (%s,%s,'glpi.status_reconciled',%s)""",
                        (tenant_id,ticket["id"],Jsonb({"from":ticket["status"],"to":remote_status,"glpi_ticket_id":ticket["glpi_ticket_id"]})))
            except Exception:
                logger.exception("glpi_ticket_reconciliation_failed",extra={"tenant_id":tenant_id,"incident_id":str(ticket["id"]),"glpi_ticket_id":ticket["glpi_ticket_id"]})


def sync_time_entry(tenant_id:str,entry_id:str)->None:
    with tenant_connection(tenant_id) as connection:
        row=connection.execute("""SELECT te.*,i.glpi_ticket_id FROM cm_time_entries te JOIN cm_incidents i ON i.id=te.ticket_id
            WHERE te.tenant_id=%s AND te.id=%s AND te.state='pending_glpi'""",(tenant_id,entry_id)).fetchone()
    if not row: return
    if not row["glpi_ticket_id"]: raise RuntimeError("Ticket is not confirmed by GLPI yet")
    try:
        glpi_task_id=GlpiClient().add_task(int(row["glpi_ticket_id"]),row["description"],row["minutes"])
    except Exception as error:
        with tenant_connection(tenant_id) as connection:
            connection.execute("UPDATE cm_time_entries SET state='rejected',rejection_reason=%s,updated_at=now() WHERE tenant_id=%s AND id=%s",(str(error)[:1000],tenant_id,entry_id))
        raise
    with tenant_connection(tenant_id) as connection:
        connection.execute("UPDATE cm_time_entries SET state='confirmed',glpi_task_id=%s,rejection_reason=NULL,updated_at=now() WHERE tenant_id=%s AND id=%s",(glpi_task_id,tenant_id,entry_id))


def provision_zabbix_monitor(tenant_id: str, monitor_id: str) -> None:
    with tenant_connection(tenant_id) as connection:
        monitor=connection.execute("""SELECT m.*,a.name AS asset_name FROM cm_monitors m JOIN cm_assets a ON a.id=m.asset_id
        WHERE m.tenant_id=%s AND m.id=%s""",(tenant_id,monitor_id)).fetchone()
    if not monitor or monitor["monitor_type"] != "http" or monitor["configuration"].get("zabbix_item_id"):
        return
    result=ZabbixClient().provision_http_monitor(str(monitor["asset_id"]),monitor["asset_name"],monitor_id,monitor["name"],monitor["configuration"]["url"])
    with tenant_connection(tenant_id) as connection:
        connection.execute("UPDATE cm_monitors SET configuration=configuration || %s WHERE tenant_id=%s AND id=%s",(Jsonb({"zabbix_host_id":result["host_id"],"zabbix_item_id":result["item_id"]}),tenant_id,monitor_id))


def execute_approved_action(tenant_id: str, execution_id: str) -> None:
    settings=get_settings()
    with tenant_connection(tenant_id) as connection:
        execution=connection.execute("""SELECT e.*,d.code,a.name AS asset_name FROM cm_action_executions e
        JOIN cm_action_definitions d ON d.id=e.action_id JOIN cm_assets a ON a.id=e.asset_id
        WHERE e.tenant_id=%s AND e.id=%s""",(tenant_id,execution_id)).fetchone()
        if not execution or execution["status"] not in ("validated","requested"): return
        connection.execute("UPDATE cm_action_executions SET status='running' WHERE tenant_id=%s AND id=%s",(tenant_id,execution_id))
        if execution["code"] == "collect_diagnostics":
            monitors=list(connection.execute("SELECT name,last_status,last_message,last_checked_at FROM cm_monitors WHERE tenant_id=%s AND asset_id=%s",(tenant_id,execution["asset_id"])).fetchall())
            result={"asset":execution["asset_name"],"monitors":monitors,"collected_at":datetime.now(UTC).isoformat()}
            final_status="succeeded"
        elif settings.action_execution_enabled:
            result={"message":"Action approved; remote agent adapter is required for this asset","executed":False}
            final_status="failed"
        else:
            result={"message":"Execution disabled by platform safety policy","executed":False}
            final_status="rejected"
        connection.execute("UPDATE cm_action_executions SET status=%s,result=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",(final_status,Jsonb(result),tenant_id,execution_id))
        connection.execute("INSERT INTO cm_audit_log(tenant_id,action,object_type,object_id,details) VALUES (%s,'action.finished','action_execution',%s,%s)",(tenant_id,execution_id,Jsonb({"status":final_status})))


def runbook_authorization_failure(requester_authorized:bool,approver_required:bool,approver_authorized:bool)->str|None:
    if not requester_authorized:return "requester_access_revoked"
    if approver_required and not approver_authorized:return "approver_access_revoked"
    return None


def execute_runbook(tenant_id:str,execution_id:str)->None:
    with tenant_connection(tenant_id) as connection:
        execution=connection.execute("""SELECT e.*,v.steps,v.postconditions,v.lock_key,v.approval_required,v.risk,r.name,
          EXISTS(SELECT 1 FROM cm_users u WHERE u.id=e.requested_by AND u.active AND (u.is_master OR EXISTS(
            SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=e.tenant_id AND g.user_id=u.id AND g.active
              AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now()) AND g.role IN ('admin','manager','operator')
          ))) AS requester_authorized,
          EXISTS(SELECT 1 FROM cm_users u WHERE u.id=e.approved_by AND u.active AND (u.is_master OR EXISTS(
            SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=e.tenant_id AND g.user_id=u.id AND g.active
              AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now()) AND g.role IN ('admin','manager')
          ))) AS approver_authorized
          FROM cm_runbook_executions e
          JOIN cm_runbook_versions v ON v.tenant_id=e.tenant_id AND v.runbook_id=e.runbook_id AND v.version=e.version
          JOIN cm_runbooks r ON r.tenant_id=e.tenant_id AND r.id=e.runbook_id
          WHERE e.tenant_id=%s AND e.id=%s FOR UPDATE""",(tenant_id,execution_id)).fetchone()
        if not execution or execution["status"]!="approved":return
        approval_required=execution["approval_required"] or execution["risk"] in ("high","critical")
        authorization_failure=runbook_authorization_failure(execution["requester_authorized"],approval_required,execution["approver_authorized"])
        if authorization_failure:
            connection.execute("UPDATE cm_runbook_executions SET status='rejected',result=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",(Jsonb({"reason":authorization_failure}),tenant_id,execution_id))
            connection.execute("INSERT INTO cm_audit_log(tenant_id,action,object_type,object_id,details) VALUES (%s,'runbook.authorization_rejected','runbook_execution',%s,%s)",(tenant_id,execution_id,Jsonb({"reason":authorization_failure})))
            return
        locked=connection.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked",(execution["lock_key"],)).fetchone()["locked"]
        if not locked:raise RuntimeError("Runbook target is locked by another execution")
        connection.execute("UPDATE cm_runbook_executions SET status='running',started_at=now() WHERE tenant_id=%s AND id=%s",(tenant_id,execution_id))
        results=[];final_status="succeeded"
        try:
            for step in execution["steps"]:
                kind=step["type"]
                if kind=="collect_diagnostics":
                    rows=list(connection.execute("SELECT name,last_status,last_message,last_checked_at FROM cm_monitors WHERE tenant_id=%s AND (%s::uuid IS NULL OR asset_id=%s::uuid)",(tenant_id,execution["target_asset_id"],execution["target_asset_id"])).fetchall())
                    results.append({"type":kind,"monitors":rows})
                elif kind=="wait":
                    seconds=min(max(float(step.get("seconds",1)),0),5);time.sleep(seconds);results.append({"type":kind,"seconds":seconds})
                elif kind in ("verify_monitor","http_request"):
                    monitor=connection.execute("SELECT * FROM cm_monitors WHERE tenant_id=%s AND id=%s",(tenant_id,step.get("monitor_id"))).fetchone()
                    if not monitor:raise RuntimeError("Referenced monitor was not found")
                    if kind=="http_request":
                        if monitor["monitor_type"]!="http":raise RuntimeError("Referenced monitor is not HTTP")
                        ok,message=check_http(monitor["configuration"])
                    else:ok,message=monitor["last_status"]=="up",monitor["last_message"]
                    results.append({"type":kind,"ok":ok,"message":message})
                    if not ok:raise RuntimeError("Runbook verification did not prove recovery")
                elif kind=="restart_service":
                    results.append({"type":kind,"message":"Remote adapter acknowledgement cannot prove recovery"});final_status="uncertain";break
            if final_status=="succeeded":
                for condition in execution["postconditions"]:
                    if condition.get("type")=="verify_monitor":
                        monitor=connection.execute("SELECT last_status FROM cm_monitors WHERE tenant_id=%s AND id=%s",(tenant_id,condition.get("monitor_id"))).fetchone()
                        if not monitor or monitor["last_status"]!="up":final_status="uncertain";results.append({"postcondition":condition,"ok":False})
        except (httpx.TimeoutException,TimeoutError) as error:
            final_status="uncertain";results.append({"error":str(error),"reason":"timeout_requires_reconciliation"})
        except Exception as error:
            final_status="failed";results.append({"error":str(error)})
        connection.execute("UPDATE cm_runbook_executions SET status=%s,result=%s,finished_at=now() WHERE tenant_id=%s AND id=%s",(final_status,Jsonb({"steps":results}),tenant_id,execution_id))
        connection.execute("INSERT INTO cm_audit_log(tenant_id,action,object_type,object_id,details) VALUES (%s,'runbook.finished','runbook_execution',%s,%s)",(tenant_id,execution_id,Jsonb({"status":final_status,"runbook":execution["name"]})))


def retry_delay_seconds(attempts: int) -> int:
    return min(900, 2 ** min(max(attempts, 1), 9))


def outbox_actor_authorized(connection:Any,tenant_id:str,requested_by:Any)->bool:
    if requested_by is None:return True
    return bool(connection.execute("""SELECT 1 FROM cm_users u WHERE u.id=%s AND u.active AND (u.is_master OR EXISTS(
        SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=%s AND g.user_id=u.id AND g.active
          AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())))""",(requested_by,tenant_id)).fetchone())


def process_outbox() -> None:
    worker_id=f"worker-{os.getpid()}"
    with connect() as connection:
        tenant_ids=[str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active ORDER BY id").fetchall()]
    rows=[]
    for tenant_id in tenant_ids:
        with tenant_connection(tenant_id) as connection:
            rows.extend(connection.execute(
                """WITH candidates AS (
                    SELECT id FROM cm_outbox
                    WHERE tenant_id=%s AND processed_at IS NULL AND attempts < 10 AND available_at<=now()
                      AND (state IN ('pending','retry_wait') OR (state='running' AND locked_at<now()-interval '10 minutes'))
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 25
                )
                UPDATE cm_outbox o SET state='running',locked_at=now(),worker_id=%s,state_reason=NULL
                FROM candidates c WHERE o.id=c.id
                RETURNING o.id,o.tenant_id,o.topic,o.payload,o.attempts,o.requested_by""",
                (tenant_id,worker_id),
            ).fetchall())
    for row in rows:
        attempt_number=row["attempts"]+1
        with tenant_connection(str(row["tenant_id"])) as connection:
            connection.execute("INSERT INTO cm_outbox_attempts(tenant_id,outbox_id,attempt,worker_id) VALUES (%s,%s,%s,%s) ON CONFLICT(outbox_id,attempt) DO NOTHING",(row["tenant_id"],row["id"],attempt_number,worker_id))
            if not outbox_actor_authorized(connection,str(row["tenant_id"]),row["requested_by"]):
                logger.warning("outbox_authorization_rejected",extra={"tenant_id":str(row["tenant_id"]),"outbox_id":row["id"],"topic":row["topic"],"requested_by":str(row["requested_by"])})
                connection.execute("UPDATE cm_outbox SET state='rejected',processed_at=now(),state_reason='requester_access_revoked',last_error='Authorization was revoked before execution',locked_at=NULL,worker_id=NULL WHERE id=%s",(row["id"],))
                connection.execute("UPDATE cm_outbox_attempts SET outcome='rejected',error='requester_access_revoked',finished_at=now() WHERE outbox_id=%s AND attempt=%s",(row["id"],attempt_number))
                connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'outbox.authorization_rejected','outbox',%s,%s)",(row["tenant_id"],row["requested_by"],str(row["id"]),Jsonb({"topic":row["topic"]})))
                continue
        try:
            if row["topic"] in ("incident.created", "incident.status_changed"):
                sync_incident(str(row["tenant_id"]), row["payload"]["incident_id"])
            elif row["topic"] == "incident.comment_created":
                sync_incident_comment(str(row["tenant_id"]),row["payload"]["incident_id"],row["payload"]["comment_id"])
            elif row["topic"] == "notification.requested":
                deliver_notifications(str(row["tenant_id"]),row["payload"]["incident_id"],row["payload"]["event"])
            elif row["topic"] == "monitor.created":
                provision_zabbix_monitor(str(row["tenant_id"]),row["payload"]["monitor_id"])
            elif row["topic"] == "action.validated":
                execute_approved_action(str(row["tenant_id"]),row["payload"]["execution_id"])
            elif row["topic"] == "time_entry.requested":
                sync_time_entry(str(row["tenant_id"]),row["payload"]["entry_id"])
            elif row["topic"] == "runbook.approved":
                execute_runbook(str(row["tenant_id"]),row["payload"]["execution_id"])
            logger.info("outbox_event", extra={"topic": row["topic"], "outbox_id": row["id"]})
            with tenant_connection(str(row["tenant_id"])) as connection:
                connection.execute("UPDATE cm_outbox SET state='succeeded',processed_at=now(),last_error=NULL,locked_at=NULL,worker_id=NULL WHERE id=%s", (row["id"],))
                connection.execute("UPDATE cm_outbox_attempts SET outcome='succeeded',finished_at=now() WHERE outbox_id=%s AND attempt=%s",(row["id"],attempt_number))
        except Exception as error:
            logger.exception("outbox_event_failed", extra={"topic": row["topic"], "outbox_id": row["id"]})
            with tenant_connection(str(row["tenant_id"])) as connection:
                next_attempt=row["attempts"]+1
                next_state="uncertain" if next_attempt>=10 else "retry_wait"
                if next_state=="uncertain":
                    logger.error("outbox_effect_uncertain",extra={"tenant_id":str(row["tenant_id"]),"outbox_id":row["id"],"topic":row["topic"],"attempts":next_attempt,"reconciliation_required":True})
                connection.execute(
                    """UPDATE cm_outbox SET attempts=%s,last_error=%s,state=%s,
                    state_reason=%s,available_at=now()+(%s * interval '1 second'),locked_at=NULL,worker_id=NULL WHERE id=%s""",
                    (next_attempt,str(error)[:1000],next_state,"attempts_exhausted_effect_requires_reconciliation" if next_state=="uncertain" else "transient_failure",retry_delay_seconds(next_attempt),row["id"]),
                )
                connection.execute("UPDATE cm_outbox_attempts SET outcome=%s,error=%s,finished_at=now() WHERE outbox_id=%s AND attempt=%s",(next_state,str(error)[:1000],row["id"],attempt_number))
            if row["topic"].startswith("incident.") and row["tenant_id"] and row["payload"].get("incident_id"):
                with tenant_connection(str(row["tenant_id"])) as connection:
                    connection.execute(
                        """UPDATE cm_incidents SET sync_status='failed',sync_error=%s
                        WHERE tenant_id=%s AND id=%s""",
                        (str(error)[:1000], row["tenant_id"], row["payload"]["incident_id"]),
                    )


def process_obligation_escalations() -> None:
    with connect() as connection:
        tenant_ids=[str(row["id"]) for row in connection.execute("SELECT id FROM cm_tenants WHERE active")]
    now=datetime.now(UTC)
    for tenant_id in tenant_ids:
        with tenant_connection(tenant_id) as connection:
            obligations=list(connection.execute("""SELECT o.*,i.title FROM cm_ticket_obligations o JOIN cm_incidents i ON i.id=o.ticket_id
                WHERE o.tenant_id=%s AND o.state='running' AND o.due_at<=now()+interval '1 hour'
                  AND i.status NOT IN ('resolved','closed','cancelled','duplicate','ignored')""",(tenant_id,)).fetchall())
            for obligation in obligations:
                level=escalation_level(obligation["due_at"],now)
                if not level: continue
                reason=f"{obligation['kind']} {'vencida' if level==3 else 'em risco'}"
                event=connection.execute("""INSERT INTO cm_escalation_events(tenant_id,ticket_id,obligation_id,level,reason)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,obligation_id,level) DO NOTHING RETURNING id""",
                    (tenant_id,obligation["ticket_id"],obligation["id"],level,reason)).fetchone()
                if level==3:
                    connection.execute("UPDATE cm_ticket_obligations SET state='breached',updated_at=now() WHERE tenant_id=%s AND id=%s",(tenant_id,obligation["id"]))
                if event:
                    connection.execute("""INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key)
                        VALUES (%s,'notification.requested',%s,%s) ON CONFLICT(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING""",
                        (tenant_id,Jsonb({"incident_id":str(obligation["ticket_id"]),"event":f"sla.{obligation['kind']}.level_{level}"}),f"sla:{obligation['id']}:{level}"))


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    migrate()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker_started")
    while running:
        process_outbox()
        reconcile_glpi_tickets()
        process_obligation_escalations()
        run_monitors()
        time.sleep(settings.monitor_interval_seconds)
    logger.info("worker_stopped")


if __name__ == "__main__":
    main()

