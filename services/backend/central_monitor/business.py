"""Independent Business Monitor API (first vertical slice BM-00..BM-05).

No implicit permissions from infrastructure roles, including technical Master.
All business queries/mutations use a concrete company and tenant_connection.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .agent_api import agent_identity
from .auth import Identity, current_identity, tenant_header
from .business_glpi_outbox import enqueue as enqueue_glpi
from .business_rules import evaluate, validate_thresholds
from .db import tenant_connection

router = APIRouter(prefix="/api/v1/business", tags=["business-monitor"])
agent_router = APIRouter(prefix="/api/v1/agent", tags=["business-agent"])
CAPABILITIES = (
    "business.view", "business.monitor.create", "business.monitor.update",
    "business.monitor.inactivate", "business.occurrence.manage",
    "business.source.manage", "business.grants.manage",
)


def _actor(identity: Identity, tenant_id: str) -> dict[str, Any]:
    # Lazy import avoids circular dependency: main includes this router.
    from .main import authorize_tenant
    return authorize_tenant(identity, tenant_id)


def _enabled(connection: Any, tenant_id: str) -> bool:
    row = connection.execute(
        "SELECT mode FROM cm_feature_flags WHERE tenant_id=%s AND feature='business_monitor'",
        (tenant_id,),
    ).fetchone()
    return bool(row and row["mode"] in ("enabled", "write_pilot"))


def _permissions(connection: Any, tenant_id: str, user_id: Any) -> dict[str, str]:
    return {row["capability"]: row["scope_type"] for row in connection.execute(
        "SELECT capability,scope_type FROM cm_business_grants WHERE tenant_id=%s AND user_id=%s AND active",
        (tenant_id, user_id),
    ).fetchall()}


def _require(identity: Identity, tenant_id: str, capability: str) -> tuple[dict[str, Any], str]:
    actor = _actor(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        if not _enabled(db, tenant_id):
            raise HTTPException(404, "Business Monitor not enabled for this company")
        grant = db.execute("""SELECT scope_type,scope_id FROM cm_business_grants
             WHERE tenant_id=%s AND user_id=%s AND capability=%s AND active""",
             (tenant_id,actor['id'],capability)).fetchone()
    if not grant:
        raise HTTPException(403, "Business Monitor permission denied")
    return {**actor,'business_scope_id':grant.get('scope_id')}, grant['scope_type']


def _require_grant_admin(db: Any, tenant_id: str, actor: dict[str, Any], capability: str | None = None) -> None:
    """Grant administration: product must be enabled; needs a company-scoped
    business.grants.manage grant. A technical Master has no implicit Business access;
    it may only bootstrap the first administrator (grants.manage, company scope)."""
    if not _enabled(db, tenant_id):
        raise HTTPException(404, "Business Monitor not enabled for this company")
    row = db.execute("""SELECT scope_type FROM cm_business_grants WHERE tenant_id=%s AND user_id=%s
        AND capability='business.grants.manage' AND active""", (tenant_id, actor["id"])).fetchone()
    if row and row["scope_type"] == "company":
        return
    if actor.get("is_master") and capability in (None, "business.grants.manage"):
        return
    raise HTTPException(403, "Company-wide grant administrator required")


def _scope_allows(row:dict[str,Any],actor:dict[str,Any],scope:str)->bool:
    if scope=='company':return True
    if scope=='own':return str(row.get('owner_user_id'))==str(actor['id'])
    field={'unit':'unit_id','department':'department_id','process':'process_id'}.get(scope)
    return bool(field and actor.get('business_scope_id') and
        str(row.get(field))==str(actor['business_scope_id']))


def _indicator_row(db: Any, tenant_id: str, indicator_id: UUID, actor: dict[str, Any], scope: str) -> dict[str, Any]:
    row = db.execute('SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s',
                     (tenant_id,indicator_id)).fetchone()
    if not row or not _scope_allows(row,actor,scope):
        raise HTTPException(404, 'Indicator not found')
    return row


def _allowed_indicator_ids(db:Any,tenant_id:str,actor:dict[str,Any],scope:str)->set[str]:
    # Filtering before pagination is required to avoid leaking totals or producing sparse pages.
    params=[tenant_id]
    filters={
        'company':('TRUE',[]),
        'own':('owner_user_id=%s',[actor['id']]),
        'unit':('unit_id=%s',[actor.get('business_scope_id')]),
        'department':('department_id=%s',[actor.get('business_scope_id')]),
        'process':('process_id=%s',[actor.get('business_scope_id')])
    }
    clause,values=filters.get(scope,('FALSE',[]))
    if scope in ('unit','department','process') and not actor.get('business_scope_id'):return set()
    return {str(r['id']) for r in db.execute(
        f'SELECT id FROM cm_business_indicators WHERE tenant_id=%s AND {clause}',
        (*params,*values)).fetchall()}


class IndicatorCreate(BaseModel):
    code: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_]+$")
    name: str = Field(min_length=3, max_length=180)
    description: str = Field(default="", max_length=3000)
    unit: str = Field(default="unidades", min_length=1, max_length=32)
    direction: Literal["above", "below"] = "above"
    warning_value: Decimal | None = None
    critical_value: Decimal | None = None
    stale_after_seconds: int = Field(default=1800, ge=30, le=2592000)

    @model_validator(mode="after")
    def check_thresholds(self):
        validate_thresholds(self.direction, self.warning_value, self.critical_value)
        return self


class IndicatorEdit(IndicatorCreate):
    version: int = Field(ge=1)
    active: bool = True


class GrantChange(BaseModel):
    user_id: UUID
    capability: Literal[
        "business.view", "business.monitor.create", "business.monitor.update",
        "business.monitor.inactivate", "business.occurrence.manage",
        "business.source.manage", "business.grants.manage"
    ]
    scope_type: Literal["company", "own"] = "company"
    active: bool = True
    reason: str = Field(min_length=4, max_length=500)


class EnableChange(BaseModel):
    enabled: bool
    reason: str = Field(min_length=4, max_length=500)


class BusinessSample(BaseModel):
    indicator_id: UUID
    event_id: str = Field(min_length=6, max_length=160)
    value: Decimal = Field(max_digits=22, decimal_places=6)
    collected_at: datetime
    collector_id: UUID | None = None
    config_revision: int | None = Field(default=None,ge=1)

    @model_validator(mode='after')
    def approved_collector_pair(self):
        if (self.collector_id is None) != (self.config_revision is None):
            raise ValueError('collector_id and config_revision must be provided together')
        return self

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: Decimal):
        if not value.is_finite():
            raise ValueError("value must be finite")
        return value

    @field_validator("collected_at")
    @classmethod
    def timezone_required(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must include timezone")
        return value


class OccurrenceAction(BaseModel):
    action: Literal["acknowledge", "close"]


@router.get("/me")
def my_business_access(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    actor = _actor(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        enabled = _enabled(db, tenant_id)
        permissions = _permissions(db, tenant_id, actor["id"]) if enabled else {}
    return {"enabled": enabled, "capabilities": sorted(permissions), "scopes": permissions}


@router.put("/enable")
def enable_business(payload: EnableChange, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _actor(identity, tenant_id)
    # Technical Master may administer licensing, but still cannot read Business data.
    if not actor.get("is_master"):
        raise HTTPException(403, "Technical Master required for product activation")
    with tenant_connection(tenant_id) as db:
        db.execute("""INSERT INTO cm_feature_flags(tenant_id,feature,mode,approved_by,reason)
            VALUES (%s,'business_monitor',%s,%s,%s)
            ON CONFLICT (tenant_id,feature) DO UPDATE SET mode=excluded.mode,approved_by=excluded.approved_by,
            reason=excluded.reason,updated_at=now()""",
            (tenant_id, "enabled" if payload.enabled else "off", actor["id"], payload.reason))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'set_feature','business_monitor',%s,%s)",
                   (tenant_id, actor["id"], tenant_id, Jsonb({"enabled": payload.enabled, "reason": payload.reason})))
    return {"enabled": payload.enabled}


@router.get("/grants")
def list_business_grants(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _actor(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        _require_grant_admin(db, tenant_id, actor)
        return list(db.execute("""SELECT g.user_id,u.display_name,u.email,g.capability,g.scope_type,g.active,g.granted_at
            FROM cm_business_grants g JOIN cm_users u ON u.id=g.user_id
            WHERE g.tenant_id=%s ORDER BY u.display_name,g.capability""", (tenant_id,)).fetchall())


@router.put("/grants")
def set_business_grant(payload: GrantChange, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _actor(identity, tenant_id)
    if str(actor["id"]) == str(payload.user_id):
        raise HTTPException(403, "Self-grant is prohibited")
    with tenant_connection(tenant_id) as db:
        _require_grant_admin(db, tenant_id, actor, payload.capability)
        if payload.scope_type != "company" and payload.capability == "business.grants.manage":
            raise HTTPException(422, "Grant administration requires company scope")
        user = db.execute("""SELECT u.id FROM cm_users u WHERE u.id=%s AND u.active AND
            (u.is_master OR EXISTS(SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=%s AND g.user_id=u.id
             AND g.active AND g.valid_from<=now() AND (g.valid_until IS NULL OR g.valid_until>now())))""",
            (payload.user_id, tenant_id)).fetchone()
        if not user:
            raise HTTPException(404, "User is not an authorized member of company")
        db.execute("""INSERT INTO cm_business_grants(tenant_id,user_id,capability,scope_type,active,granted_by,reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,user_id,capability) DO UPDATE SET
              scope_type=excluded.scope_type,active=excluded.active,granted_by=excluded.granted_by,
              reason=excluded.reason,granted_at=now()""",
            (tenant_id,payload.user_id,payload.capability,payload.scope_type,payload.active,actor["id"],payload.reason))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'business_grant','user',%s,%s)",
                   (tenant_id,actor["id"],str(payload.user_id),Jsonb(payload.model_dump(mode="json"))))
    return {"status": "saved"}


def _serialize_indicator(row: dict[str,Any]) -> dict[str,Any]:
    state = row.get("state") or "sem_dados"
    collected = row.get("last_collected_at")
    now = datetime.now(UTC)
    if not row["active"]:
        state = "suspenso"
    elif not collected or now - collected > timedelta(seconds=row["stale_after_seconds"]):
        state = "sem_dados"
    return {**row, "state": state}


@router.get("/indicators")
def list_indicators(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor,scope=_require(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        ids=_allowed_indicator_ids(db,tenant_id,actor,scope)
        if not ids:return []
        rows=db.execute("""SELECT i.*,s.state,s.last_value,s.last_collected_at,s.evaluated_at
            FROM cm_business_indicators i LEFT JOIN cm_business_states s
             ON s.tenant_id=i.tenant_id AND s.indicator_id=i.id
            WHERE i.tenant_id=%s AND i.id=ANY(%s::uuid[]) ORDER BY i.name LIMIT 500""",
            (tenant_id,list(ids))).fetchall()
    return [_serialize_indicator(x) for x in rows]


@router.post("/indicators",status_code=201)
def create_indicator(payload: IndicatorCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor, _ = _require(identity, tenant_id, "business.monitor.create")
    try:
        with tenant_connection(tenant_id) as db:
            row = db.execute("""INSERT INTO cm_business_indicators
             (tenant_id,code,name,description,unit,direction,warning_value,critical_value,stale_after_seconds,owner_user_id)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
             (tenant_id,payload.code,payload.name,payload.description,payload.unit,payload.direction,
              payload.warning_value,payload.critical_value,payload.stale_after_seconds,actor["id"])).fetchone()
            db.execute("INSERT INTO cm_business_states(tenant_id,indicator_id) VALUES (%s,%s)", (tenant_id,row["id"]))
            db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'create','business_indicator',%s)",
                       (tenant_id,actor["id"],str(row["id"])))
        return row
    except UniqueViolation as exc:
        raise HTTPException(409, "Indicator code already exists in this company") from exc


