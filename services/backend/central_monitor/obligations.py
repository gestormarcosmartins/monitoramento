from datetime import UTC, datetime, timedelta
from typing import Any


def create_ticket_obligations(connection:Any,tenant_id:str,ticket_id:str,priority:str="medium",response_due_at=None,resolution_due_at=None,supplier_due_at=None)->None:
    now=datetime.now(UTC)
    policy=connection.execute("""SELECT pv.* FROM cm_sla_policies p LEFT JOIN LATERAL (
        SELECT * FROM cm_sla_policy_versions v WHERE v.tenant_id=p.tenant_id AND v.policy_id=p.id
          AND v.state='published' AND v.effective_from<=now() AND (v.effective_until IS NULL OR v.effective_until>now())
        ORDER BY v.version DESC LIMIT 1) pv ON true
        WHERE p.tenant_id=%s AND p.priority=%s AND p.active""",(tenant_id,priority)).fetchone()
    response_minutes=(policy or {}).get("response_minutes") or 120
    resolution_minutes=(policy or {}).get("resolution_minutes") or 1440
    response_due=response_due_at or now+timedelta(minutes=response_minutes)
    resolution_due=resolution_due_at or now+timedelta(minutes=resolution_minutes)
    definitions=[("assignment_tto",now+timedelta(minutes=max(5,response_minutes//2))),("first_response",response_due),("resolution_ttr",resolution_due)]
    if supplier_due_at: definitions.append(("supplier_ola",supplier_due_at))
    for kind,due_at in definitions:
        connection.execute("""INSERT INTO cm_ticket_obligations(tenant_id,ticket_id,kind,due_at,policy_version_id)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,ticket_id,kind) DO NOTHING""",
            (tenant_id,ticket_id,kind,due_at,policy.get("id") if policy else None))


def escalation_level(due_at,now=None)->int|None:
    now=now or datetime.now(UTC)
    remaining=(due_at-now).total_seconds()
    if remaining<=0: return 3
    if remaining<=1800: return 2
    if remaining<=3600: return 1
    return None
