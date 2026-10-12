from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize

router=APIRouter(prefix="/api/v1",tags=["service-operations"])


class QueuePayload(BaseModel):
    name:str=Field(min_length=2,max_length=160)
    description:str|None=Field(default=None,max_length=1000)
    team_id:UUID|None=None
    active:bool=True


class TimeEntryCreate(BaseModel):
    ticket_id:UUID
    task_id:UUID|None=None
    description:str=Field(min_length=3,max_length=2000)
    started_at:datetime
    ended_at:datetime|None=None
    minutes:int|None=Field(default=None,ge=1,le=1440)

    @model_validator(mode="after")
    def valid_time(self):
        if self.ended_at and self.ended_at<=self.started_at: raise ValueError("ended_at must be after started_at")
        if not self.ended_at and not self.minutes: raise ValueError("ended_at or minutes is required")
        return self


class SlaVersionCreate(BaseModel):
    timezone:str=Field(default="America/Sao_Paulo",min_length=3,max_length=100)
    response_minutes:int=Field(ge=1)
    resolution_minutes:int=Field(ge=1)
    coverage:Literal["24x7","business_hours"]="24x7"
    business_start:str="08:00"
    business_end:str="18:00"
    business_days:list[int]=Field(default_factory=lambda:[1,2,3,4,5])
    pause_states:list[Literal["waiting_customer","waiting_supplier"]]=Field(default_factory=lambda:["waiting_customer"])
    effective_from:datetime
    effective_until:datetime|None=None


class NotificationPreferencePayload(BaseModel):
    email_enabled:bool=True
    whatsapp_enabled:bool=False
    in_app_enabled:bool=True
    muted_events:list[str]=Field(default_factory=list)