@router.put("/indicators/{indicator_id}")
def edit_indicator(indicator_id: UUID,payload: IndicatorEdit,tenant_id: str=Depends(tenant_header),identity: Identity=Depends(current_identity)):
    actor,scope = _require(identity,tenant_id,"business.monitor.update")
    with tenant_connection(tenant_id) as db:
        current=_indicator_row(db,tenant_id,indicator_id,actor,scope)
        if current['active'] != payload.active and 'business.monitor.inactivate' not in _permissions(db,tenant_id,actor['id']):
            raise HTTPException(403,'Inactivation requires a separate permission')
        row=db.execute("""UPDATE cm_business_indicators SET code=%s,name=%s,description=%s,unit=%s,direction=%s,
          warning_value=%s,critical_value=%s,stale_after_seconds=%s,active=%s,version=version+1,updated_at=now()
          WHERE tenant_id=%s AND id=%s AND version=%s RETURNING *""",
          (payload.code,payload.name,payload.description,payload.unit,payload.direction,payload.warning_value,
           payload.critical_value,payload.stale_after_seconds,payload.active,tenant_id,indicator_id,payload.version)).fetchone()
        if not row:
            raise HTTPException(409,"Indicator modified by another user; reload first")
        # Rule revision changed: never present a previous evaluation as current.
        db.execute("""UPDATE cm_business_states SET state='sem_dados',last_value=NULL,last_collected_at=NULL,
                evaluated_at=now() WHERE tenant_id=%s AND indicator_id=%s""",(tenant_id,indicator_id))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','business_indicator',%s)",
                   (tenant_id,actor["id"],str(indicator_id)))
    return row


