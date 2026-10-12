import csv
import hashlib
import io
import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize

router=APIRouter(prefix="/api/v1",tags=["commercial"])


def content_hash(value:Any)->str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,default=str).encode()).hexdigest()


def rounded_minutes(raw_minutes:int,rounding:int)->int:
    if raw_minutes==0:return 0
    sign=1 if raw_minutes>0 else -1
    return sign*int(math.ceil(abs(raw_minutes)/rounding)*rounding)


class ContractCreate(BaseModel):
    engagement_id:UUID
    code:str=Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,79}$")
    name:str=Field(min_length=2,max_length=200)
    starts_on:date
    ends_on:date|None=None


class ContractUpdate(ContractCreate):
    active:bool=True


class ContractVersionCreate(BaseModel):
    currency:str=Field(default="BRL",min_length=3,max_length=3)
    included_minutes:int=Field(default=0,ge=0)
    rounding_minutes:int=Field(default=1,ge=1,le=60)
    included_rate:Decimal|None=Field(default=None,ge=0)
    excess_rate:Decimal|None=Field(default=None,ge=0)
    internal_cost_rate:Decimal|None=Field(default=None,ge=0)
    coverage:dict[str,Any]=Field(default_factory=dict)
    targets:dict[str,Any]=Field(default_factory=dict)
    effective_from:date
    effective_until:date|None=None


class LedgerEntryCreate(BaseModel):
    contract_id:UUID
    time_entry_id:UUID|None=None
    ticket_id:UUID|None=None
    entry_type:Literal["effort","commercial_adjustment"]="effort"
    source_key:str=Field(min_length=4,max_length=200)
    competence:date
    occurred_at:datetime
    raw_minutes:int
    description:str=Field(min_length=3,max_length=2000)


class ReversalCreate(BaseModel):
    reason:str=Field(min_length=3,max_length=1000)
    source_key:str=Field(min_length=4,max_length=200)


class ConsumptionReviewCreate(BaseModel):
    proposed_minutes:int=Field(ge=0)
    reason:str=Field(min_length=3,max_length=1000)


class ConsumptionReviewDecision(BaseModel):
    decision:Literal["approved","rejected"]
    note:str|None=Field(default=None,max_length=1000)


class BillingReference(BaseModel):
    reference:str=Field(min_length=2,max_length=200)


class CsatCreate(BaseModel):
    score:int=Field(ge=1,le=5)
    comment:str|None=Field(default=None,max_length=3000)


def require_permission(actor:dict[str,Any],tenant_id:str,permission:str)->None:
    if actor["role"]=="master":return
    with tenant_connection(tenant_id) as c:
        allowed=c.execute("SELECT 1 FROM cm_role_permissions WHERE role=%s AND permission_code=%s",(actor["role"],permission)).fetchone()
    if not allowed:raise HTTPException(403,f"Permission {permission} required")


def commercial_delta(technical_minutes:int,proposed_minutes:int)->int:
    """Return the append-only commercial correction without changing technical effort."""
    return proposed_minutes-technical_minutes


def commercial_allocation_delta(original_included:int,original_excess:int,delta:int,remaining_included:int)->tuple[int,int]:
    """Allocate an append-only correction without rewriting the original franchise split."""
    if delta>=0:
        included_delta=min(delta,max(0,remaining_included))
        return included_delta,delta-included_delta
    reduction=-delta
    excess_reduction=min(reduction,max(0,original_excess))
    included_reduction=reduction-excess_reduction
    return -included_reduction,-excess_reduction


def referenced_source_key(review_id:str)->str:
    return f"commercial-review:{review_id}"


def billing_external_key(contract_id:str,competence:date,revision:int)->str:
    base=f"NEXORA-{contract_id}-{competence:%Y%m}"
    return base if revision==1 else f"{base}-R{revision}"