@router.get("/queues")
def queues(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT q.*,t.name AS team_name,count(i.id) FILTER(WHERE i.status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS backlog
            FROM cm_queues q LEFT JOIN cm_teams t ON t.id=q.team_id LEFT JOIN cm_incidents i ON i.queue_id=q.id AND i.tenant_id=q.tenant_id
            WHERE q.tenant_id=%s GROUP BY q.id,t.name ORDER BY q.name""",(tenant_id,)).fetchall())


@router.post("/queues",status_code=201)
def create_queue(payload:QueuePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    try:
        with tenant_connection(tenant_id) as c:
            if payload.team_id and not c.execute("SELECT 1 FROM cm_teams WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.team_id)).fetchone():
                raise HTTPException(422,"Responsible team must be active in the selected company")
            row=c.execute("INSERT INTO cm_queues(tenant_id,name,description,team_id,active) VALUES (%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.description,payload.team_id,payload.active)).fetchone()
            c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'queue.created','queue',%s,%s)",(tenant_id,actor["id"],str(row["id"]),Jsonb({"name":payload.name,"team_id":str(payload.team_id) if payload.team_id else None})))
            return row
    except UniqueViolation as error:raise HTTPException(409,"Queue name already exists in this company") from error


@router.put("/queues/{queue_id}")
def update_queue(queue_id:str,payload:QueuePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    try:
        with tenant_connection(tenant_id) as c:
            if payload.team_id and not c.execute("SELECT 1 FROM cm_teams WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.team_id)).fetchone():
                raise HTTPException(422,"Responsible team must be active in the selected company")
            row=c.execute("UPDATE cm_queues SET name=%s,description=%s,team_id=%s,active=%s,updated_at=now() WHERE tenant_id=%s AND id=%s RETURNING *",(payload.name,payload.description,payload.team_id,payload.active,tenant_id,queue_id)).fetchone()
            if not row: raise HTTPException(404,"Queue not found")
            c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'queue.updated','queue',%s,%s)",(tenant_id,actor["id"],queue_id,Jsonb(payload.model_dump(mode="json"))))
            return row
    except UniqueViolation as error:raise HTTPException(409,"Queue name already exists in this company") from error


@router.get("/my-queue")
def my_queue(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        items=list(c.execute("""SELECT i.*,q.name AS queue_name,t.name AS team_name,
            CASE WHEN i.resolution_due_at<now() THEN 'breached' WHEN i.resolution_due_at<now()+interval '1 hour' THEN 'at_risk' ELSE 'on_track' END AS sla_state
            FROM cm_incidents i LEFT JOIN cm_queues q ON q.id=i.queue_id LEFT JOIN cm_teams t ON t.id=i.assignee_team_id
            WHERE i.tenant_id=%s AND i.inactive_at IS NULL AND i.status NOT IN ('resolved','closed','cancelled','duplicate','ignored')
              AND (i.assignee_user_id=%s OR EXISTS(SELECT 1 FROM cm_team_members tm WHERE tm.tenant_id=i.tenant_id AND tm.team_id=i.assignee_team_id AND tm.user_id=%s))
            ORDER BY CASE i.priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,i.resolution_due_at NULLS LAST,i.created_at""",(tenant_id,actor["id"],actor["id"])).fetchall())
        tasks=list(c.execute("""SELECT task.*,i.title AS ticket_title FROM cm_ticket_tasks task JOIN cm_incidents i ON i.id=task.incident_id
            WHERE task.tenant_id=%s AND task.assignee_user_id=%s AND task.status IN ('pending','in_progress') ORDER BY task.due_at NULLS LAST""",(tenant_id,actor["id"])).fetchall())
    return {"tickets":items,"tasks":tasks,"data_as_of":datetime.now(UTC)}


@router.get("/operations/workload")
def workload(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin","manager","operator","auditor"))
    with tenant_connection(tenant_id) as c:
        users=list(c.execute("""SELECT u.id,u.display_name,u.email,count(DISTINCT i.id) FILTER(WHERE i.status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS open_tickets,
            count(DISTINCT i.id) FILTER(WHERE i.status IN ('resolved','closed') AND i.resolved_at>=date_trunc('month',now())) AS resolved_tickets_month,
            round(avg(extract(epoch FROM (i.first_response_at-i.created_at))/60) FILTER(WHERE i.first_response_at IS NOT NULL)) AS avg_first_response_minutes,
            round(avg(extract(epoch FROM (i.resolved_at-i.created_at))/60) FILTER(WHERE i.resolved_at IS NOT NULL)) AS avg_resolution_minutes,
            count(DISTINCT task.id) FILTER(WHERE task.status IN ('pending','in_progress')) AS pending_tasks,
            COALESCE(sum(te.minutes) FILTER(WHERE te.state='confirmed' AND te.started_at>=date_trunc('month',now())),0) AS confirmed_minutes_month
            FROM cm_access_grants g JOIN cm_users u ON u.id=g.user_id
            LEFT JOIN cm_incidents i ON i.tenant_id=g.tenant_id AND i.assignee_user_id=u.id
            LEFT JOIN cm_ticket_tasks task ON task.tenant_id=g.tenant_id AND task.assignee_user_id=u.id
            LEFT JOIN cm_time_entries te ON te.tenant_id=g.tenant_id AND te.user_id=u.id
            WHERE g.tenant_id=%s AND g.active GROUP BY u.id ORDER BY open_tickets DESC,u.display_name""",(tenant_id,)).fetchall())
    return {"users":users,"data_as_of":datetime.now(UTC)}


@router.get("/notifications")
def in_app_notifications(unread_only:bool=False,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        rows=list(c.execute("""SELECT * FROM cm_in_app_notifications WHERE tenant_id=%s AND user_id=%s
            AND (%s=false OR read_at IS NULL) ORDER BY created_at DESC LIMIT 100""",(tenant_id,actor["id"],unread_only)).fetchall())
    return {"items":rows,"unread":sum(1 for item in rows if item["read_at"] is None),"data_as_of":datetime.now(UTC)}


@router.post("/notifications/{notification_id}/read")
def read_in_app_notification(notification_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_in_app_notifications SET read_at=COALESCE(read_at,now()) WHERE tenant_id=%s AND user_id=%s AND id=%s RETURNING *",(tenant_id,actor["id"],notification_id)).fetchone()
        if not row: raise HTTPException(404,"Notification not found")
        return row


@router.get("/time-entries")
def time_entries(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return list(c.execute("""SELECT te.*,i.title AS ticket_title,t.title AS task_title,u.display_name AS user_name
            FROM cm_time_entries te JOIN cm_incidents i ON i.id=te.ticket_id LEFT JOIN cm_ticket_tasks t ON t.id=te.task_id JOIN cm_users u ON u.id=te.user_id
            WHERE te.tenant_id=%s AND (%s OR te.user_id=%s) ORDER BY te.started_at DESC""",(tenant_id,actor["role"] in ("master","admin","manager","auditor"),actor["id"])).fetchall())


@router.post("/time-entries",status_code=201)
def create_time_entry(payload:TimeEntryCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    minutes=payload.minutes or max(1,int((payload.ended_at-payload.started_at).total_seconds()/60))
    with tenant_connection(tenant_id) as c:
        if payload.task_id and not c.execute("SELECT 1 FROM cm_ticket_tasks WHERE tenant_id=%s AND incident_id=%s AND id=%s",(tenant_id,payload.ticket_id,payload.task_id)).fetchone(): raise HTTPException(422,"Task does not belong to ticket")
        row=c.execute("""INSERT INTO cm_time_entries(tenant_id,ticket_id,task_id,user_id,description,started_at,ended_at,minutes,state)
            SELECT %s,id,%s,%s,%s,%s,%s,%s,'draft' FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL RETURNING *""",
            (tenant_id,payload.task_id,actor["id"],payload.description,payload.started_at,payload.ended_at,minutes,tenant_id,payload.ticket_id)).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        return row


@router.post("/time-entries/{entry_id}/submit")
def submit_time_entry(entry_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_time_entries SET state='pending_glpi',updated_at=now() WHERE tenant_id=%s AND id=%s AND user_id=%s AND state='draft' RETURNING *",(tenant_id,entry_id,actor["id"])).fetchone()
        if not row: raise HTTPException(409,"Only the author can submit a draft entry")
        c.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key,requested_by) VALUES (%s,'time_entry.requested',%s,%s,%s)",(tenant_id,Jsonb({"entry_id":entry_id}),f"time_entry.requested:{entry_id}",actor["id"]))
        return row


@router.get("/sla-policies/{policy_id}/versions")
def sla_versions(policy_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->list[dict[str,Any]]:
    authorize(identity,tenant_id,("admin","manager","auditor"))
    with tenant_connection(tenant_id) as c: return list(c.execute("SELECT * FROM cm_sla_policy_versions WHERE tenant_id=%s AND policy_id=%s ORDER BY version DESC",(tenant_id,policy_id)).fetchall())


@router.post("/sla-policies/{policy_id}/versions",status_code=201)
def create_sla_version(policy_id:str,payload:SlaVersionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager"))
    if any(day<1 or day>7 for day in payload.business_days): raise HTTPException(422,"business_days must use ISO values 1 to 7")
    with tenant_connection(tenant_id) as c:
        row=c.execute("""INSERT INTO cm_sla_policy_versions(tenant_id,policy_id,version,timezone,response_minutes,resolution_minutes,coverage,business_start,business_end,business_days,pause_states,effective_from,effective_until,created_by)
            SELECT %s,id,COALESCE((SELECT max(version)+1 FROM cm_sla_policy_versions WHERE tenant_id=%s AND policy_id=%s),1),%s,%s,%s,%s,%s::time,%s::time,%s,%s,%s,%s,%s
            FROM cm_sla_policies WHERE tenant_id=%s AND id=%s RETURNING *""",
            (tenant_id,tenant_id,policy_id,payload.timezone,payload.response_minutes,payload.resolution_minutes,payload.coverage,payload.business_start,payload.business_end,payload.business_days,payload.pause_states,payload.effective_from,payload.effective_until,actor["id"],tenant_id,policy_id)).fetchone()
        if not row: raise HTTPException(404,"SLA policy not found")
        return row


@router.post("/sla-policies/{policy_id}/versions/{version}/publish")
def publish_sla_version(policy_id:str,version:int,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_sla_policy_versions SET state='published',published_at=now() WHERE tenant_id=%s AND policy_id=%s AND version=%s AND state='draft' RETURNING *",(tenant_id,policy_id,version)).fetchone()
        if not row: raise HTTPException(409,"Only draft SLA versions can be published")
        return row


@router.get("/notification-preferences")
def notification_preferences(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return c.execute("SELECT * FROM cm_notification_preferences WHERE tenant_id=%s AND user_id=%s",(tenant_id,actor["id"])).fetchone() or {"tenant_id":tenant_id,"user_id":actor["id"],"email_enabled":True,"whatsapp_enabled":False,"in_app_enabled":True,"muted_events":[]}


@router.put("/notification-preferences")
def update_notification_preferences(payload:NotificationPreferencePayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        return c.execute("""INSERT INTO cm_notification_preferences(tenant_id,user_id,email_enabled,whatsapp_enabled,in_app_enabled,muted_events)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,user_id) DO UPDATE SET email_enabled=excluded.email_enabled,whatsapp_enabled=excluded.whatsapp_enabled,in_app_enabled=excluded.in_app_enabled,muted_events=excluded.muted_events,updated_at=now() RETURNING *""",
            (tenant_id,actor["id"],payload.email_enabled,payload.whatsapp_enabled,payload.in_app_enabled,payload.muted_events)).fetchone()
