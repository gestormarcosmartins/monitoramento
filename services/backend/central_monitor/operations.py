from datetime import date, datetime
from io import BytesIO
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from openpyxl import Workbook
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, EmailStr, Field
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .auth import Identity, current_identity, tenant_header
from .db import connect, tenant_connection
from .notifications import get_notification_settings, save_notification_settings, send_email, send_whatsapp

router = APIRouter(prefix="/api/v1", tags=["operations"])
ROLES = ("admin", "manager", "operator", "viewer", "client", "auditor")


def authorize(identity: Identity, tenant_id: str, roles: tuple[str, ...] = ROLES) -> dict[str, Any]:
    with connect() as connection:
        user = connection.execute(
            """INSERT INTO cm_users(external_subject,email,display_name) VALUES (%s,%s,%s)
            ON CONFLICT (external_subject) DO UPDATE SET email=excluded.email,display_name=excluded.display_name RETURNING *""",
            (identity.subject, identity.email, identity.display_name),
        ).fetchone()
        membership = connection.execute(
            "SELECT role FROM cm_user_tenants WHERE tenant_id=%s AND user_id=%s", (tenant_id, user["id"])
        ).fetchone()
    if not membership or membership["role"] not in roles:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    return {**user, "role": membership["role"]}


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    is_internal: bool = False


class SupplierPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    contact_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    services: str | None = None
    contract_reference: str | None = None
    response_minutes: int = Field(default=240, ge=1)
    resolution_minutes: int = Field(default=1440, ge=1)
    active: bool = True


class SlaPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    priority: Literal["low", "medium", "high", "critical"]
    response_minutes: int = Field(ge=1)
    resolution_minutes: int = Field(ge=1)
    coverage: Literal["24x7", "business_hours"] = "24x7"
    pause_on_waiting: bool = True
    active: bool = True


class TeamPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    active: bool = True


class NotificationRulePayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    channel: Literal["email", "whatsapp", "webhook"]
    target: str = Field(min_length=3, max_length=1000)
    minimum_priority: Literal["low", "medium", "high", "critical"] = "high"
    events: list[str] = Field(default_factory=lambda: ["incident.created"])
    active: bool = True


class NotificationSettingsPayload(BaseModel):
    smtp_enabled: bool = False
    smtp_host: str | None = Field(default=None, max_length=500)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=500)
    smtp_password: str | None = Field(default=None, max_length=2000)
    smtp_from_address: EmailStr | None = None
    smtp_tls: bool = True
    evolution_enabled: bool = False
    evolution_api_url: str | None = Field(default=None, max_length=1000)
    evolution_api_key: str | None = Field(default=None, max_length=2000)
    evolution_instance: str | None = Field(default=None, max_length=300)


class TestEmailPayload(BaseModel):
    target: EmailStr


class TestWhatsappPayload(BaseModel):
    target: str = Field(min_length=10, max_length=30)


class MaintenancePayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    asset_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str | None = None
    active: bool = True


class MembershipPayload(BaseModel):
    user_id: UUID
    role: Literal["admin", "manager", "operator", "viewer", "client", "auditor"]


class HolidayPayload(BaseModel):
    holiday_date: date
    name: str = Field(min_length=2,max_length=160)


@router.get("/incidents/{incident_id}/detail")
def incident_detail(incident_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            """SELECT i.*,a.severity,a.message,u.display_name AS assignee_name,t.name AS team_name,s.name AS supplier_name,
            CASE WHEN i.status NOT IN ('resolved','closed','ignored') AND i.resolution_due_at < now() THEN 'breached'
                 WHEN i.status NOT IN ('resolved','closed','ignored') AND i.resolution_due_at < now()+interval '1 hour' THEN 'at_risk'
                 ELSE 'on_track' END AS sla_status
            FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id
            LEFT JOIN cm_users u ON u.id=i.assignee_user_id LEFT JOIN cm_teams t ON t.id=i.assignee_team_id
            LEFT JOIN cm_suppliers s ON s.id=i.supplier_id WHERE i.tenant_id=%s AND i.id=%s""",
            (tenant_id, incident_id),
        ).fetchone()
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        comments = list(connection.execute("SELECT c.*,u.display_name AS author_name FROM cm_incident_comments c LEFT JOIN cm_users u ON u.id=c.author_id WHERE c.tenant_id=%s AND c.incident_id=%s ORDER BY c.created_at", (tenant_id,incident_id)).fetchall())
        history = list(connection.execute("SELECT h.*,u.display_name AS actor_name FROM cm_incident_history h LEFT JOIN cm_users u ON u.id=h.actor_id WHERE h.tenant_id=%s AND h.incident_id=%s ORDER BY h.created_at DESC", (tenant_id,incident_id)).fetchall())
    return {"incident": incident, "comments": comments, "history": history}


