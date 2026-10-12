from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, HttpUrl

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize
from .security import validate_outbound_url

router=APIRouter(prefix="/api/v1",tags=["integration-registry"])


class ConnectionPayload(BaseModel):
    integration:Literal["glpi","zabbix","agent","smtp","whatsapp","erp"]
    connection_key:str=Field(min_length=1,max_length=160);display_name:str=Field(min_length=2,max_length=240)
    base_url:HttpUrl|None=None;secret_reference:str|None=Field(default=None,max_length=240)


class MappingPayload(BaseModel):
    mapping_type:Literal["entity","status","category","user","team","asset","service","priority"]
    external_id:str=Field(min_length=1,max_length=300);internal_value:str=Field(min_length=1,max_length=300)
    verified:bool=False;metadata:dict[str,Any]=Field(default_factory=dict)


class HomologationPayload(BaseModel):
    state:Literal["draft","discovered","validated","homologated","blocked"]
    reason:str=Field(min_length=3,max_length=1000)


@router.get("/integrations")
def connections(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT id,integration,connection_key,display_name,base_url,active,homologation_state,last_success_at,last_failure_at,last_error,updated_at,
          (SELECT count(*) FROM cm_integration_mappings m WHERE m.tenant_id=x.tenant_id AND m.connection_id=x.id) AS mapping_count,
          (SELECT max(r.completed_at) FROM cm_reconciliation_runs r WHERE r.tenant_id=x.tenant_id AND r.connection_id=x.id AND r.state='completed') AS last_reconciled_at,
          (SELECT count(*) FROM cm_reconciliation_findings f JOIN cm_reconciliation_runs r ON r.tenant_id=f.tenant_id AND r.id=f.run_id
             WHERE r.tenant_id=x.tenant_id AND r.connection_id=x.id AND f.state='open') AS open_reconciliation_findings
          FROM cm_integration_connections x WHERE tenant_id=%s ORDER BY integration,display_name""",(tenant_id,)).fetchall())


@router.post("/integrations",status_code=201)
def create_connection(payload:ConnectionPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    if payload.base_url:
        try:validate_outbound_url(str(payload.base_url))
        except ValueError as error:raise HTTPException(422,str(error)) from error
    try:
        with tenant_connection(tenant_id) as c:
            row=c.execute("INSERT INTO cm_integration_connections(tenant_id,integration,connection_key,display_name,base_url,secret_reference) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,integration,connection_key,display_name,base_url,active,homologation_state",(tenant_id,payload.integration,payload.connection_key,payload.display_name,str(payload.base_url) if payload.base_url else None,payload.secret_reference)).fetchone()
            c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'integration.create','integration',%s,%s)",(tenant_id,actor["id"],str(row["id"]),Jsonb({"integration":payload.integration,"connection_key":payload.connection_key,"has_secret_reference":bool(payload.secret_reference)})))
            return row
    except UniqueViolation as error:raise HTTPException(409,"Integration connection key already exists") from error


@router.get("/integrations/{connection_id}/mappings")
def mappings(connection_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_integration_mappings WHERE tenant_id=%s AND connection_id=%s ORDER BY mapping_type,external_id",(tenant_id,connection_id)).fetchall())


@router.post("/integrations/{connection_id}/mappings",status_code=201)
def create_mapping(connection_id:str,payload:MappingPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_integration_mappings(tenant_id,connection_id,mapping_type,external_id,internal_value,verified,verified_at,metadata)
          SELECT %s,id,%s,%s,%s,%s,CASE WHEN %s THEN now() END,%s FROM cm_integration_connections WHERE tenant_id=%s AND id=%s
          ON CONFLICT(tenant_id,connection_id,mapping_type,external_id) DO UPDATE SET internal_value=excluded.internal_value,verified=excluded.verified,verified_at=excluded.verified_at,metadata=excluded.metadata RETURNING *""",
          (tenant_id,payload.mapping_type,payload.external_id,payload.internal_value,payload.verified,payload.verified,Jsonb(payload.metadata),tenant_id,connection_id)).fetchone()
        if not row:raise HTTPException(404,"Integration connection not found")
        return row


@router.patch("/integrations/{connection_id}/homologation")
def homologate(connection_id:str,payload:HomologationPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        if payload.state=="homologated":
            connection=c.execute("SELECT integration FROM cm_integration_connections WHERE tenant_id=%s AND id=%s",(tenant_id,connection_id)).fetchone()
            if not connection:raise HTTPException(404,"Integration connection not found")
            if connection["integration"] in ("glpi","zabbix","erp") and not c.execute("SELECT 1 FROM cm_integration_mappings WHERE tenant_id=%s AND connection_id=%s AND verified",(tenant_id,connection_id)).fetchone():raise HTTPException(409,"A verified mapping is required before homologation")
        row=c.execute("UPDATE cm_integration_connections SET homologation_state=%s,updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING id,integration,connection_key,display_name,homologation_state",(payload.state,tenant_id,connection_id)).fetchone()
        if not row:raise HTTPException(404,"Integration connection not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'integration.homologation','integration',%s,%s)",(tenant_id,actor["id"],connection_id,Jsonb({"state":payload.state,"reason":payload.reason})))
        return row