@router.get("/health")
def business_health(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,scope=_require(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        ids=_allowed_indicator_ids(db,tenant_id,actor,scope)
        rows=db.execute("""SELECT i.active,i.stale_after_seconds,s.state,s.last_collected_at
             FROM cm_business_indicators i LEFT JOIN cm_business_states s
             ON s.tenant_id=i.tenant_id AND s.indicator_id=i.id
             WHERE i.tenant_id=%s AND i.id=ANY(%s::uuid[])""",
             (tenant_id,list(ids))).fetchall() if ids else []
    count={k:0 for k in ('normal','atencao','critico','sem_dados','suspenso')}
    now=datetime.now(UTC)
    for row in rows:
        if not row['active']:state='suspenso'
        elif not row['last_collected_at'] or now-row['last_collected_at']>timedelta(seconds=row['stale_after_seconds']):
            state='sem_dados'
        else:state=row['state'] or 'sem_dados'
        count[state]+=1
    return {'tenant_id':tenant_id,'counts':count,'total':len(rows),'as_of':now,
            'scope':scope,'financial_exposure':None,'financial_exposure_reason':'Not verified'}


@router.get("/occurrences")
def list_occurrences(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,scope=_require(identity,tenant_id,'business.view')
    with tenant_connection(tenant_id) as db:
        ids=_allowed_indicator_ids(db,tenant_id,actor,scope)
        if not ids:return []
        return list(db.execute("""SELECT o.*,i.name AS indicator_name FROM cm_business_occurrences o
             JOIN cm_business_indicators i ON i.tenant_id=o.tenant_id AND i.id=o.indicator_id
             WHERE o.tenant_id=%s AND i.id=ANY(%s::uuid[])
             ORDER BY o.opened_at DESC LIMIT 300""",(tenant_id,list(ids))).fetchall())


@router.post("/occurrences/{occurrence_id}/actions")
def occurrence_action(occurrence_id:UUID,payload:OccurrenceAction,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor,scope=_require(identity,tenant_id,"business.occurrence.manage")
    with tenant_connection(tenant_id) as db:
        row=db.execute("""SELECT o.id,o.state,i.owner_user_id,i.unit_id,i.department_id,i.process_id
            FROM cm_business_occurrences o JOIN cm_business_indicators i
            ON i.id=o.indicator_id AND i.tenant_id=o.tenant_id
            WHERE o.tenant_id=%s AND o.id=%s FOR UPDATE OF o""",
            (tenant_id,occurrence_id)).fetchone()
        if not row or not _scope_allows(row,actor,scope): raise HTTPException(404,'Occurrence not found')
        if payload.action == "acknowledge":
            if row['state']!='open': raise HTTPException(409,'Only open occurrences can be acknowledged')
            new_state='acknowledged'
        else:
            if row['state'] != 'normalized':
                raise HTTPException(409,'Normalize the indicator before closing the occurrence')
            new_state='closed'
        db.execute("""UPDATE cm_business_occurrences SET state=%s,acknowledged_by=CASE WHEN %s='acknowledged' THEN %s ELSE acknowledged_by END,
           closed_at=CASE WHEN %s='closed' THEN now() ELSE closed_at END WHERE tenant_id=%s AND id=%s""",
           (new_state,new_state,actor['id'],new_state,tenant_id,occurrence_id))
        db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,%s,'business_occurrence',%s)",
                   (tenant_id,actor['id'],new_state,str(occurrence_id)))
        enqueue_glpi(db,tenant_id,occurrence_id,'acknowledged' if new_state=='acknowledged' else 'closed')
    return {"id":occurrence_id,"state":new_state}


@router.post("/agent-bindings", status_code=410)
def bind_agent() -> None:
    # Direct binding skipped the maker-checker publication flow (author cannot self-approve).
    # Superseded by POST /collectors + POST /collectors/{id}/publish in business_p1.py.
    raise HTTPException(410, "Use /collectors and publish a reviewed revision instead")


def _digest(sample:BusinessSample)->str:
    obj=sample.model_dump(mode='json')
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@agent_router.post("/business-events",status_code=202)
def agent_business_event(payload:BusinessSample,agent:dict[str,Any]=Depends(agent_identity)):
    tenant_id=str(agent['tenant_id']);agent_id=agent['id'];digest=_digest(payload)
    with tenant_connection(tenant_id) as db:
        if not _enabled(db,tenant_id): raise HTTPException(404,'Business Monitor disabled')
        binding=db.execute("""SELECT 1 FROM cm_business_agent_bindings WHERE tenant_id=%s AND agent_id=%s
                        AND indicator_id=%s AND active""",(tenant_id,agent_id,payload.indicator_id)).fetchone()
        if not binding: raise HTTPException(403,'Collector is not bound to indicator')
        if payload.collector_id is None:
            raise HTTPException(403,'Only published collector revisions may send Business events')
        published=db.execute("""SELECT 1 FROM cm_business_collector_defs c
            JOIN cm_business_collector_revisions r ON r.tenant_id=c.tenant_id AND r.collector_id=c.id
            WHERE c.tenant_id=%s AND c.id=%s AND c.agent_id=%s AND c.indicator_id=%s
              AND c.state='published' AND r.revision=%s""",
            (tenant_id,payload.collector_id,agent_id,payload.indicator_id,payload.config_revision)).fetchone()
        if not published:raise HTTPException(403,'Collector revision is not published for this HUB')
        indicator=db.execute("SELECT * FROM cm_business_indicators WHERE tenant_id=%s AND id=%s FOR UPDATE",
                             (tenant_id,payload.indicator_id)).fetchone()
        if not indicator or not indicator['active']:raise HTTPException(404,'Indicator unavailable')
        previous=db.execute("""SELECT payload_sha256 FROM cm_business_values WHERE tenant_id=%s
                   AND indicator_id=%s AND event_id=%s""",(tenant_id,payload.indicator_id,payload.event_id)).fetchone()
        if previous:
            if previous['payload_sha256']!=digest:raise HTTPException(409,'Event ID reused with different payload')
            return {'status':'duplicate','event_id':payload.event_id}
        now=datetime.now(UTC)
        # Reject future readings rather than allowing a false green state.
        if payload.collected_at > now + timedelta(minutes=5):
            raise HTTPException(422,'collected_at is unreasonably in the future')
        db.execute("""INSERT INTO cm_business_values(tenant_id,indicator_id,agent_id,event_id,payload_sha256,value,collected_at,collector_id,revision)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
             (tenant_id,payload.indicator_id,agent_id,payload.event_id,digest,payload.value,payload.collected_at,payload.collector_id,payload.config_revision))
        if payload.collector_id:
            db.execute("""INSERT INTO cm_business_collector_runs(tenant_id,collector_id,collected_at,status,records_count)
                VALUES(%s,%s,%s,'success',1)""",(tenant_id,payload.collector_id,payload.collected_at))
        state=db.execute("SELECT * FROM cm_business_states WHERE tenant_id=%s AND indicator_id=%s FOR UPDATE",
                         (tenant_id,payload.indicator_id)).fetchone()
        if state and state['last_collected_at'] and payload.collected_at <= state['last_collected_at']:
            return {'status':'accepted_out_of_order','event_id':payload.event_id}
        # The series and current sample are persisted in the SAME tenant transaction.
        # The centralized evaluator applies the published Business policy (when any).
        db.execute("""INSERT INTO cm_business_states(tenant_id,indicator_id,state,last_value,last_collected_at,evaluated_at)
             VALUES (%s,%s,'sem_dados',%s,%s,now()) ON CONFLICT (tenant_id,indicator_id) DO UPDATE SET
             last_value=excluded.last_value,last_collected_at=excluded.last_collected_at""",
             (tenant_id,payload.indicator_id,payload.value,payload.collected_at))
        from .business_evaluation import apply_evaluation
        status_now=apply_evaluation(db,tenant_id,indicator,now)
    return {'status':'accepted','event_id':payload.event_id,'indicator_state':status_now}