@router.get("/contracts")
def contracts(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id,("admin","manager","auditor"));require_permission(actor,tenant_id,"contract.read")
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT c.*,e.name AS engagement_name,e.kind AS engagement_kind,v.id AS version_id,v.version,v.currency,v.included_minutes,v.excess_rate,v.state AS version_state
            FROM cm_contracts c JOIN cm_service_engagements e ON e.id=c.engagement_id
            LEFT JOIN cm_contract_versions v ON v.tenant_id=c.tenant_id AND v.contract_id=c.id AND v.version=c.active_version
            WHERE c.tenant_id=%s ORDER BY c.name""",(tenant_id,)).fetchall())


@router.post("/contracts",status_code=201)
def create_contract(payload:ContractCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",));
    if payload.ends_on and payload.ends_on<payload.starts_on:raise HTTPException(422,"ends_on must be after starts_on")
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_contracts(tenant_id,engagement_id,code,name,starts_on,ends_on,created_by)
            SELECT %s,e.id,%s,%s,%s,%s,%s FROM cm_service_engagements e WHERE e.tenant_id=%s AND e.id=%s AND e.active RETURNING *""",
            (tenant_id,payload.code,payload.name,payload.starts_on,payload.ends_on,actor["id"],tenant_id,payload.engagement_id)).fetchone()
        if not row:raise HTTPException(422,"Active service engagement not found")
        return row


