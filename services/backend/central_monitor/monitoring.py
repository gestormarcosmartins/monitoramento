import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import connect, tenant_connection
from .operations import authorize

router=APIRouter(prefix="/api/v1",tags=["monitoring-integration"])


class ExternalIdentityPayload(BaseModel):
    asset_id:UUID
    source:Literal["glpi","zabbix","agent","nexora"]
    connection_key:str=Field(min_length=1,max_length=200)
    external_id:str=Field(min_length=1,max_length=300)
    verified:bool=False
    metadata:dict[str,Any]=Field(default_factory=dict)


class DependencyPayload(BaseModel):
    upstream_asset_id:UUID
    downstream_asset_id:UUID
    dependency_type:str=Field(default="depends_on",max_length=100)
    source:str=Field(default="manual",max_length=100)


class QuarantineResolve(BaseModel):
    asset_id:UUID


class MajorIncidentCreate(BaseModel):
    title:str=Field(min_length=3,max_length=240)
    summary:str=Field(min_length=3,max_length=5000)
    severity:Literal["high","critical"]
    impacts:list[dict[str,Any]]=Field(default_factory=list)


class MajorIncidentStatus(BaseModel):
    status:Literal["investigating","monitoring","resolved","closed"]
    summary:str|None=Field(default=None,max_length=5000)


class ChannelCapabilityPayload(BaseModel):
    provider:str|None=Field(default=None,max_length=160)
    identity_verified:bool=False
    consent_verified:bool=False
    template_approved:bool=False
    capability_verified:bool=False
    metadata:dict[str,Any]=Field(default_factory=dict)


class DependencyStatusPayload(BaseModel):
    status:Literal["up","degraded","down","unknown"]
    checked_at:datetime
    details:dict[str,Any]=Field(default_factory=dict)


def episode_fingerprint(source:str,connection_key:str,asset_id:str,service_key:str,trigger_key:str,seed:str)->str:
    return hashlib.sha256("|".join((source,connection_key,asset_id,service_key,trigger_key,seed)).encode()).hexdigest()


def channel_ready(capability:dict[str,Any]|None,channel:str)->bool:
    if not capability or not capability.get("provider"):
        return False
    required=("identity_verified","capability_verified")
    if channel=="whatsapp":required+=("consent_verified","template_approved")
    return all(bool(capability.get(field)) for field in required)


def episode_transition(current_status:str,last_event_at:datetime,incoming_at:datetime,is_recovery:bool)->tuple[bool,str]:
    if incoming_at < last_event_at:
        return False,current_status
    return True,"recovering" if is_recovery else "open"


def ingest_mapped_event(connection:Any,tenant_id:str,payload:Any)->dict[str,Any]:
    attrs=payload.attributes or {};connection_key=str(attrs.get("connection_key") or "default")
    external_asset_id=payload.asset_external_key or attrs.get("external_asset_id")
    mapping=connection.execute("""SELECT asset_id FROM cm_asset_external_identities
        WHERE tenant_id=%s AND source=%s AND connection_key=%s AND external_id=%s AND verified""",
        (tenant_id,payload.source,connection_key,external_asset_id)).fetchone() if external_asset_id else None
    if not mapping:
        try:
            row=connection.execute("""INSERT INTO cm_event_quarantine(tenant_id,source,connection_key,external_event_id,external_asset_id,reason,payload)
                VALUES (%s,%s,%s,%s,%s,'unmapped_asset',%s) RETURNING id,created_at""",
                (tenant_id,payload.source,connection_key,payload.event_id,external_asset_id,Jsonb(payload.model_dump(mode="json",by_alias=True)))).fetchone()
        except UniqueViolation:
            row=connection.execute("SELECT id,created_at FROM cm_event_quarantine WHERE tenant_id=%s AND source=%s AND connection_key=%s AND external_event_id=%s",(tenant_id,payload.source,connection_key,payload.event_id)).fetchone()
        return {"status":"quarantined","reason":"unmapped_asset",**row}
    asset_id=str(mapping["asset_id"]);service_key=str(attrs.get("service_key") or "default");trigger_key=str(attrs.get("trigger_key") or payload.event_type)
    is_recovery=bool(attrs.get("recovery")) or payload.event_type.lower() in ("recovery","resolved","ok")
    episode=connection.execute("""SELECT * FROM cm_event_episodes WHERE tenant_id=%s AND connection_key=%s AND asset_id=%s
        AND service_key=%s AND trigger_key=%s AND status IN ('open','recovering') ORDER BY first_event_at DESC LIMIT 1 FOR UPDATE""",
        (tenant_id,connection_key,asset_id,service_key,trigger_key)).fetchone()
    if not episode:
        episode_key=episode_fingerprint(payload.source,connection_key,asset_id,service_key,trigger_key,payload.occurred_at.isoformat())
        episode=connection.execute("""INSERT INTO cm_event_episodes(tenant_id,connection_key,asset_id,service_key,trigger_key,episode_key,technical_severity,first_event_at,last_event_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(tenant_id,connection_key,asset_id,service_key,trigger_key,episode_key,payload.severity,payload.occurred_at,payload.occurred_at)).fetchone()
    else:
        advances,next_status=episode_transition(episode["status"],episode["last_event_at"],payload.occurred_at,is_recovery)
        if advances:
            episode=connection.execute("""UPDATE cm_event_episodes SET last_event_at=%s,technical_severity=%s,
                status=%s,recovery_at=CASE WHEN %s THEN %s ELSE recovery_at END
                WHERE tenant_id=%s AND id=%s RETURNING *""",(payload.occurred_at,payload.severity,next_status,is_recovery,payload.occurred_at,tenant_id,episode["id"])).fetchone()
    try:
        event=connection.execute("""INSERT INTO cm_events(tenant_id,external_event_id,source,event_type,severity,asset_id,occurred_at,attributes,connection_key,episode_id,processing_state)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'correlated') RETURNING id,created_at""",
            (tenant_id,payload.event_id,payload.source,payload.event_type,payload.severity,asset_id,payload.occurred_at,Jsonb(attrs),connection_key,episode["id"])).fetchone()
    except UniqueViolation:
        return {"status":"duplicate","eventId":payload.event_id,"episode_id":episode["id"]}
    return {"status":"correlated","episode_id":episode["id"],"recovery_state":"health_updated_work_preserved" if is_recovery else None,**event}


@router.get("/asset-identities")
def identities(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT x.*,a.name AS asset_name FROM cm_asset_external_identities x JOIN cm_assets a ON a.id=x.asset_id WHERE x.tenant_id=%s ORDER BY x.source,x.connection_key,a.name",(tenant_id,)).fetchall())


@router.post("/asset-identities",status_code=201)
def create_identity(payload:ExternalIdentityPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_asset_external_identities(tenant_id,asset_id,source,connection_key,external_id,verified,metadata,verified_at)
            SELECT %s,id,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END FROM cm_assets WHERE tenant_id=%s AND id=%s RETURNING *""",
            (tenant_id,payload.source,payload.connection_key,payload.external_id,payload.verified,Jsonb(payload.metadata),payload.verified,tenant_id,payload.asset_id)).fetchone()
        if not row:raise HTTPException(404,"Asset not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'asset.identity.create','asset_identity',%s,%s)",(tenant_id,actor["id"],str(row["id"]),Jsonb({"source":payload.source,"connection_key":payload.connection_key,"external_id":payload.external_id})))
        return row


