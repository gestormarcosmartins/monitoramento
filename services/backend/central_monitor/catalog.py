import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize
from .obligations import create_ticket_obligations

router = APIRouter(prefix="/api/v1", tags=["catalog"])
FIELD_TYPES = {"text", "long_text", "number", "date", "select", "multi_select", "attachment"}
CONDITION_OPERATORS = {"equals", "not_equals", "contains", "in", "not_in", "is_empty", "is_not_empty"}


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def validate_form_schema(schema: dict[str,Any]) -> dict[str,Any]:
    fields=schema.get("fields",[])
    if not isinstance(fields,list): raise ValueError("form_schema.fields must be a list")
    seen:set[str]=set()
    for field in fields:
        if not isinstance(field,dict) or field.get("type") not in FIELD_TYPES: raise ValueError("unsupported form field type")
        code=field.get("code")
        if not code or code in seen: raise ValueError("field codes must be unique")
        seen.add(code)
        if field["type"] in ("select","multi_select") and not field.get("options"): raise ValueError("selection fields require options")
        condition=field.get("visible_when")
        if condition and (not isinstance(condition,dict) or condition.get("operator") not in CONDITION_OPERATORS):
            raise ValueError("unsupported declarative condition")
    return schema


def validate_answers(schema:dict[str,Any],answers:dict[str,Any])->None:
    definitions={field["code"]:field for field in schema.get("fields",[])}
    unknown=set(answers)-set(definitions)
    if unknown: raise HTTPException(422,f"Unknown form fields: {', '.join(sorted(unknown))}")
    missing=[code for code,field in definitions.items() if field.get("required") and answers.get(code) in (None,"",[])]
    if missing: raise HTTPException(422,f"Required form fields: {', '.join(missing)}")
    for code,value in answers.items():
        field=definitions[code]; kind=field["type"]
        if kind=="number" and not isinstance(value,(int,float)): raise HTTPException(422,f"Field {code} must be numeric")
        if kind=="multi_select" and not isinstance(value,list): raise HTTPException(422,f"Field {code} must be a list")
        if kind in ("select","multi_select"):
            selected=value if isinstance(value,list) else [value]
            allowed={item["value"] if isinstance(item,dict) else item for item in field.get("options",[])}
            if any(item not in allowed for item in selected): raise HTTPException(422,f"Field {code} has an invalid option")


class CatalogServiceCreate(BaseModel):
    code:str=Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    name:str=Field(min_length=2,max_length=160)
    description:str=Field(min_length=3,max_length=5000)
    category:str=Field(default="Geral",max_length=120)


class CatalogVersionCreate(BaseModel):
    description:str=Field(min_length=3,max_length=5000)
    form_schema:dict[str,Any]=Field(default_factory=lambda:{"fields":[]})
    eligibility:dict[str,Any]=Field(default_factory=lambda:{"roles":["client","viewer","operator","manager","admin"]})
    routing:dict[str,Any]=Field(default_factory=dict)
    approval_policy:dict[str,Any]=Field(default_factory=lambda:{"required":False})
    coverage:dict[str,Any]=Field(default_factory=dict)
    sla_policy_id:UUID|None=None

    @field_validator("form_schema")
    @classmethod
    def valid_schema(cls,value:dict[str,Any])->dict[str,Any]: return validate_form_schema(value)


class ServiceRequestCreate(BaseModel):
    answers:dict[str,Any]=Field(default_factory=dict)
    title:str|None=Field(default=None,max_length=240)
    description:str|None=Field(default=None,max_length=20000)


class PortalEntryCreate(BaseModel):
    body:str=Field(min_length=1,max_length=10000)


class ApprovalDecision(BaseModel):
    decision:Literal["approved","rejected"]
    reason:str|None=Field(default=None,max_length=2000)


class AttachmentCreate(BaseModel):
    original_name:str=Field(min_length=1,max_length=255)
    declared_type:str|None=Field(default=None,max_length=200)
    content_base64:str


class AttachmentScan(BaseModel):
    status:Literal["clean","rejected"]
    details:str=Field(min_length=3,max_length=2000)
    engine:str=Field(min_length=2,max_length=120)
    reference:str=Field(min_length=3,max_length=500)
    signature_version:str|None=Field(default=None,max_length=120)


def eligible(version:dict[str,Any],role:str)->bool:
    rules=version.get("eligibility") or {}
    return (role=="master" or role in rules.get("roles",[])) and not rules.get("disabled",False)


