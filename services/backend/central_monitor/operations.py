from datetime import date, datetime
from io import BytesIO
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from openpyxl import Workbook
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, EmailStr, Field, model_validator
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .auth import Identity, current_identity, tenant_header
from .access_scope import incident_scope
from .db import connect, tenant_connection
from .notifications import get_notification_settings, get_whatsapp_connection, save_notification_settings, send_email, send_whatsapp

router = APIRouter(prefix="/api/v1", tags=["operations"])
ROLES = ("admin", "manager", "operator", "viewer", "client", "auditor")


def authorize(identity: Identity, tenant_id: str, roles: tuple[str, ...] = ROLES) -> dict[str, Any]:
    with connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(20261011)")
        user = connection.execute(
            """INSERT INTO cm_users(external_subject,email,display_name,is_master)
            VALUES (%s,%s,%s,NOT EXISTS (SELECT 1 FROM cm_users WHERE is_master))
            ON CONFLICT (external_subject) DO UPDATE SET email=excluded.email,display_name=excluded.display_name RETURNING *""",
            (identity.subject, identity.email, identity.display_name),
        ).fetchone()
    with tenant_connection(tenant_id) as connection:
        membership = connection.execute(
            """SELECT role,row_scope,engagement_id,department_id,team_id FROM cm_access_grants
            WHERE tenant_id=%s AND user_id=%s AND active
              AND valid_from<=now() AND (valid_until IS NULL OR valid_until>now())
            ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'manager' THEN 2 WHEN 'operator' THEN 3
                WHEN 'auditor' THEN 4 WHEN 'viewer' THEN 5 ELSE 6 END LIMIT 1""",
            (tenant_id, user["id"]),
        ).fetchone()
    if not user["active"]:
        raise HTTPException(status_code=403, detail="User is inactive")
    if user["is_master"]:
        return {**user, "role": "master"}
    if not membership or membership["role"] not in roles:
        raise HTTPException(status_code=403, detail="Tenant access denied")
    return {**user, **membership}


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
    emails: list[EmailStr] = Field(default_factory=list)
    whatsapp_numbers: list[str] = Field(default_factory=list)
    whatsapp_group_id: str | None = Field(default=None, max_length=200)
    whatsapp_group_ids: list[str] = Field(default_factory=list)
    member_user_ids: list[UUID] = Field(default_factory=list)
    active: bool = True


class UserNotificationPayload(BaseModel):
    whatsapp_number: str | None = Field(default=None, max_length=30)


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


class UserAccessPayload(BaseModel):
    is_master: bool = False
    tenant_ids: list[UUID] = Field(default_factory=list)