@router.get("/asset-topology")
def topology(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        nodes=list(c.execute("SELECT id,name,kind,criticality,active,metadata FROM cm_assets WHERE tenant_id=%s ORDER BY name",(tenant_id,)).fetchall())
        edges=list(c.execute("SELECT * FROM cm_asset_dependencies WHERE tenant_id=%s",(tenant_id,)).fetchall())
    return {"nodes":nodes,"edges":edges,"data_as_of":datetime.now().astimezone()}


@router.post("/asset-topology/dependencies",status_code=201)
def create_dependency(payload:DependencyPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        try:return c.execute("""INSERT INTO cm_asset_dependencies(tenant_id,upstream_asset_id,downstream_asset_id,dependency_type,source)
            SELECT %s,u.id,d.id,%s,%s FROM cm_assets u JOIN cm_assets d ON d.tenant_id=u.tenant_id
            WHERE u.tenant_id=%s AND u.id=%s AND d.id=%s RETURNING *""",(tenant_id,payload.dependency_type,payload.source,tenant_id,payload.upstream_asset_id,payload.downstream_asset_id)).fetchone()
        except UniqueViolation as error:raise HTTPException(409,"Dependency already exists") from error


@router.get("/event-quarantine")
def quarantined_events(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","operator","auditor"))
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_event_quarantine WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 500",(tenant_id,)).fetchall())


@router.post("/event-quarantine/{quarantine_id}/map")
def map_quarantined_event(quarantine_id:str,payload:QuarantineResolve,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        item=c.execute("SELECT * FROM cm_event_quarantine WHERE tenant_id=%s AND id=%s AND status='pending' FOR UPDATE",(tenant_id,quarantine_id)).fetchone()
        if not item:raise HTTPException(404,"Pending quarantined event not found")
        mapping=c.execute("""INSERT INTO cm_asset_external_identities(tenant_id,asset_id,source,connection_key,external_id,verified,verified_at,metadata)
            SELECT %s,id,%s,%s,%s,true,now(),%s FROM cm_assets WHERE tenant_id=%s AND id=%s
            ON CONFLICT(tenant_id,source,connection_key,external_id) DO UPDATE SET asset_id=excluded.asset_id,verified=true,verified_at=now() RETURNING *""",
            (tenant_id,item["source"],item["connection_key"],item["external_asset_id"],Jsonb({"resolved_from_quarantine":quarantine_id}),tenant_id,payload.asset_id)).fetchone()
        if not mapping:raise HTTPException(404,"Asset not found")
        c.execute("UPDATE cm_event_quarantine SET status='mapped',resolved_by=%s,resolved_at=now() WHERE tenant_id=%s AND id=%s",(actor["id"],tenant_id,quarantine_id))
        return mapping


def ensure_operator(c:Any,tenant_id:str)->dict[str,Any]:
    tenant=c.execute("SELECT id,workspace_id,company_kind FROM cm_tenants WHERE id=%s",(tenant_id,)).fetchone()
    if not tenant or tenant["company_kind"] not in ("operator","both"):raise HTTPException(403,"Major incidents must be declared by an operator company")
    return tenant


@router.get("/major-incidents")
def major_incidents(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","operator","auditor"))
    with connect() as c:
        rows=list(c.execute("""SELECT m.*,count(i.impacted_tenant_id) AS impacted_companies FROM cm_major_incidents m
            LEFT JOIN cm_major_incident_impacts i ON i.major_incident_id=m.id AND i.operator_tenant_id=m.operator_tenant_id
            WHERE m.operator_tenant_id=%s GROUP BY m.id ORDER BY m.declared_at DESC""",(tenant_id,)).fetchall())
    return rows


@router.post("/major-incidents",status_code=201)
def create_major_incident(payload:MajorIncidentCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with connect() as c:
        operator=ensure_operator(c,tenant_id)
        row=c.execute("INSERT INTO cm_major_incidents(operator_tenant_id,title,summary,severity,commander_id) VALUES (%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.title,payload.summary,payload.severity,actor["id"])).fetchone()
        for impact in payload.impacts:
            impacted=str(impact.get("tenant_id") or "");public_summary=str(impact.get("public_summary") or "").strip();incident_id=impact.get("incident_id")
            if not public_summary:raise HTTPException(422,"Each impacted company requires a sanitized public_summary")
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(impacted,))
            allowed=c.execute("""SELECT 1 FROM cm_tenants t WHERE t.id=%s AND t.workspace_id=%s AND
                (t.id=%s OR EXISTS(SELECT 1 FROM cm_service_engagements e WHERE e.tenant_id=t.id AND e.operator_company_id=%s AND e.active))""",(impacted,operator["workspace_id"],tenant_id,tenant_id)).fetchone()
            if not allowed:raise HTTPException(422,"Impacted company is outside the operator scope")
            c.execute("INSERT INTO cm_major_incident_impacts(major_incident_id,operator_tenant_id,impacted_tenant_id,incident_id,public_summary) VALUES (%s,%s,%s,%s,%s)",(row["id"],tenant_id,impacted,incident_id,public_summary))
        return row


@router.patch("/major-incidents/{major_id}")
def update_major_incident(major_id:str,payload:MajorIncidentStatus,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with connect() as c:
        ensure_operator(c,tenant_id)
        row=c.execute("UPDATE cm_major_incidents SET status=%s,summary=COALESCE(%s,summary),resolved_at=CASE WHEN %s='resolved' THEN now() ELSE resolved_at END,updated_at=now() WHERE operator_tenant_id=%s AND id=%s RETURNING *",(payload.status,payload.summary,payload.status,tenant_id,major_id)).fetchone()
        if not row:raise HTTPException(404,"Major incident not found")
        return row


@router.get("/major-incidents/{major_id}/communications")
def major_communications(major_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","operator","auditor"))
    with connect() as c:
        return list(c.execute("""SELECT i.impacted_tenant_id,t.name AS company,i.public_summary,m.status,m.severity,m.declared_at,m.resolved_at
            FROM cm_major_incident_impacts i JOIN cm_major_incidents m ON m.id=i.major_incident_id AND m.operator_tenant_id=i.operator_tenant_id
            JOIN cm_tenants t ON t.id=i.impacted_tenant_id WHERE i.operator_tenant_id=%s AND i.major_incident_id=%s ORDER BY t.name""",(tenant_id,major_id)).fetchall())


@router.put("/channel-capabilities/{channel}")
def update_channel_capability(channel:Literal["whatsapp","erp"],payload:ChannelCapabilityPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:return c.execute("""INSERT INTO cm_channel_capabilities(tenant_id,channel,provider,identity_verified,consent_verified,template_approved,capability_verified,metadata)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,channel) DO UPDATE SET provider=excluded.provider,identity_verified=excluded.identity_verified,consent_verified=excluded.consent_verified,template_approved=excluded.template_approved,capability_verified=excluded.capability_verified,metadata=excluded.metadata,updated_at=now() RETURNING *""",
        (tenant_id,channel,payload.provider,payload.identity_verified,payload.consent_verified,payload.template_approved,payload.capability_verified,Jsonb(payload.metadata))).fetchone()


@router.get("/external-dependencies")
def external_dependencies(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with connect() as c:return list(c.execute("SELECT * FROM cm_external_dependency_status ORDER BY dependency").fetchall())


@router.put("/external-dependencies/{dependency}")
def update_external_dependency(dependency:Literal["nexora","glpi"],payload:DependencyStatusPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with connect() as c:return c.execute("""INSERT INTO cm_external_dependency_status(dependency,status,checked_at,details) VALUES (%s,%s,%s,%s)
        ON CONFLICT(dependency) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at,details=excluded.details RETURNING *""",(dependency,payload.status,payload.checked_at,Jsonb(payload.details))).fetchone()