@router.get("/catalog/services")
def catalog_services(include_drafts:bool=False,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id)
    if include_drafts and actor["role"] not in ("master","admin","manager"): raise HTTPException(403,"Catalog management permission required")
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT s.*,v.id AS version_id,v.version,v.form_schema,v.eligibility,v.routing,v.approval_policy,v.coverage,v.state AS version_state
            FROM cm_catalog_services s LEFT JOIN cm_catalog_service_versions v ON v.tenant_id=s.tenant_id AND v.service_id=s.id AND v.version=s.active_version
            WHERE s.tenant_id=%s AND (%s OR s.status='published') ORDER BY s.category,s.name""",(tenant_id,include_drafts)).fetchall())
    return rows if include_drafts else [row for row in rows if row["version_id"] and eligible(row,actor["role"])]


@router.post("/catalog/services",status_code=201)
def create_catalog_service(payload:CatalogServiceCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("INSERT INTO cm_catalog_services(tenant_id,code,name,description,category,created_by) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.code,payload.name,payload.description,payload.category,actor["id"])).fetchone()
        return row


@router.post("/catalog/services/{service_id}/versions",status_code=201)
def create_catalog_version(service_id:str,payload:CatalogVersionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager")); snapshot=payload.model_dump(mode="json"); digest=canonical_hash(snapshot)
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_catalog_service_versions(tenant_id,service_id,version,description,form_schema,eligibility,routing,approval_policy,coverage,sla_policy_id,content_hash,created_by)
            SELECT %s,id,COALESCE((SELECT max(version)+1 FROM cm_catalog_service_versions WHERE tenant_id=%s AND service_id=%s),1),%s,%s,%s,%s,%s,%s,%s,%s,%s
            FROM cm_catalog_services WHERE tenant_id=%s AND id=%s RETURNING *""",
            (tenant_id,tenant_id,service_id,payload.description,Jsonb(payload.form_schema),Jsonb(payload.eligibility),Jsonb(payload.routing),Jsonb(payload.approval_policy),Jsonb(payload.coverage),payload.sla_policy_id,digest,actor["id"],tenant_id,service_id)).fetchone()
        if not row: raise HTTPException(404,"Catalog service not found")
        return row