@router.post("/incidents/{incident_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(incident_id: str, payload: CommentCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize(identity, tenant_id, ("admin","manager","operator","client"))
    if payload.is_internal and user["role"] == "client":
        raise HTTPException(status_code=403, detail="Clients cannot create internal comments")
    with tenant_connection(tenant_id) as connection:
        comment = connection.execute("INSERT INTO cm_incident_comments(tenant_id,incident_id,author_id,body,is_internal) VALUES (%s,%s,%s,%s,%s) RETURNING *", (tenant_id,incident_id,user["id"],payload.body,payload.is_internal)).fetchone()
        connection.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'comment.created',%s)", (tenant_id,incident_id,user["id"],Jsonb({"comment_id":str(comment["id"]),"internal":payload.is_internal})))
        connection.execute("INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.comment_created',%s)", (tenant_id,Jsonb({"incident_id":incident_id,"comment_id":str(comment["id"])})))
        return comment


def crud_list(table: str, tenant_id: str) -> list[dict[str, Any]]:
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(f"SELECT * FROM {table} WHERE tenant_id=%s ORDER BY name", (tenant_id,)).fetchall())


@router.get("/suppliers")
def suppliers(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize(identity,tenant_id); return crud_list("cm_suppliers",tenant_id)


@router.post("/suppliers", status_code=201)
def create_supplier(payload: SupplierPayload, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize(identity,tenant_id,("admin","manager"))
    try:
        with tenant_connection(tenant_id) as c:
            return c.execute("INSERT INTO cm_suppliers(tenant_id,name,contact_name,email,phone,services,contract_reference,response_minutes,resolution_minutes,active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.contact_name,payload.email,payload.phone,payload.services,payload.contract_reference,payload.response_minutes,payload.resolution_minutes,payload.active)).fetchone()
    except UniqueViolation as error: raise HTTPException(409,"Supplier already exists") from error


@router.put("/suppliers/{item_id}")
def update_supplier(item_id: str,payload: SupplierPayload,tenant_id: str=Depends(tenant_header),identity: Identity=Depends(current_identity)) -> dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_suppliers SET name=%s,contact_name=%s,email=%s,phone=%s,services=%s,contract_reference=%s,response_minutes=%s,resolution_minutes=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.contact_name,payload.email,payload.phone,payload.services,payload.contract_reference,payload.response_minutes,payload.resolution_minutes,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Supplier not found")
        return row


@router.delete("/suppliers/{item_id}",status_code=204)
def delete_supplier(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_suppliers WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/sla-policies")
def sla_policies(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id); return crud_list("cm_sla_policies",tenant_id)


@router.get("/holidays")
def holidays(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c: return list(c.execute("SELECT * FROM cm_holidays WHERE tenant_id=%s ORDER BY holiday_date",(tenant_id,)).fetchall())


@router.post("/holidays",status_code=201)
def create_holiday(payload:HolidayPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c: return c.execute("INSERT INTO cm_holidays(tenant_id,holiday_date,name) VALUES (%s,%s,%s) ON CONFLICT(tenant_id,holiday_date) DO UPDATE SET name=excluded.name RETURNING *",(tenant_id,payload.holiday_date,payload.name)).fetchone()


@router.put("/holidays/{item_id}")
def update_holiday(item_id:str,payload:HolidayPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_holidays SET holiday_date=%s,name=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.holiday_date,payload.name,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Holiday not found")
        return row


@router.delete("/holidays/{item_id}",status_code=204)
def delete_holiday(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_holidays WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.post("/sla-policies",status_code=201)
def create_sla(payload:SlaPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        return c.execute("INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes,coverage,pause_on_waiting,active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,priority) DO UPDATE SET name=excluded.name,response_minutes=excluded.response_minutes,resolution_minutes=excluded.resolution_minutes,coverage=excluded.coverage,pause_on_waiting=excluded.pause_on_waiting,active=excluded.active RETURNING *",(tenant_id,payload.name,payload.priority,payload.response_minutes,payload.resolution_minutes,payload.coverage,payload.pause_on_waiting,payload.active)).fetchone()


@router.put("/sla-policies/{item_id}")
def update_sla(item_id:str,payload:SlaPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_sla_policies SET name=%s,priority=%s,response_minutes=%s,resolution_minutes=%s,coverage=%s,pause_on_waiting=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.priority,payload.response_minutes,payload.resolution_minutes,payload.coverage,payload.pause_on_waiting,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"SLA policy not found")
        return row


@router.delete("/sla-policies/{item_id}",status_code=204)
def delete_sla(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_sla_policies WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/teams")
def teams(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id); return crud_list("cm_teams",tenant_id)


@router.post("/teams",status_code=201)
def create_team(payload:TeamPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c: return c.execute("INSERT INTO cm_teams(tenant_id,name,active) VALUES (%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.active)).fetchone()


@router.put("/teams/{item_id}")
def update_team(item_id:str,payload:TeamPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_teams SET name=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Team not found")
        return row


@router.delete("/teams/{item_id}",status_code=204)
def delete_team(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_teams WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/notification-rules")
def notification_rules(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id); return crud_list("cm_notification_rules",tenant_id)


@router.post("/notification-rules",status_code=201)
def create_notification_rule(payload:NotificationRulePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c: return c.execute("INSERT INTO cm_notification_rules(tenant_id,name,channel,target,minimum_priority,events,active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.channel,payload.target,payload.minimum_priority,payload.events,payload.active)).fetchone()


@router.put("/notification-rules/{item_id}")
def update_notification_rule(item_id:str,payload:NotificationRulePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_notification_rules SET name=%s,channel=%s,target=%s,minimum_priority=%s,events=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.channel,payload.target,payload.minimum_priority,payload.events,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Notification rule not found")
        return row


@router.delete("/notification-rules/{item_id}",status_code=204)
def delete_notification_rule(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_notification_rules WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/notification-settings")
def notification_settings(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    return get_notification_settings(tenant_id)


@router.put("/notification-settings")
def update_notification_settings(payload:NotificationSettingsPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    if payload.evolution_api_url and not payload.evolution_api_url.startswith(("http://","https://")):
        raise HTTPException(422,"A URL da Evolution API deve usar HTTP ou HTTPS")
    return save_notification_settings(tenant_id,payload.model_dump())


@router.post("/notification-settings/test-email")
def test_email(payload:TestEmailPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,str]:
    authorize(identity,tenant_id,("admin","manager"))
    try: send_email(tenant_id,str(payload.target),"Teste Nexora","Configuração de e-mail validada com sucesso.")
    except Exception as error: raise HTTPException(502,f"Falha no teste de e-mail: {error}") from error
    return {"message":"E-mail de teste enviado"}


@router.post("/notification-settings/test-whatsapp")
def test_whatsapp(payload:TestWhatsappPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,str]:
    authorize(identity,tenant_id,("admin","manager"))
    try: send_whatsapp(tenant_id,payload.target,"Teste Nexora: configuração de WhatsApp validada com sucesso.")
    except Exception as error: raise HTTPException(502,f"Falha no teste de WhatsApp: {error}") from error
    return {"message":"WhatsApp de teste enviado"}


@router.get("/maintenance-windows")
def maintenance_windows(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c: return list(c.execute("SELECT m.*,a.name AS asset_name FROM cm_maintenance_windows m LEFT JOIN cm_assets a ON a.id=m.asset_id WHERE m.tenant_id=%s ORDER BY starts_at DESC",(tenant_id,)).fetchall())


@router.post("/maintenance-windows",status_code=201)
def create_maintenance(payload:MaintenancePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c: return c.execute("INSERT INTO cm_maintenance_windows(tenant_id,name,asset_id,starts_at,ends_at,reason,active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.asset_id,payload.starts_at,payload.ends_at,payload.reason,payload.active)).fetchone()


@router.put("/maintenance-windows/{item_id}")
def update_maintenance(item_id:str,payload:MaintenancePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_maintenance_windows SET name=%s,asset_id=%s,starts_at=%s,ends_at=%s,reason=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.asset_id,payload.starts_at,payload.ends_at,payload.reason,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Maintenance window not found")
        return row


@router.delete("/maintenance-windows/{item_id}",status_code=204)
def delete_maintenance(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_maintenance_windows WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/users")
def users(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with connect() as c: return list(c.execute("SELECT u.id,u.email,u.display_name,u.active,ut.role FROM cm_user_tenants ut JOIN cm_users u ON u.id=ut.user_id WHERE ut.tenant_id=%s ORDER BY u.display_name",(tenant_id,)).fetchall())


@router.put("/users/membership")
def update_membership(payload:MembershipPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with connect() as c:
        row=c.execute("INSERT INTO cm_user_tenants(user_id,tenant_id,role) VALUES (%s,%s,%s) ON CONFLICT(user_id,tenant_id) DO UPDATE SET role=excluded.role RETURNING *",(payload.user_id,tenant_id,payload.role)).fetchone()
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'role.change','membership',%s,%s)",(tenant_id,actor["id"],str(payload.user_id),Jsonb({"role":payload.role})))
        return row


@router.patch("/users/{user_id}/active")
def toggle_user(user_id:str,active:bool,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with connect() as c:
        row=c.execute("UPDATE cm_users SET active=%s WHERE id=%s AND EXISTS(SELECT 1 FROM cm_user_tenants WHERE tenant_id=%s AND user_id=cm_users.id) RETURNING id,active",(active,user_id,tenant_id)).fetchone()
        if not row: raise HTTPException(404,"User not found")
        return row


@router.delete("/actions/grants/{item_id}",status_code=204)
def delete_action_grant(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c: c.execute("DELETE FROM cm_action_grants WHERE tenant_id=%s AND id=%s",(tenant_id,item_id))


@router.get("/audit-log")
def audit_log(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with connect() as c: return list(c.execute("SELECT l.*,u.display_name AS user_name FROM cm_audit_log l LEFT JOIN cm_users u ON u.id=l.user_id WHERE l.tenant_id=%s ORDER BY l.created_at DESC LIMIT 500",(tenant_id,)).fetchall())


@router.get("/actions/catalog")
def action_catalog(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","operator","auditor"))
    with tenant_connection(tenant_id) as c:
        definitions=list(c.execute("SELECT * FROM cm_action_definitions WHERE enabled ORDER BY name").fetchall())
        grants=list(c.execute("SELECT g.*,d.code,d.name,a.name AS asset_name FROM cm_action_grants g JOIN cm_action_definitions d ON d.id=g.action_id JOIN cm_assets a ON a.id=g.asset_id WHERE g.tenant_id=%s ORDER BY a.name,d.name",(tenant_id,)).fetchall())
        executions=list(c.execute("SELECT e.*,d.code,d.name,a.name AS asset_name,u.display_name AS requested_by_name FROM cm_action_executions e JOIN cm_action_definitions d ON d.id=e.action_id JOIN cm_assets a ON a.id=e.asset_id LEFT JOIN cm_users u ON u.id=e.requested_by WHERE e.tenant_id=%s ORDER BY e.created_at DESC LIMIT 100",(tenant_id,)).fetchall())
    return {"definitions":definitions,"grants":grants,"executions":executions}


@router.get("/reports/summary")
def report_summary(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        totals=c.execute("""SELECT count(*) AS total,
        count(*) FILTER(WHERE status NOT IN ('resolved','closed','ignored')) AS open,
        count(*) FILTER(WHERE resolution_due_at<now() AND status NOT IN ('resolved','closed','ignored')) AS breached,
        round(avg(EXTRACT(EPOCH FROM (first_response_at-created_at))/60) FILTER(WHERE first_response_at IS NOT NULL),1) AS mtta_minutes,
        round(avg(EXTRACT(EPOCH FROM (resolved_at-created_at))/60) FILTER(WHERE resolved_at IS NOT NULL),1) AS mttr_minutes
        FROM cm_incidents WHERE tenant_id=%s""",(tenant_id,)).fetchone()
        by_priority=list(c.execute("SELECT priority,count(*) AS total FROM cm_incidents WHERE tenant_id=%s GROUP BY priority ORDER BY priority",(tenant_id,)).fetchall())
        suppliers=list(c.execute("SELECT s.name,count(i.id) AS incidents,count(i.id) FILTER(WHERE i.supplier_due_at<now() AND i.status='waiting_supplier') AS breached FROM cm_suppliers s LEFT JOIN cm_incidents i ON i.supplier_id=s.id WHERE s.tenant_id=%s GROUP BY s.id,s.name ORDER BY incidents DESC",(tenant_id,)).fetchall())
    return {"totals":totals,"by_priority":by_priority,"suppliers":suppliers}


def _report_rows(tenant_id: str) -> list[dict[str, Any]]:
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT i.id,i.title,i.priority,i.status,i.created_at,i.first_response_at,i.resolved_at,
        i.response_due_at,i.resolution_due_at,s.name AS supplier,tm.name AS team,u.display_name AS responsible
        FROM cm_incidents i LEFT JOIN cm_suppliers s ON s.id=i.supplier_id LEFT JOIN cm_teams tm ON tm.id=i.assignee_team_id
        LEFT JOIN cm_users u ON u.id=i.assignee_user_id WHERE i.tenant_id=%s ORDER BY i.created_at DESC""",(tenant_id,)).fetchall())


@router.get("/reports/export/{export_format}")
def export_report(export_format:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->Response:
    authorize(identity,tenant_id)
    rows=_report_rows(tenant_id)
    headers=["ID","Título","Prioridade","Estado","Abertura","Primeiro atendimento","Resolução","Prazo resposta","Prazo solução","Fornecedor","Equipe","Responsável"]
    keys=["id","title","priority","status","created_at","first_response_at","resolved_at","response_due_at","resolution_due_at","supplier","team","responsible"]
    if export_format == "xlsx":
        workbook=Workbook(); sheet=workbook.active; sheet.title="Incidentes"; sheet.append(headers)
        for row in rows: sheet.append([str(row.get(key) or "") for key in keys])
        for cell in sheet[1]: cell.font=cell.font.copy(bold=True)
        output=BytesIO(); workbook.save(output)
        return Response(output.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":"attachment; filename=relatorio-incidentes.xlsx"})
    if export_format == "pdf":
        output=BytesIO(); pdf=canvas.Canvas(output,pagesize=A4); width,height=A4
        pdf.setFont("Helvetica-Bold",16); pdf.drawString(40,height-45,"Nexora - Relatório de incidentes")
        y=height-75; pdf.setFont("Helvetica",8)
        for row in rows:
            line=f"{str(row['created_at'])[:16]} | {row['priority']} | {row['status']} | {row['title']}"
            pdf.drawString(40,y,line[:115]); y-=14
            if y<40: pdf.showPage(); pdf.setFont("Helvetica",8); y=height-45
        pdf.save()
        return Response(output.getvalue(),media_type="application/pdf",headers={"Content-Disposition":"attachment; filename=relatorio-incidentes.pdf"})
    raise HTTPException(400,"Format must be pdf or xlsx")