class AccessGrantPayload(BaseModel):
    user_id: UUID
    role: Literal["admin", "manager", "operator", "viewer", "client", "auditor"] = "viewer"
    row_scope: Literal["own", "department", "team_service", "company"] = "company"
    engagement_id: UUID | None = None
    department_id: UUID | None = None
    team_id: UUID | None = None
    valid_until: datetime | None = None
    reason: str = Field(default="Acesso concedido pela administração", min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_scope_target(self):
        if self.row_scope == "department" and not self.department_id:
            raise ValueError("department scope requires department_id")
        if self.row_scope == "team_service" and not (self.team_id or self.engagement_id):
            raise ValueError("team_service scope requires team_id or engagement_id")
        if self.row_scope in ("own", "company") and (self.department_id or self.team_id or self.engagement_id):
            raise ValueError(f"{self.row_scope} scope does not accept a target")
        return self


class DepartmentPayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    active: bool = True


class UserDepartmentsPayload(BaseModel):
    department_ids: list[UUID] = Field(default_factory=list)


class ServiceEngagementPayload(BaseModel):
    operator_company_id: UUID
    kind: Literal["internal", "managed_service"]
    name: str = Field(min_length=2, max_length=160)
    ends_at: datetime | None = None
    active: bool = True


class HolidayPayload(BaseModel):
    holiday_date: date
    name: str = Field(min_length=2,max_length=160)


@router.get("/incidents/{incident_id}/detail")
def incident_detail(incident_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    actor=authorize(identity, tenant_id)
    scope_sql,scope_params=incident_scope(actor)
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            f"""SELECT i.*,a.severity,a.message,u.display_name AS assignee_name,t.name AS team_name,s.name AS supplier_name,
            CASE WHEN i.status NOT IN ('resolved','closed','ignored') AND i.resolution_due_at < now() THEN 'breached'
                 WHEN i.status NOT IN ('resolved','closed','ignored') AND i.resolution_due_at < now()+interval '1 hour' THEN 'at_risk'
                 ELSE 'on_track' END AS sla_status
            FROM cm_incidents i LEFT JOIN cm_alerts a ON a.id=i.alert_id
            LEFT JOIN cm_users u ON u.id=i.assignee_user_id LEFT JOIN cm_teams t ON t.id=i.assignee_team_id
            LEFT JOIN cm_suppliers s ON s.id=i.supplier_id WHERE i.tenant_id=%s AND i.id=%s AND {scope_sql}""",
            (tenant_id, incident_id, *scope_params),
        ).fetchone()
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        private_filter="" if actor["role"] in ("master","admin","manager","operator","auditor") else " AND NOT c.is_internal"
        comments = list(connection.execute(f"SELECT c.*,u.display_name AS author_name FROM cm_incident_comments c LEFT JOIN cm_users u ON u.id=c.author_id WHERE c.tenant_id=%s AND c.incident_id=%s{private_filter} ORDER BY c.created_at", (tenant_id,incident_id)).fetchall())
        history = list(connection.execute("SELECT h.*,u.display_name AS actor_name FROM cm_incident_history h LEFT JOIN cm_users u ON u.id=h.actor_id WHERE h.tenant_id=%s AND h.incident_id=%s ORDER BY h.created_at DESC", (tenant_id,incident_id)).fetchall())
        tasks=list(connection.execute("SELECT t.*,u.display_name AS assignee_name FROM cm_ticket_tasks t LEFT JOIN cm_users u ON u.id=t.assignee_user_id WHERE t.tenant_id=%s AND t.incident_id=%s ORDER BY t.created_at",(tenant_id,incident_id)).fetchall())
        solutions=list(connection.execute("SELECT * FROM cm_ticket_solutions WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at DESC",(tenant_id,incident_id)).fetchall())
        approvals=list(connection.execute("SELECT id,status,approver_id,reason,decided_at,created_at FROM cm_ticket_approvals WHERE tenant_id=%s AND ticket_id=%s ORDER BY created_at DESC",(tenant_id,incident_id)).fetchall())
        obligations=list(connection.execute("SELECT kind,state,due_at,completed_at,paused_seconds FROM cm_ticket_obligations WHERE tenant_id=%s AND ticket_id=%s ORDER BY kind",(tenant_id,incident_id)).fetchall())
        assets=list(connection.execute("""SELECT DISTINCT a.id,a.name,a.kind,a.address,a.criticality,a.active FROM cm_assets a
          JOIN cm_monitors m ON m.tenant_id=a.tenant_id AND m.asset_id=a.id JOIN cm_alerts al ON al.tenant_id=m.tenant_id AND al.monitor_id=m.id
          JOIN cm_incidents i ON i.tenant_id=al.tenant_id AND i.alert_id=al.id WHERE i.tenant_id=%s AND i.id=%s""",(tenant_id,incident_id)).fetchall())
        contracts=list(connection.execute("""SELECT c.id,c.code,c.name,c.status,c.active_version FROM cm_contracts c JOIN cm_incidents i ON i.tenant_id=c.tenant_id AND i.engagement_id=c.engagement_id
          WHERE i.tenant_id=%s AND i.id=%s ORDER BY c.created_at DESC""",(tenant_id,incident_id)).fetchall())
        consumption=[] if actor["role"] in ("client","viewer") else list(connection.execute("SELECT entry_type,competence,raw_minutes,billable_minutes,included_minutes,excess_minutes,amount,description,created_at FROM cm_consumption_ledger WHERE tenant_id=%s AND ticket_id=%s ORDER BY created_at",(tenant_id,incident_id)).fetchall())
        attachments=list(connection.execute("SELECT id,original_name,detected_type,size_bytes,sha256,scan_status,created_at FROM cm_ticket_attachments WHERE tenant_id=%s AND ticket_id=%s AND (%s OR scan_status='clean') ORDER BY created_at",(tenant_id,incident_id,actor["role"] not in ("client","viewer"))).fetchall())
    return {"incident": incident, "comments": comments, "history": history,"tasks":tasks,"solutions":solutions,"approvals":approvals,"obligations":obligations,"assets":assets,"contracts":contracts,"consumption":consumption,"attachments":attachments,
            "allowed_tabs":["service","tasks","approvals","assets","contract","history"] if actor["role"] not in ("client","viewer") else ["service","tasks","approvals","assets","history"],"data_as_of":datetime.now().astimezone()}


@router.post("/incidents/{incident_id}/comments", status_code=status.HTTP_201_CREATED)
def add_comment(incident_id: str, payload: CommentCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize(identity, tenant_id, ("admin","manager","operator","client"))
    if payload.is_internal and user["role"] == "client":
        raise HTTPException(status_code=403, detail="Clients cannot create internal comments")
    with tenant_connection(tenant_id) as connection:
        scope_sql,scope_params=incident_scope(user)
        allowed=connection.execute(f"SELECT 1 FROM cm_incidents i WHERE i.tenant_id=%s AND i.id=%s AND i.inactive_at IS NULL AND {scope_sql}",(tenant_id,incident_id,*scope_params)).fetchone()
        if not allowed:
            raise HTTPException(status_code=404,detail="Incident not found")
        comment = connection.execute("INSERT INTO cm_incident_comments(tenant_id,incident_id,author_id,body,is_internal) VALUES (%s,%s,%s,%s,%s) RETURNING *", (tenant_id,incident_id,user["id"],payload.body,payload.is_internal)).fetchone()
        if not payload.is_internal and user["role"] in ("master","admin","manager","operator"):
            connection.execute("UPDATE cm_incidents SET first_response_at=COALESCE(first_response_at,now()) WHERE tenant_id=%s AND id=%s",(tenant_id,incident_id))
            connection.execute("UPDATE cm_ticket_obligations SET state='met',completed_at=COALESCE(completed_at,now()),updated_at=now() WHERE tenant_id=%s AND ticket_id=%s AND kind='first_response' AND state IN ('running','paused')",(tenant_id,incident_id))
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
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_suppliers SET active=false WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Supplier not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','supplier',%s)",(tenant_id,actor["id"],item_id))


@router.get("/sla-policies")
def sla_policies(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id); return crud_list("cm_sla_policies",tenant_id)


@router.get("/holidays")
def holidays(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c: return list(c.execute("SELECT * FROM cm_holidays WHERE tenant_id=%s AND active ORDER BY holiday_date",(tenant_id,)).fetchall())


@router.post("/holidays",status_code=201)
def create_holiday(payload:HolidayPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c: return c.execute("INSERT INTO cm_holidays(tenant_id,holiday_date,name) VALUES (%s,%s,%s) ON CONFLICT(tenant_id,holiday_date) DO UPDATE SET name=excluded.name,active=true,inactive_at=NULL RETURNING *",(tenant_id,payload.holiday_date,payload.name)).fetchone()


@router.put("/holidays/{item_id}")
def update_holiday(item_id:str,payload:HolidayPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_holidays SET holiday_date=%s,name=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.holiday_date,payload.name,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Holiday not found")
        return row


@router.delete("/holidays/{item_id}",status_code=204)
def delete_holiday(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_holidays SET active=false,inactive_at=now() WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Holiday not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','holiday',%s)",(tenant_id,actor["id"],item_id))


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
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_sla_policies SET active=false WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"SLA policy not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','sla_policy',%s)",(tenant_id,actor["id"],item_id))


@router.get("/teams")
def teams(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT t.*,
            COALESCE(array_agg(tm.user_id) FILTER (WHERE tm.user_id IS NOT NULL),'{}') AS member_user_ids,
            COALESCE(string_agg(u.display_name, ', ' ORDER BY u.display_name),'') AS member_names
            FROM cm_teams t LEFT JOIN cm_team_members tm ON tm.team_id=t.id AND tm.tenant_id=t.tenant_id
            LEFT JOIN cm_users u ON u.id=tm.user_id WHERE t.tenant_id=%s
            GROUP BY t.id ORDER BY t.name""",(tenant_id,)).fetchall())


@router.post("/teams",status_code=201)
def create_team(payload:TeamPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        groups=payload.whatsapp_group_ids or ([payload.whatsapp_group_id] if payload.whatsapp_group_id else [])
        row=c.execute("INSERT INTO cm_teams(tenant_id,name,emails,whatsapp_numbers,whatsapp_group_id,whatsapp_group_ids,active) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,[str(x) for x in payload.emails],payload.whatsapp_numbers,groups[0] if groups else None,groups,payload.active)).fetchone()
        for user_id in set(payload.member_user_ids):
            c.execute("INSERT INTO cm_team_members(tenant_id,team_id,user_id) SELECT %s,%s,%s WHERE EXISTS(SELECT 1 FROM cm_access_grants WHERE tenant_id=%s AND user_id=%s AND active)",(tenant_id,row["id"],user_id,tenant_id,user_id))
        return row


@router.put("/teams/{item_id}")
def update_team(item_id:str,payload:TeamPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        groups=payload.whatsapp_group_ids or ([payload.whatsapp_group_id] if payload.whatsapp_group_id else [])
        row=c.execute("UPDATE cm_teams SET name=%s,emails=%s,whatsapp_numbers=%s,whatsapp_group_id=%s,whatsapp_group_ids=%s,active=%s WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,[str(x) for x in payload.emails],payload.whatsapp_numbers,groups[0] if groups else None,groups,payload.active,tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Team not found")
        c.execute("DELETE FROM cm_team_members WHERE tenant_id=%s AND team_id=%s",(tenant_id,item_id))
        for user_id in set(payload.member_user_ids):
            c.execute("INSERT INTO cm_team_members(tenant_id,team_id,user_id) SELECT %s,%s,%s WHERE EXISTS(SELECT 1 FROM cm_access_grants WHERE tenant_id=%s AND user_id=%s AND active)",(tenant_id,item_id,user_id,tenant_id,user_id))
        return row


@router.delete("/teams/{item_id}",status_code=204)
def delete_team(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_teams SET active=false WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Team not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','team',%s)",(tenant_id,actor["id"],item_id))


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
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_notification_rules SET active=false WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Notification rule not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','notification_rule',%s)",(tenant_id,actor["id"],item_id))


@router.get("/notification-settings")
def notification_settings(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    return get_notification_settings(tenant_id)


@router.put("/notification-settings")
def update_notification_settings(payload:NotificationSettingsPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    if payload.smtp_enabled and (not payload.smtp_host or not payload.smtp_from_address):
        raise HTTPException(422,"Para habilitar o e-mail, informe o servidor SMTP e o e-mail remetente")
    if payload.evolution_enabled and (not payload.evolution_api_url or not payload.evolution_instance):
        raise HTTPException(422,"Para habilitar o WhatsApp, informe a URL da Evolution API e a instância")
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
    try: send_whatsapp(tenant_id,payload.target,"Teste Nexora: configuração de WhatsApp validada com sucesso.",require_homologation=False)
    except Exception as error: raise HTTPException(502,f"Falha no teste de WhatsApp: {error}") from error
    return {"message":"WhatsApp de teste enviado"}


@router.get("/notification-settings/whatsapp-connection")
def whatsapp_connection(qr:bool=False,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager"))
    try: return get_whatsapp_connection(tenant_id,request_qr=qr)
    except Exception as error: raise HTTPException(502,f"Falha ao consultar a Evolution API: {error}") from error


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
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_maintenance_windows SET active=false WHERE tenant_id=%s AND id=%s AND active RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Maintenance window not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','maintenance_window',%s)",(tenant_id,actor["id"],item_id))


@router.get("/users")
def users(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id,("admin","manager","auditor"))
    if actor["is_master"]:
        with connect() as c:
            result=list(c.execute("SELECT id,email,display_name,whatsapp_number,active,is_master FROM cm_users ORDER BY display_name").fetchall())
            tenant_ids=[str(row["id"]) for row in c.execute("SELECT id FROM cm_tenants WHERE active ORDER BY id").fetchall()]
        grants_by_user:dict[str,list[Any]]={str(row["id"]):[] for row in result}
        for scoped_tenant_id in tenant_ids:
            with tenant_connection(scoped_tenant_id) as tenant_db:
                for grant in tenant_db.execute("""SELECT DISTINCT user_id,tenant_id FROM cm_access_grants
                    WHERE tenant_id=%s AND active AND valid_from<=now()
                      AND (valid_until IS NULL OR valid_until>now())""",(scoped_tenant_id,)).fetchall():
                    grants_by_user.setdefault(str(grant["user_id"]),[]).append(grant["tenant_id"])
        return [{**row,"tenant_ids":grants_by_user.get(str(row["id"]),[])} for row in result]
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT u.id,u.email,u.display_name,u.whatsapp_number,u.active,false AS is_master,
            ARRAY[g.tenant_id] AS tenant_ids,g.role,g.row_scope,g.valid_until,
            COALESCE((SELECT array_agg(ud.department_id ORDER BY ud.department_id) FROM cm_user_departments ud
              WHERE ud.tenant_id=g.tenant_id AND ud.user_id=u.id),'{}'::uuid[]) AS department_ids
            FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id
            WHERE g.tenant_id=%s AND g.active AND g.valid_from<=now()
              AND (g.valid_until IS NULL OR g.valid_until>now()) ORDER BY u.display_name""",(tenant_id,)).fetchall())


@router.get("/access-grants")
def access_grants(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT g.*,u.email,u.display_name,e.name AS engagement_name,
            d.name AS department_name,t.name AS team_name
            FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id
            LEFT JOIN cm_service_engagements e ON e.id=g.engagement_id
            LEFT JOIN cm_departments d ON d.id=g.department_id
            LEFT JOIN cm_teams t ON t.id=g.team_id
            WHERE g.tenant_id=%s AND g.active ORDER BY u.display_name,g.created_at DESC""",(tenant_id,)).fetchall())


@router.post("/access-grants",status_code=201)
def create_access_grant(payload:AccessGrantPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        exists=c.execute("SELECT id FROM cm_users WHERE id=%s AND active",(payload.user_id,)).fetchone()
        if not exists: raise HTTPException(404,"User not found")
        if payload.engagement_id and not c.execute("SELECT id FROM cm_service_engagements WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.engagement_id)).fetchone():
            raise HTTPException(422,"Invalid service engagement")
        if payload.department_id and not c.execute("SELECT id FROM cm_departments WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.department_id)).fetchone():
            raise HTTPException(422,"Invalid department")
        if payload.team_id and not c.execute("SELECT id FROM cm_teams WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.team_id)).fetchone():
            raise HTTPException(422,"Invalid team")
        c.execute("UPDATE cm_access_grants SET active=false,updated_at=now(),authz_version=authz_version+1 WHERE tenant_id=%s AND user_id=%s AND active",(tenant_id,payload.user_id))
        row=c.execute("""INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,engagement_id,department_id,team_id,valid_until,granted_by,reason)
            SELECT workspace_id,id,%s,%s,%s,%s,%s,%s,%s,%s,%s FROM cm_tenants WHERE id=%s RETURNING *""",
            (payload.user_id,payload.role,payload.row_scope,payload.engagement_id,payload.department_id,payload.team_id,payload.valid_until,actor["id"],payload.reason,tenant_id)).fetchone()
        c.execute("""INSERT INTO cm_user_tenants(user_id,tenant_id,role) VALUES (%s,%s,%s)
            ON CONFLICT(user_id,tenant_id) DO UPDATE SET role=excluded.role""",(payload.user_id,tenant_id,payload.role))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'grant.create','access_grant',%s,%s)",(tenant_id,actor["id"],str(row["id"]),Jsonb(payload.model_dump(mode="json"))))
        return row


@router.get("/departments")
def departments(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("SELECT * FROM cm_departments WHERE tenant_id=%s ORDER BY active DESC,name",(tenant_id,)).fetchall())


@router.post("/departments",status_code=201)
def create_department(payload:DepartmentPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    try:
        with tenant_connection(tenant_id) as c:
            row=c.execute("INSERT INTO cm_departments(tenant_id,name,active) VALUES (%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.active)).fetchone()
            c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'department.create','department',%s)",(tenant_id,actor["id"],str(row["id"])))
            return row
    except UniqueViolation as error:
        raise HTTPException(409,"Department already exists") from error


@router.put("/departments/{department_id}")
def update_department(department_id:str,payload:DepartmentPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_departments SET name=%s,active=%s,updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.active,tenant_id,department_id)).fetchone()
        if not row: raise HTTPException(404,"Department not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'department.update','department',%s)",(tenant_id,actor["id"],department_id))
        return row


@router.put("/users/{user_id}/departments")
def update_user_departments(user_id:str,payload:UserDepartmentsPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    department_ids=list(dict.fromkeys(payload.department_ids))
    with tenant_connection(tenant_id) as c:
        valid={row["id"] for row in c.execute("SELECT id FROM cm_departments WHERE tenant_id=%s AND id=ANY(%s::uuid[]) AND active",(tenant_id,department_ids)).fetchall()} if department_ids else set()
        if len(valid)!=len(department_ids): raise HTTPException(422,"Invalid or inactive department")
        c.execute("DELETE FROM cm_user_departments WHERE tenant_id=%s AND user_id=%s",(tenant_id,user_id))
        for department_id in department_ids:
            c.execute("INSERT INTO cm_user_departments(tenant_id,user_id,department_id) VALUES (%s,%s,%s)",(tenant_id,user_id,department_id))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'user.departments.update','user',%s,%s)",(tenant_id,actor["id"],user_id,Jsonb({"department_ids":[str(item) for item in department_ids]})))
    return {"user_id":user_id,"department_ids":department_ids}


@router.delete("/access-grants/{grant_id}",status_code=204)
def revoke_access_grant(grant_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_access_grants SET active=false,updated_at=now(),authz_version=authz_version+1 WHERE tenant_id=%s AND id=%s AND active RETURNING user_id",(tenant_id,grant_id)).fetchone()
        if not row: raise HTTPException(404,"Access grant not found")
        if not c.execute("SELECT 1 FROM cm_access_grants WHERE tenant_id=%s AND user_id=%s AND active",(tenant_id,row["user_id"])).fetchone():
            c.execute("DELETE FROM cm_user_tenants WHERE tenant_id=%s AND user_id=%s",(tenant_id,row["user_id"]))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'grant.revoke','access_grant',%s)",(tenant_id,actor["id"],grant_id))


@router.get("/service-engagements")
def service_engagements(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT e.*,operator.name AS operator_company_name
            FROM cm_service_engagements e JOIN cm_tenants operator ON operator.id=e.operator_company_id
            WHERE e.tenant_id=%s ORDER BY e.active DESC,e.name""",(tenant_id,)).fetchall())


@router.post("/service-engagements",status_code=201)
def create_service_engagement(payload:ServiceEngagementPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        try:
            row=c.execute("""INSERT INTO cm_service_engagements(workspace_id,tenant_id,operator_company_id,kind,name,ends_at,active)
                SELECT customer.workspace_id,customer.id,operator.id,%s,%s,%s,%s
                FROM cm_tenants customer JOIN cm_tenants operator ON operator.workspace_id=customer.workspace_id
                WHERE customer.id=%s AND operator.id=%s RETURNING *""",
                (payload.kind,payload.name,payload.ends_at,payload.active,tenant_id,payload.operator_company_id)).fetchone()
        except UniqueViolation as error:
            raise HTTPException(409,"Active service engagement already exists") from error
        if not row: raise HTTPException(422,"Operator company must belong to the same workspace")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'engagement.create','service_engagement',%s)",(tenant_id,actor["id"],str(row["id"])))
        return row


@router.put("/users/{user_id}/access")
def update_user_access(user_id:str,payload:UserAccessPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    if not actor["is_master"]:
        raise HTTPException(403,"Master access required")
    target_ids=list(dict.fromkeys(str(item) for item in payload.tenant_ids))
    with connect() as c:
        target=c.execute("SELECT id,is_master FROM cm_users WHERE id=%s FOR UPDATE",(user_id,)).fetchone()
        if not target: raise HTTPException(404,"User not found")
        if payload.is_master:
            c.execute("UPDATE cm_users SET is_master=false WHERE is_master AND id<>%s",(user_id,))
            c.execute("UPDATE cm_users SET is_master=true WHERE id=%s",(user_id,))
        elif target["is_master"]:
            raise HTTPException(400,"Transfer master access to another user before demoting this user")
        all_tenant_ids=[str(row["id"]) for row in c.execute("SELECT id FROM cm_tenants ORDER BY id").fetchall()]
    for scoped_tenant_id in all_tenant_ids:
        with tenant_connection(scoped_tenant_id) as tenant_db:
            tenant_db.execute("UPDATE cm_access_grants SET active=false,updated_at=now(),authz_version=authz_version+1 WHERE tenant_id=%s AND user_id=%s AND active",(scoped_tenant_id,user_id))
            tenant_db.execute("DELETE FROM cm_user_tenants WHERE tenant_id=%s AND user_id=%s",(scoped_tenant_id,user_id))
    if not payload.is_master:
        for selected_tenant_id in target_ids:
            with tenant_connection(selected_tenant_id) as tenant_db:
                tenant_db.execute("""INSERT INTO cm_user_tenants(user_id,tenant_id,role)
                SELECT %s,id,'viewer' FROM cm_tenants WHERE id=%s
                ON CONFLICT(user_id,tenant_id) DO UPDATE SET role='viewer'""",(user_id,selected_tenant_id))
                tenant_db.execute("""INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,granted_by,reason)
                SELECT workspace_id,id,%s,'viewer','company',%s,'Acesso atualizado pelo Master'
                FROM cm_tenants WHERE id=%s""",(user_id,actor["id"],selected_tenant_id))
    with tenant_connection(tenant_id) as audit_db:
        audit_db.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'access.change','user',%s,%s)",
            (tenant_id,actor["id"],user_id,Jsonb({"is_master":payload.is_master,"tenant_ids":target_ids})))
    return {"id":user_id,"is_master":payload.is_master,"tenant_ids":target_ids}


@router.put("/users/{user_id}/notifications")
def update_user_notifications(user_id:str,payload:UserNotificationPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    with (connect() if actor["is_master"] else tenant_connection(tenant_id)) as c:
        row=c.execute("""UPDATE cm_users SET whatsapp_number=%s WHERE id=%s
            AND (%s OR EXISTS(SELECT 1 FROM cm_access_grants WHERE tenant_id=%s AND user_id=cm_users.id AND active))
            RETURNING id,email,display_name,whatsapp_number,active""",(payload.whatsapp_number or None,user_id,actor["is_master"],tenant_id)).fetchone()
        if not row: raise HTTPException(404,"User not found")
        return row


@router.put("/users/membership")
def update_membership(payload:MembershipPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("INSERT INTO cm_user_tenants(user_id,tenant_id,role) VALUES (%s,%s,%s) ON CONFLICT(user_id,tenant_id) DO UPDATE SET role=excluded.role RETURNING *",(payload.user_id,tenant_id,payload.role)).fetchone()
        c.execute("UPDATE cm_access_grants SET active=false,updated_at=now(),authz_version=authz_version+1 WHERE tenant_id=%s AND user_id=%s AND active",(tenant_id,payload.user_id))
        c.execute("""INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,granted_by,reason)
            SELECT workspace_id,id,%s,%s,'company',%s,'Perfil atualizado pela administração'
            FROM cm_tenants WHERE id=%s""",(payload.user_id,payload.role,actor["id"],tenant_id))
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'role.change','membership',%s,%s)",(tenant_id,actor["id"],str(payload.user_id),Jsonb({"role":payload.role})))
        return row


@router.patch("/users/{user_id}/active")
def toggle_user(user_id:str,active:bool,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin",))
    with (connect() if actor["is_master"] else tenant_connection(tenant_id)) as c:
        row=c.execute("UPDATE cm_users SET active=%s WHERE id=%s AND (%s OR EXISTS(SELECT 1 FROM cm_access_grants WHERE tenant_id=%s AND user_id=cm_users.id AND active)) RETURNING id,active",(active,user_id,actor["is_master"],tenant_id)).fetchone()
        if not row: raise HTTPException(404,"User not found")
        return row


@router.delete("/actions/grants/{item_id}",status_code=204)
def delete_action_grant(item_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_action_grants SET enabled=false WHERE tenant_id=%s AND id=%s AND enabled RETURNING id",(tenant_id,item_id)).fetchone()
        if not row: raise HTTPException(404,"Action grant not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'revoke','action_grant',%s)",(tenant_id,actor["id"],item_id))


@router.get("/audit-log")
def audit_log(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with tenant_connection(tenant_id) as c: return list(c.execute("SELECT l.*,u.display_name AS user_name FROM cm_audit_log l LEFT JOIN cm_users u ON u.id=l.user_id WHERE l.tenant_id=%s ORDER BY l.created_at DESC LIMIT 500",(tenant_id,)).fetchall())


@router.get("/integration-commands")
def integration_commands(state:str|None=None,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    # cm_outbox has FORCE ROW LEVEL SECURITY.  Always establish the tenant
    # context before reading it; filtering by tenant_id alone is not enough for
    # PostgreSQL to admit rows through the policy.
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT id,topic,state,state_reason,correlation_id,attempts,available_at,locked_at,worker_id,last_error,created_at,processed_at
            FROM cm_outbox WHERE tenant_id=%s AND (%s IS NULL OR state=%s)
            ORDER BY id DESC LIMIT 250""",(tenant_id,state,state)).fetchall())
        totals=list(c.execute("SELECT state,count(*) AS total FROM cm_outbox WHERE tenant_id=%s GROUP BY state ORDER BY state",(tenant_id,)).fetchall())
        metrics=c.execute("""SELECT count(*) FILTER(WHERE state IN ('pending','running','retry_wait')) AS queued,
          count(*) FILTER(WHERE state='uncertain') AS uncertain,count(*) FILTER(WHERE state='conflict') AS conflicts,
          count(*) FILTER(WHERE state='rejected') AS rejected,
          COALESCE(EXTRACT(EPOCH FROM(now()-(min(created_at) FILTER(WHERE state IN ('pending','running','retry_wait'))))),0)::bigint AS oldest_queue_seconds,
          COALESCE(EXTRACT(EPOCH FROM(now()-(max(processed_at) FILTER(WHERE state='succeeded')))),0)::bigint AS seconds_since_success
          FROM cm_outbox WHERE tenant_id=%s""",(tenant_id,)).fetchone()
    return {"items":rows,"totals":totals,"metrics":metrics,"data_as_of":datetime.now().astimezone()}


@router.post("/integration-commands/{command_id}/retry")
def retry_integration_command(command_id:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    from .feature_flags import require_feature
    with tenant_connection(tenant_id) as tenant_db:
        require_feature(tenant_db,tenant_id,"action:integration-command.retry")
    # Keep the retry and its audit entry in the same tenant-scoped transaction.
    # This is required by outbox RLS and also makes the audit atomic.
    with tenant_connection(tenant_id) as c:
        row=c.execute("""UPDATE cm_outbox SET state='pending',available_at=now(),processed_at=NULL,state_reason='manual_reprocess',
            locked_at=NULL,worker_id=NULL,last_error=NULL WHERE tenant_id=%s AND id=%s
            AND state IN ('retry_wait','rejected','uncertain','conflict') RETURNING id,topic,state,attempts,available_at""",(tenant_id,command_id)).fetchone()
        if not row: raise HTTPException(404,"Retryable integration command not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'command.retry','outbox',%s)",(tenant_id,actor["id"],str(command_id)))
        return row


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
    actor=authorize(identity,tenant_id);scope_sql,scope_params=incident_scope(actor)
    with tenant_connection(tenant_id) as c:
        totals=c.execute(f"""SELECT count(*) AS total,
        count(*) FILTER(WHERE status NOT IN ('resolved','closed','ignored')) AS open,
        count(*) FILTER(WHERE resolution_due_at<now() AND status NOT IN ('resolved','closed','ignored')) AS breached,
        round(avg(EXTRACT(EPOCH FROM (first_response_at-created_at))/60) FILTER(WHERE first_response_at IS NOT NULL),1) AS mtta_minutes,
        round(avg(EXTRACT(EPOCH FROM (resolved_at-created_at))/60) FILTER(WHERE resolved_at IS NOT NULL),1) AS mttr_minutes
        FROM cm_incidents i WHERE i.tenant_id=%s AND {scope_sql}""",(tenant_id,*scope_params)).fetchone()
        by_priority=list(c.execute(f"SELECT priority,count(*) AS total FROM cm_incidents i WHERE i.tenant_id=%s AND {scope_sql} GROUP BY priority ORDER BY priority",(tenant_id,*scope_params)).fetchall())
        suppliers=list(c.execute(f"SELECT s.name,count(i.id) FILTER(WHERE {scope_sql}) AS incidents,count(i.id) FILTER(WHERE {scope_sql} AND i.supplier_due_at<now() AND i.status='waiting_supplier') AS breached FROM cm_suppliers s LEFT JOIN cm_incidents i ON i.supplier_id=s.id WHERE s.tenant_id=%s GROUP BY s.id,s.name ORDER BY incidents DESC",(*scope_params,*scope_params,tenant_id)).fetchall())
    return {"totals":totals,"by_priority":by_priority,"suppliers":suppliers}


def _report_rows(tenant_id: str, actor: dict[str,Any]) -> list[dict[str, Any]]:
    scope_sql,scope_params=incident_scope(actor)
    with tenant_connection(tenant_id) as c:
        return list(c.execute(f"""SELECT i.id,i.title,i.priority,i.status,i.created_at,i.first_response_at,i.resolved_at,
        i.response_due_at,i.resolution_due_at,s.name AS supplier,tm.name AS team,u.display_name AS responsible
        FROM cm_incidents i LEFT JOIN cm_suppliers s ON s.id=i.supplier_id LEFT JOIN cm_teams tm ON tm.id=i.assignee_team_id
        LEFT JOIN cm_users u ON u.id=i.assignee_user_id WHERE i.tenant_id=%s AND {scope_sql} ORDER BY i.created_at DESC""",(tenant_id,*scope_params)).fetchall())


@router.get("/reports/export/{export_format}")
def export_report(export_format:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->Response:
    actor=authorize(identity,tenant_id)
    rows=_report_rows(tenant_id,actor)
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