@router.post("/catalog/services/{service_id}/versions/{version}/review")
def review_catalog_version(service_id:str,version:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_catalog_service_versions SET state='review' WHERE tenant_id=%s AND service_id=%s AND version=%s AND state='draft' RETURNING *",(tenant_id,service_id,version)).fetchone()
        if not row: raise HTTPException(409,"Only draft versions can enter review")
        return row


@router.post("/catalog/services/{service_id}/versions/{version}/publish")
def publish_catalog_version(service_id:str,version:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_catalog_service_versions SET state='published',published_at=now() WHERE tenant_id=%s AND service_id=%s AND version=%s AND state='review' RETURNING *",(tenant_id,service_id,version)).fetchone()
        if not row: raise HTTPException(409,"Only reviewed versions can be published")
        c.execute("UPDATE cm_catalog_services SET active_version=%s,status='published',updated_at=now() WHERE tenant_id=%s AND id=%s",(version,tenant_id,service_id))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'catalog.publish','catalog_service',%s,%s)",(tenant_id,actor["id"],service_id,Jsonb({"version":version,"hash":row["content_hash"]})))
        return row


@router.get("/portal")
def portal_home(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    services=catalog_services(False,tenant_id,identity)
    with tenant_connection(tenant_id) as c:
        tickets=list(c.execute("""SELECT id,title,status,priority,ticket_type,glpi_ticket_id,sync_status,created_at,updated_at
            FROM cm_incidents WHERE tenant_id=%s AND requester_user_id=%s AND inactive_at IS NULL
            ORDER BY updated_at DESC LIMIT 10""",(tenant_id,actor["id"])).fetchall())
    public_services=[{key:row[key] for key in ("id","code","name","description","category","version_id","version","form_schema") } for row in services]
    return {"services":public_services,"recent_tickets":tickets,"data_as_of":datetime.now(UTC)}


@router.get("/portal/tickets")
def portal_tickets(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT id,title,status,priority,ticket_type,glpi_ticket_id,sync_status,created_at,updated_at
            FROM cm_incidents WHERE tenant_id=%s AND requester_user_id=%s AND inactive_at IS NULL ORDER BY updated_at DESC""",(tenant_id,actor["id"])).fetchall())


@router.get("/portal/tickets/{ticket_id}")
def portal_ticket(ticket_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        ticket=c.execute("""SELECT id,title,description,status,priority,ticket_type,glpi_ticket_id,sync_status,created_at,updated_at,resolved_at,closed_at
            FROM cm_incidents WHERE tenant_id=%s AND id=%s AND requester_user_id=%s AND inactive_at IS NULL""",(tenant_id,ticket_id,actor["id"])).fetchone()
        if not ticket: raise HTTPException(404,"Ticket not found")
        entries=list(c.execute("SELECT id,body,created_at FROM cm_incident_comments WHERE tenant_id=%s AND incident_id=%s AND NOT is_internal ORDER BY created_at",(tenant_id,ticket_id)).fetchall())
        solutions=list(c.execute("SELECT id,solution_type,description,evidence,status,created_at FROM cm_ticket_solutions WHERE tenant_id=%s AND incident_id=%s AND status IN ('proposed','accepted','rejected') ORDER BY created_at DESC",(tenant_id,ticket_id)).fetchall())
        attachments=list(c.execute("SELECT id,original_name,detected_type,size_bytes,sha256,created_at FROM cm_ticket_attachments WHERE tenant_id=%s AND ticket_id=%s AND scan_status='clean'",(tenant_id,ticket_id)).fetchall())
    return {"ticket":ticket,"entries":entries,"solutions":solutions,"attachments":attachments}


@router.post("/portal/services/{service_id}/request",status_code=201)
def request_service(service_id:str,payload:ServiceRequestCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","viewer","client"))
    with tenant_connection(tenant_id) as c:
        version=c.execute("""SELECT v.*,s.name,s.code FROM cm_catalog_services s JOIN cm_catalog_service_versions v
            ON v.tenant_id=s.tenant_id AND v.service_id=s.id AND v.version=s.active_version
            WHERE s.tenant_id=%s AND s.id=%s AND s.status='published' AND v.state='published'""",(tenant_id,service_id)).fetchone()
        if not version or not eligible(version,actor["role"]): raise HTTPException(404,"Eligible service not found")
        validate_answers(version["form_schema"],payload.answers)
        snapshot={"service":{"id":service_id,"code":version["code"],"name":version["name"],"version":version["version"]},"schema":version["form_schema"],"answers":payload.answers}
        digest=canonical_hash(snapshot)
        ticket=c.execute("""INSERT INTO cm_incidents(tenant_id,alert_id,status,title,description,priority,category,origin,ticket_type,requester_user_id,service_code,sync_status)
            VALUES (%s,NULL,'new',%s,%s,'medium','service_request','portal','request',%s,%s,'pending') RETURNING *""",
            (tenant_id,payload.title or version["name"],payload.description or version["description"],actor["id"],version["code"])).fetchone()
        submission=c.execute("""INSERT INTO cm_service_submissions(tenant_id,ticket_id,service_id,service_version_id,requester_id,answers,schema_snapshot,snapshot_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(tenant_id,ticket["id"],service_id,version["id"],actor["id"],Jsonb(payload.answers),Jsonb(snapshot),digest)).fetchone()
        create_ticket_obligations(c,tenant_id,str(ticket["id"]),"medium")
        if version["approval_policy"].get("required"):
            c.execute("INSERT INTO cm_ticket_approvals(tenant_id,ticket_id,submission_id,requested_by,snapshot_hash) VALUES (%s,%s,%s,%s,%s)",(tenant_id,ticket["id"],submission["id"],actor["id"],digest))
        c.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key,requested_by) VALUES (%s,'incident.created',%s,%s,%s)",(tenant_id,Jsonb({"incident_id":str(ticket["id"])}),f"incident.created:{ticket['id']}:1",actor["id"]))
        return {"ticket":ticket,"submission":{"id":submission["id"],"snapshot_hash":digest},"approval_required":bool(version["approval_policy"].get("required"))}


@router.post("/portal/tickets/{ticket_id}/entries",status_code=201)
def portal_entry(ticket_id:str,payload:PortalEntryCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_incident_comments(tenant_id,incident_id,author_id,body,is_internal)
            SELECT %s,id,%s,%s,false FROM cm_incidents WHERE tenant_id=%s AND id=%s AND requester_user_id=%s AND inactive_at IS NULL RETURNING id,body,created_at""",
            (tenant_id,actor["id"],payload.body,tenant_id,ticket_id,actor["id"])).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        c.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key,requested_by) VALUES (%s,'incident.comment_created',%s,%s,%s)",(tenant_id,Jsonb({"incident_id":ticket_id,"comment_id":str(row["id"])}),f"incident.comment_created:{ticket_id}:{row['id']}",actor["id"]))
        return row


@router.post("/approvals/{approval_id}/decision")
def approval_decision(approval_id:str,payload:ApprovalDecision,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        approval=c.execute("SELECT * FROM cm_ticket_approvals WHERE tenant_id=%s AND id=%s AND status='pending' FOR UPDATE",(tenant_id,approval_id)).fetchone()
        if not approval: raise HTTPException(404,"Pending approval not found")
        if approval["requested_by"]==actor["id"]: raise HTTPException(409,"Self-approval is not allowed")
        row=c.execute("UPDATE cm_ticket_approvals SET status=%s,approver_id=%s,reason=%s,decided_at=now() WHERE tenant_id=%s AND id=%s RETURNING *",(payload.decision,actor["id"],payload.reason,tenant_id,approval_id)).fetchone()
        if payload.decision=="rejected": c.execute("UPDATE cm_incidents SET status='cancelled',updated_at=now(),version=version+1 WHERE tenant_id=%s AND id=%s",(tenant_id,approval["ticket_id"]))
        return row


def detected_type(content:bytes)->str:
    if content.startswith(b"%PDF-"): return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if content.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if content.startswith(b"PK\x03\x04"): return "application/zip"
    if content.startswith(b"MZ"): return "application/x-msdownload"
    if content.startswith(b"\x7fELF"): return "application/x-executable"
    lowered=content[:512].lstrip().lower()
    if lowered.startswith((b"<!doctype html",b"<html",b"<script",b"<?php")): return "text/html"
    if lowered.startswith((b"#!/bin/sh",b"#!/bin/bash",b"#!/usr/bin/env")): return "text/x-shellscript"
    return "application/octet-stream"


FORBIDDEN_ATTACHMENT_TYPES={"application/x-msdownload","application/x-executable","text/html","text/x-shellscript"}


def safe_attachment_name(value:str)->str:
    cleaned="".join(ch for ch in value.replace("\\","/").split("/")[-1] if ch not in "\r\n\0\"").strip()
    return cleaned[:255] or "arquivo"


@router.post("/portal/tickets/{ticket_id}/attachments",status_code=201)
def upload_attachment(ticket_id:str,payload:AttachmentCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    try: content=base64.b64decode(payload.content_base64,validate=True)
    except ValueError as error: raise HTTPException(422,"Invalid base64 attachment") from error
    if not content or len(content)>10*1024*1024: raise HTTPException(413,"Attachment must be between 1 byte and 10 MiB")
    mime=detected_type(content); digest=hashlib.sha256(content).hexdigest()
    with tenant_connection(tenant_id) as c:
        ticket=c.execute("SELECT requester_user_id FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL",(tenant_id,ticket_id)).fetchone()
        if not ticket or (actor["role"] in ("client","viewer") and ticket["requester_user_id"]!=actor["id"]): raise HTTPException(404,"Ticket not found")
        forbidden=mime in FORBIDDEN_ATTACHMENT_TYPES
        row=c.execute("""INSERT INTO cm_ticket_attachments(tenant_id,ticket_id,uploaded_by,original_name,declared_type,detected_type,size_bytes,sha256,content,scan_status,rejected_reason,scan_details,scanned_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END) RETURNING id,original_name,declared_type,detected_type,size_bytes,sha256,scan_status,rejected_reason,created_at""",
            (tenant_id,ticket_id,actor["id"],safe_attachment_name(payload.original_name),payload.declared_type,mime,len(content),digest,content,"rejected" if forbidden else "quarantine","Tipo executável ou ativo bloqueado" if forbidden else None,"Bloqueio preventivo por assinatura" if forbidden else None,forbidden)).fetchone()
        return row


@router.post("/attachments/{attachment_id}/scan")
def scan_attachment(attachment_id:str,payload:AttachmentScan,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("""UPDATE cm_ticket_attachments SET scan_status=%s,scan_details=%s,scan_engine=%s,scan_reference=%s,
            scan_signature_version=%s,rejected_reason=CASE WHEN %s='rejected' THEN %s ELSE NULL END,scanned_at=now()
            WHERE tenant_id=%s AND id=%s AND scan_status='quarantine' AND detected_type<>ALL(%s)
            RETURNING id,scan_status,scan_details,scan_engine,scan_reference,scan_signature_version,scanned_at""",
            (payload.status,payload.details,payload.engine,payload.reference,payload.signature_version,payload.status,payload.details,tenant_id,attachment_id,list(FORBIDDEN_ATTACHMENT_TYPES))).fetchone()
        if not row: raise HTTPException(404,"Quarantined attachment not found")
        return row


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->Response:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        row=c.execute("""SELECT a.*,i.requester_user_id FROM cm_ticket_attachments a JOIN cm_incidents i ON i.id=a.ticket_id AND i.tenant_id=a.tenant_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.scan_status='clean'""",(tenant_id,attachment_id)).fetchone()
        if not row or (actor["role"] in ("client","viewer") and row["requester_user_id"]!=actor["id"]): raise HTTPException(404,"Released attachment not found")
    filename=safe_attachment_name(row["original_name"])
    return Response(bytes(row["content"]),media_type=row["detected_type"],headers={"Content-Disposition":f'attachment; filename="{filename}"',"X-Content-Type-Options":"nosniff","Content-Security-Policy":"sandbox","ETag":f'"{row["sha256"]}"'})