@router.put("/contracts/{contract_id}")
def update_contract(contract_id:str,payload:ContractUpdate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    if payload.ends_on and payload.ends_on<payload.starts_on:raise HTTPException(422,"ends_on must be after starts_on")
    with tenant_connection(tenant_id) as c:
        row=c.execute("""UPDATE cm_contracts contract SET engagement_id=engagement.id,code=%s,name=%s,starts_on=%s,ends_on=%s,
            status=CASE WHEN %s THEN CASE WHEN contract.active_version IS NULL THEN 'draft' ELSE 'active' END ELSE 'inactive' END,updated_at=now()
            FROM cm_service_engagements engagement
            WHERE contract.tenant_id=%s AND contract.id=%s AND engagement.tenant_id=contract.tenant_id AND engagement.id=%s
              AND (engagement.active OR engagement.id=contract.engagement_id)
            RETURNING contract.*""",(payload.code,payload.name,payload.starts_on,payload.ends_on,payload.active,tenant_id,contract_id,payload.engagement_id)).fetchone()
        if not row:raise HTTPException(404,"Contract or active service engagement not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'contract.updated','contract',%s,%s)",(tenant_id,actor["id"],contract_id,Jsonb({"active":payload.active})))
        return row


@router.post("/contracts/{contract_id}/versions",status_code=201)
def create_contract_version(contract_id:str,payload:ContractVersionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",));snapshot=payload.model_dump(mode="json");digest=content_hash(snapshot)
    if payload.effective_until and payload.effective_until<payload.effective_from:raise HTTPException(422,"effective_until must be after effective_from")
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_contract_versions(tenant_id,contract_id,version,currency,included_minutes,rounding_minutes,included_rate,excess_rate,internal_cost_rate,coverage,targets,effective_from,effective_until,content_hash,created_by)
            SELECT %s,id,COALESCE((SELECT max(version)+1 FROM cm_contract_versions WHERE tenant_id=%s AND contract_id=%s),1),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            FROM cm_contracts WHERE tenant_id=%s AND id=%s RETURNING *""",
            (tenant_id,tenant_id,contract_id,payload.currency.upper(),payload.included_minutes,payload.rounding_minutes,payload.included_rate,payload.excess_rate,payload.internal_cost_rate,Jsonb(payload.coverage),Jsonb(payload.targets),payload.effective_from,payload.effective_until,digest,actor["id"],tenant_id,contract_id)).fetchone()
        if not row:raise HTTPException(404,"Contract not found")
        return row


@router.post("/contracts/{contract_id}/versions/{version}/review")
def review_contract_version(contract_id:str,version:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_contract_versions SET state='review' WHERE tenant_id=%s AND contract_id=%s AND version=%s AND state='draft' RETURNING *",(tenant_id,contract_id,version)).fetchone()
        if not row:raise HTTPException(409,"Only draft contract versions can enter review")
        return row


@router.post("/contracts/{contract_id}/versions/{version}/publish")
def publish_contract_version(contract_id:str,version:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_contract_versions SET state='published',published_at=now() WHERE tenant_id=%s AND contract_id=%s AND version=%s AND state='review' RETURNING *",(tenant_id,contract_id,version)).fetchone()
        if not row:raise HTTPException(409,"Only reviewed contract versions can be published")
        c.execute("UPDATE cm_contracts SET active_version=%s,status='active',updated_at=now() WHERE tenant_id=%s AND id=%s",(version,tenant_id,contract_id))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'contract.publish','contract',%s,%s)",(tenant_id,actor["id"],contract_id,Jsonb({"version":version,"hash":row["content_hash"]})))
        return row


def append_ledger(c:Any,tenant_id:str,actor_id:str,payload:LedgerEntryCreate)->dict[str,Any]:
    version=c.execute("""SELECT v.* FROM cm_contracts ct JOIN cm_contract_versions v ON v.tenant_id=ct.tenant_id AND v.contract_id=ct.id AND v.version=ct.active_version
        WHERE ct.tenant_id=%s AND ct.id=%s AND ct.status='active' AND v.state='published' AND %s BETWEEN v.effective_from AND COALESCE(v.effective_until,'9999-12-31')""",
        (tenant_id,payload.contract_id,payload.competence)).fetchone()
    if not version:raise HTTPException(422,"No published contract version covers this competence")
    if payload.time_entry_id:
        time_entry=c.execute("SELECT ticket_id,minutes,state FROM cm_time_entries WHERE tenant_id=%s AND id=%s",(tenant_id,payload.time_entry_id)).fetchone()
        if not time_entry or time_entry["state"]!="confirmed":raise HTTPException(422,"Only GLPI-confirmed time entries can generate consumption")
        if payload.ticket_id and payload.ticket_id!=time_entry["ticket_id"]:raise HTTPException(422,"Time entry does not belong to ticket")
    billable=rounded_minutes(payload.raw_minutes,version["rounding_minutes"])
    consumed=c.execute("""SELECT COALESCE(sum(included_minutes),0) AS included FROM cm_consumption_ledger
        WHERE tenant_id=%s AND contract_id=%s AND competence=%s""",(tenant_id,payload.contract_id,payload.competence)).fetchone()["included"]
    remaining=max(0,version["included_minutes"]-consumed)
    included=max(0,min(billable,remaining)) if billable>0 else 0
    excess=billable-included
    amount=(Decimal(excess)*version["excess_rate"]/Decimal(60)).quantize(Decimal("0.01")) if version["excess_rate"] is not None else None
    cost=(Decimal(billable)*version["internal_cost_rate"]/Decimal(60)).quantize(Decimal("0.01")) if version["internal_cost_rate"] is not None else None
    try:
        return c.execute("""INSERT INTO cm_consumption_ledger(tenant_id,contract_id,contract_version_id,ticket_id,time_entry_id,entry_type,source_key,competence,occurred_at,raw_minutes,billable_minutes,included_minutes,excess_minutes,amount,cost_amount,description,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (tenant_id,payload.contract_id,version["id"],payload.ticket_id,payload.time_entry_id,payload.entry_type,payload.source_key,payload.competence,payload.occurred_at,payload.raw_minutes,billable,included,excess,amount,cost,payload.description,actor_id)).fetchone()
    except UniqueViolation:
        return c.execute("SELECT * FROM cm_consumption_ledger WHERE tenant_id=%s AND source_key=%s",(tenant_id,payload.source_key)).fetchone()


@router.get("/consumption")
def consumption(contract_id:UUID|None=None,competence:date|None=None,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","auditor"));require_permission(actor,tenant_id,"contract.read")
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT l.*,ct.name AS contract_name,i.title AS ticket_title,
            s.commercial_state,s.proposed_minutes,s.review_id,s.snapshot_hash AS review_snapshot_hash
            FROM cm_consumption_ledger l
            JOIN cm_contracts ct ON ct.id=l.contract_id LEFT JOIN cm_incidents i ON i.id=l.ticket_id
            LEFT JOIN cm_consumption_commercial_state s ON s.tenant_id=l.tenant_id AND s.ledger_entry_id=l.id
            WHERE l.tenant_id=%s AND (%s::uuid IS NULL OR l.contract_id=%s) AND (%s::date IS NULL OR l.competence=%s)
            ORDER BY l.occurred_at DESC""",(tenant_id,contract_id,contract_id,competence,competence)).fetchall())
    return {"items":rows,"data_as_of":datetime.now().astimezone(),"definition":"Ledger imutável; incluído e excedente conforme versão contratual vigente na competência."}


@router.get("/consumption/review-queue")
def consumption_review_queue(state:Literal["pending","approved","rejected","superseded"]|None=None,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","auditor"));require_permission(actor,tenant_id,"contract.read")
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT r.*,l.description,l.competence,l.contract_id,l.ticket_id,ct.name AS contract_name
            FROM cm_consumption_reviews r JOIN cm_consumption_ledger l ON l.tenant_id=r.tenant_id AND l.id=r.ledger_entry_id
            JOIN cm_contracts ct ON ct.tenant_id=l.tenant_id AND ct.id=l.contract_id
            WHERE r.tenant_id=%s AND (%s::text IS NULL OR r.state=%s) ORDER BY r.requested_at DESC""",(tenant_id,state,state)).fetchall())
    return {"items":rows,"data_as_of":datetime.now().astimezone(),"definition":"A revisão comercial é separada do esforço técnico e toda correção é lançada no ledger por referência."}


@router.post("/consumption/{entry_id}/reviews",status_code=201)
def request_consumption_review(entry_id:str,payload:ConsumptionReviewCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"));require_permission(actor,tenant_id,"consumption.review")
    with tenant_connection(tenant_id) as c:
        entry=c.execute("SELECT * FROM cm_consumption_ledger WHERE tenant_id=%s AND id=%s AND entry_type='effort'",(tenant_id,entry_id)).fetchone()
        if not entry:raise HTTPException(404,"Technical consumption entry not found")
        pending=c.execute("SELECT id FROM cm_consumption_reviews WHERE tenant_id=%s AND ledger_entry_id=%s AND state='pending'",(tenant_id,entry_id)).fetchone()
        if pending:raise HTTPException(409,"A pending commercial review already exists")
        revision=c.execute("SELECT COALESCE(max(revision),0)+1 AS value FROM cm_consumption_reviews WHERE tenant_id=%s AND ledger_entry_id=%s",(tenant_id,entry_id)).fetchone()["value"]
        snapshot={"ledger_entry_id":entry_id,"revision":revision,"technical_minutes":entry["raw_minutes"],"original_billable_minutes":entry["billable_minutes"],"proposed_minutes":payload.proposed_minutes,"reason":payload.reason}
        return c.execute("""INSERT INTO cm_consumption_reviews(tenant_id,ledger_entry_id,revision,technical_minutes,proposed_minutes,reason,requested_by,snapshot,snapshot_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(tenant_id,entry_id,revision,entry["raw_minutes"],payload.proposed_minutes,payload.reason,actor["id"],Jsonb(snapshot),content_hash(snapshot))).fetchone()


@router.post("/consumption-reviews/{review_id}/decision")
def decide_consumption_review(review_id:str,payload:ConsumptionReviewDecision,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",));require_permission(actor,tenant_id,"consumption.review")
    with tenant_connection(tenant_id) as c:
        review=c.execute("""SELECT r.*,l.contract_id,l.contract_version_id,l.ticket_id,l.time_entry_id,l.competence,l.billable_minutes,l.included_minutes,l.excess_minutes,l.amount,l.cost_amount,l.description
            FROM cm_consumption_reviews r JOIN cm_consumption_ledger l ON l.tenant_id=r.tenant_id AND l.id=r.ledger_entry_id
            WHERE r.tenant_id=%s AND r.id=%s AND r.state='pending' FOR UPDATE""",(tenant_id,review_id)).fetchone()
        if not review:raise HTTPException(409,"Commercial review must be pending")
        adjustment_id=None
        if payload.decision=="approved":
            delta=commercial_delta(review["billable_minutes"],review["proposed_minutes"])
            if delta:
                version=c.execute("SELECT * FROM cm_contract_versions WHERE tenant_id=%s AND id=%s",(tenant_id,review["contract_version_id"])).fetchone()
                consumed_included=c.execute("SELECT COALESCE(sum(included_minutes),0) AS value FROM cm_consumption_ledger WHERE tenant_id=%s AND contract_id=%s AND competence=%s",(tenant_id,review["contract_id"],review["competence"])).fetchone()["value"]
                included_delta,excess_delta=commercial_allocation_delta(review["included_minutes"],review["excess_minutes"],delta,version["included_minutes"]-consumed_included)
                amount=(Decimal(excess_delta)*version["excess_rate"]/Decimal(60)).quantize(Decimal("0.01")) if version["excess_rate"] is not None else None
                cost=(Decimal(delta)*version["internal_cost_rate"]/Decimal(60)).quantize(Decimal("0.01")) if version["internal_cost_rate"] is not None else None
                adjustment=c.execute("""INSERT INTO cm_consumption_ledger(tenant_id,contract_id,contract_version_id,ticket_id,time_entry_id,entry_type,source_key,original_entry_id,competence,occurred_at,raw_minutes,billable_minutes,included_minutes,excess_minutes,amount,cost_amount,description,created_by)
                    VALUES (%s,%s,%s,%s,%s,'commercial_adjustment',%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key) DO NOTHING RETURNING id""",
                    (tenant_id,review["contract_id"],review["contract_version_id"],review["ticket_id"],review["time_entry_id"],referenced_source_key(review_id),review["ledger_entry_id"],review["competence"],delta,delta,included_delta,excess_delta,amount,cost,f"Ajuste comercial: {review['reason']}",actor["id"])).fetchone()
                adjustment_id=adjustment["id"] if adjustment else c.execute("SELECT id FROM cm_consumption_ledger WHERE tenant_id=%s AND source_key=%s",(tenant_id,referenced_source_key(review_id))).fetchone()["id"]
        row=c.execute("""UPDATE cm_consumption_reviews SET state=%s,decided_by=%s,decided_at=now(),decision_note=%s,adjustment_entry_id=%s
            WHERE tenant_id=%s AND id=%s AND state='pending' RETURNING *""",(payload.decision,actor["id"],payload.note,adjustment_id,tenant_id,review_id)).fetchone()
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'consumption.review.decision','consumption_review',%s,%s)",(tenant_id,actor["id"],review_id,Jsonb({"decision":payload.decision,"adjustment_entry_id":str(adjustment_id) if adjustment_id else None,"snapshot_hash":review["snapshot_hash"]})))
        return row


@router.post("/consumption",status_code=201)
def create_consumption(payload:LedgerEntryCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:return append_ledger(c,tenant_id,str(actor["id"]),payload)


@router.post("/consumption/{entry_id}/reverse",status_code=201)
def reverse_consumption(entry_id:str,payload:ReversalCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        original=c.execute("SELECT * FROM cm_consumption_ledger WHERE tenant_id=%s AND id=%s",(tenant_id,entry_id)).fetchone()
        if not original:raise HTTPException(404,"Ledger entry not found")
        try:
            return c.execute("""INSERT INTO cm_consumption_ledger(tenant_id,contract_id,contract_version_id,ticket_id,time_entry_id,entry_type,source_key,original_entry_id,competence,occurred_at,raw_minutes,billable_minutes,included_minutes,excess_minutes,amount,cost_amount,description,created_by)
                VALUES (%s,%s,%s,%s,%s,'reversal',%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (tenant_id,original["contract_id"],original["contract_version_id"],original["ticket_id"],original["time_entry_id"],payload.source_key,original["id"],original["competence"],-original["raw_minutes"],-original["billable_minutes"],-original["included_minutes"],-original["excess_minutes"],-original["amount"] if original["amount"] is not None else None,-original["cost_amount"] if original["cost_amount"] is not None else None,payload.reason,actor["id"])).fetchone()
        except UniqueViolation:
            return c.execute("SELECT * FROM cm_consumption_ledger WHERE tenant_id=%s AND source_key=%s",(tenant_id,payload.source_key)).fetchone()


@router.get("/billing-cycles")
def billing_cycles(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id,("admin","manager","auditor"));require_permission(actor,tenant_id,"contract.read")
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT b.*,ct.name AS contract_name FROM cm_billing_cycles b JOIN cm_contracts ct ON ct.id=b.contract_id WHERE b.tenant_id=%s ORDER BY b.competence DESC,b.revision DESC",(tenant_id,)).fetchall())


@router.post("/billing-cycles/{contract_id}/{competence}",status_code=201)
def build_billing_cycle(contract_id:str,competence:date,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        existing=c.execute("SELECT * FROM cm_billing_cycles WHERE tenant_id=%s AND contract_id=%s AND competence=%s ORDER BY revision DESC LIMIT 1",(tenant_id,contract_id,competence)).fetchone()
        ledger=list(c.execute("""SELECT l.* FROM cm_consumption_ledger l
            WHERE l.tenant_id=%s AND l.contract_id=%s AND l.competence=%s
              AND NOT EXISTS (
                SELECT 1 FROM cm_consumption_reviews r WHERE r.tenant_id=l.tenant_id AND r.ledger_entry_id=l.id
                  AND r.revision=(SELECT max(r2.revision) FROM cm_consumption_reviews r2 WHERE r2.tenant_id=r.tenant_id AND r2.ledger_entry_id=r.ledger_entry_id)
                  AND r.state='rejected'
              )
              AND (%s::boolean OR NOT EXISTS (
                SELECT 1 FROM cm_billing_lines bl JOIN cm_billing_cycles prior ON prior.id=bl.cycle_id AND prior.tenant_id=bl.tenant_id
                WHERE bl.tenant_id=l.tenant_id AND bl.ledger_entry_id=l.id AND prior.state<>'cancelled'
              ))
            ORDER BY l.occurred_at,l.id""",(tenant_id,contract_id,competence,not bool(existing) or bool(existing and existing["state"]=="draft"))).fetchall())
        if existing and existing["state"]!="draft" and not ledger:return existing
        version=c.execute("SELECT * FROM cm_contract_versions WHERE tenant_id=%s AND contract_id=%s AND state='published' AND %s BETWEEN effective_from AND COALESCE(effective_until,'9999-12-31') ORDER BY version DESC LIMIT 1",(tenant_id,contract_id,competence)).fetchone()
        if not version:raise HTTPException(422,"Published contract version not found")
        review_hashes=list(c.execute("""SELECT snapshot_hash FROM cm_consumption_reviews r JOIN cm_consumption_ledger l ON l.id=r.ledger_entry_id AND l.tenant_id=r.tenant_id
            WHERE r.tenant_id=%s AND l.contract_id=%s AND l.competence=%s AND r.state='approved' ORDER BY r.id""",(tenant_id,contract_id,competence)).fetchall())
        revision=existing["revision"] if existing and existing["state"]=="draft" else (existing["revision"]+1 if existing else 1)
        snapshot={"contract_id":contract_id,"contract_version":version["version"],"competence":str(competence),"revision":revision,"ledger":[str(row["id"]) for row in ledger],"commercial_reviews":[row["snapshot_hash"] for row in review_hashes]};digest=content_hash(snapshot)
        totals={key:sum((row[key] or 0 for row in ledger),0) for key in ("billable_minutes","included_minutes","excess_minutes","amount","cost_amount")}
        external_key=billing_external_key(contract_id,competence,revision)
        if existing and existing["state"]=="draft":
            cycle=c.execute("""UPDATE cm_billing_cycles SET currency=%s,total_minutes=%s,included_minutes=%s,excess_minutes=%s,total_amount=%s,total_cost=%s,snapshot=%s,snapshot_hash=%s,updated_at=now()
                WHERE tenant_id=%s AND id=%s RETURNING *""",(version["currency"],totals["billable_minutes"],totals["included_minutes"],totals["excess_minutes"],totals["amount"] if any(row["amount"] is not None for row in ledger) else None,totals["cost_amount"] if any(row["cost_amount"] is not None for row in ledger) else None,Jsonb(snapshot),digest,tenant_id,existing["id"])).fetchone()
            c.execute("DELETE FROM cm_billing_lines WHERE tenant_id=%s AND cycle_id=%s",(tenant_id,cycle["id"]))
        else:
            cycle=c.execute("""INSERT INTO cm_billing_cycles(tenant_id,contract_id,competence,revision,external_key,currency,total_minutes,included_minutes,excess_minutes,total_amount,total_cost,snapshot,snapshot_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(tenant_id,contract_id,competence,revision,external_key,version["currency"],totals["billable_minutes"],totals["included_minutes"],totals["excess_minutes"],totals["amount"] if any(row["amount"] is not None for row in ledger) else None,totals["cost_amount"] if any(row["cost_amount"] is not None for row in ledger) else None,Jsonb(snapshot),digest)).fetchone()
        for row in ledger:c.execute("INSERT INTO cm_billing_lines(tenant_id,cycle_id,ledger_entry_id,description,billable_minutes,included_minutes,excess_minutes,amount) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(tenant_id,cycle["id"],row["id"],row["description"],row["billable_minutes"],row["included_minutes"],row["excess_minutes"],row["amount"]))
        return cycle


def transition_cycle(c:Any,tenant_id:str,cycle_id:str,source:str,target:str,extra:str="",params:tuple=())->dict[str,Any]:
    row=c.execute(f"UPDATE cm_billing_cycles SET state=%s,updated_at=now(){extra} WHERE tenant_id=%s AND id=%s AND state=%s RETURNING *",(target,*params,tenant_id,cycle_id,source)).fetchone()
    if not row:raise HTTPException(409,f"Billing cycle must be {source}")
    return row


@router.post("/billing-cycles/{cycle_id}/review")
def review_cycle(cycle_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:return transition_cycle(c,tenant_id,cycle_id,"draft","review")


@router.post("/billing-cycles/{cycle_id}/approve")
def approve_cycle(cycle_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",));require_permission(actor,tenant_id,"billing.approve")
    with tenant_connection(tenant_id) as c:
        current=c.execute("SELECT snapshot_hash FROM cm_billing_cycles WHERE tenant_id=%s AND id=%s AND state='review'",(tenant_id,cycle_id)).fetchone()
        if not current:raise HTTPException(409,"Billing cycle must be review")
        return transition_cycle(c,tenant_id,cycle_id,"review","approved",",approved_snapshot_hash=%s,approved_by=%s,approved_at=now()",(current["snapshot_hash"],actor["id"]))


@router.post("/billing-cycles/{cycle_id}/export")
def export_cycle(cycle_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->Response:
    actor=authorize(identity,tenant_id,("admin",));require_permission(actor,tenant_id,"billing.export")
    with tenant_connection(tenant_id) as c:
        cycle=transition_cycle(c,tenant_id,cycle_id,"approved","exported",",exported_at=now()")
        lines=list(c.execute("SELECT * FROM cm_billing_lines WHERE tenant_id=%s AND cycle_id=%s ORDER BY created_at",(tenant_id,cycle_id)).fetchall())
    output=io.StringIO();writer=csv.writer(output,delimiter=";");writer.writerow(["external_key","competence","description","minutes","included","excess","amount","currency"])
    for line in lines:writer.writerow([cycle["external_key"],cycle["competence"],line["description"],line["billable_minutes"],line["included_minutes"],line["excess_minutes"],line["amount"],cycle["currency"]])
    return Response("\ufeff"+output.getvalue(),media_type="text/csv;charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{cycle["external_key"]}.csv"'})


@router.post("/billing-cycles/{cycle_id}/erp-confirm")
def confirm_erp(cycle_id:str,payload:BillingReference,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    from .monitoring import channel_ready
    with tenant_connection(tenant_id) as c:
        current=c.execute("SELECT * FROM cm_billing_cycles WHERE tenant_id=%s AND id=%s",(tenant_id,cycle_id)).fetchone()
        if current and current["state"] in ("erp_confirmed","received") and current["erp_reference"]==payload.reference:return current
        capability=c.execute("SELECT * FROM cm_channel_capabilities WHERE tenant_id=%s AND channel='erp'",(tenant_id,)).fetchone()
        if not channel_ready(capability,"erp"):raise HTTPException(409,"ERP bloqueado: homologue o contrato, a identidade e o retorno confiável do provedor")
        return transition_cycle(c,tenant_id,cycle_id,"exported","erp_confirmed",",erp_reference=%s,erp_confirmed_at=now()",(payload.reference,))


@router.post("/billing-cycles/{cycle_id}/receive")
def receive_cycle(cycle_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:return transition_cycle(c,tenant_id,cycle_id,"erp_confirmed","received",",received_at=now()")


@router.get("/quality")
def quality(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with tenant_connection(tenant_id) as c:
        metrics=c.execute("""SELECT count(*) AS sample,
            round(100.0*count(*) FILTER(WHERE first_response_at<=response_due_at)/NULLIF(count(*) FILTER(WHERE first_response_at IS NOT NULL AND response_due_at IS NOT NULL),0),2) AS first_response_sla_pct,
            round(100.0*count(*) FILTER(WHERE resolved_at<=resolution_due_at)/NULLIF(count(*) FILTER(WHERE resolved_at IS NOT NULL AND resolution_due_at IS NOT NULL),0),2) AS resolution_sla_pct,
            count(*) FILTER(WHERE status='in_progress' AND resolved_at IS NOT NULL) AS reopened,
            round(avg(EXTRACT(EPOCH FROM(resolved_at-created_at))/60) FILTER(WHERE resolved_at IS NOT NULL),2) AS resolution_minutes
            FROM cm_incidents WHERE tenant_id=%s AND inactive_at IS NULL""",(tenant_id,)).fetchone()
        csat=c.execute("SELECT count(*) AS sample,round(avg(score),2) AS average FROM cm_csat_responses WHERE tenant_id=%s",(tenant_id,)).fetchone()
    return {"metrics":metrics,"csat":csat,"source":"Nexora projections confirmed by operational events","cutoff":datetime.now().astimezone(),"definition":"SLA uses non-null eligible timestamps; missing bases remain null, never zero."}


@router.post("/tickets/{ticket_id}/csat",status_code=201)
def create_csat(ticket_id:str,payload:CsatCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_csat_responses(tenant_id,ticket_id,respondent_id,score,comment)
            SELECT %s,id,%s,%s,%s FROM cm_incidents WHERE tenant_id=%s AND id=%s AND requester_user_id=%s AND status IN ('resolved','closed')
            ON CONFLICT(tenant_id,ticket_id,respondent_id) DO UPDATE SET score=excluded.score,comment=excluded.comment,created_at=now() RETURNING *""",
            (tenant_id,actor["id"],payload.score,payload.comment,tenant_id,ticket_id,actor["id"])).fetchone()
        if not row:raise HTTPException(404,"Eligible resolved ticket not found")
        return row


@router.get("/profitability")
def profitability(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",));require_permission(actor,tenant_id,"profitability.read")
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT ct.id,ct.name,l.competence,sum(l.amount) AS revenue,sum(l.cost_amount) AS cost,
            CASE WHEN bool_and(l.amount IS NOT NULL) AND bool_and(l.cost_amount IS NOT NULL) THEN sum(l.amount)-sum(l.cost_amount) END AS margin,
            count(*) AS sample FROM cm_consumption_ledger l JOIN cm_contracts ct ON ct.id=l.contract_id
            WHERE l.tenant_id=%s GROUP BY ct.id,ct.name,l.competence ORDER BY l.competence DESC,ct.name""",(tenant_id,)).fetchall())
    return {"items":rows,"data_as_of":datetime.now().astimezone(),"definition":"Margem somente quando todas as linhas possuem receita e custo confiáveis."}
