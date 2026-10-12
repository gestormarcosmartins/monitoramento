import csv
import io
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize
from .obligations import create_ticket_obligations
from .command_bus import enqueue_command
from .access_scope import incident_scope

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])

TicketStatus = Literal["new", "triaged", "assigned", "acknowledged", "in_progress", "waiting_customer", "waiting_supplier", "resolved", "closed", "cancelled", "duplicate"]

TRANSITIONS: dict[str, set[str]] = {
    "new": {"triaged", "assigned", "cancelled", "duplicate"},
    "triaged": {"assigned", "in_progress", "cancelled", "duplicate"},
    "assigned": {"acknowledged", "in_progress", "waiting_customer", "waiting_supplier", "cancelled", "duplicate"},
    "acknowledged": {"in_progress", "waiting_customer", "waiting_supplier", "resolved"},
    "in_progress": {"waiting_customer", "waiting_supplier", "resolved", "cancelled", "duplicate"},
    "waiting_customer": {"in_progress", "resolved", "cancelled"},
    "waiting_supplier": {"in_progress", "resolved", "cancelled"},
    "resolved": {"closed", "in_progress"},
    "closed": {"in_progress"},
    "cancelled": set(),
    "duplicate": set(),
}


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=20000)
    ticket_type: Literal["incident", "request"] = "incident"
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    category: str = Field(default="general", max_length=100)
    engagement_id: UUID | None = None
    service_code: str | None = Field(default=None, max_length=100)
    requester_user_id: UUID | None = None
    source_reference: str | None = Field(default=None, max_length=300)
    assignee_team_id: UUID | None = None
    assignee_user_id: UUID | None = None
    sla_policy_id: UUID | None = None


class TicketPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=240)
    description: str | None = Field(default=None, min_length=3, max_length=20000)
    priority: Literal["low", "medium", "high", "critical"] | None = None
    category: str | None = Field(default=None, max_length=100)
    assignee_user_id: UUID | None = None
    assignee_team_id: UUID | None = None
    sla_policy_id: UUID | None = None


class TicketListResponse(BaseModel):
    items: list[dict[str, Any]]
    page: int
    page_size: int
    total: int
    pages: int
    data_as_of: datetime
    sync_state: Literal["projected"] = "projected"
    filters_applied: dict[str, str | bool | None]


class EntryCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    visibility: Literal["public", "private"] = "public"


class EntryPatch(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    description: str | None = None
    assignee_user_id: UUID | None = None
    due_at: datetime | None = None


class TaskPatch(BaseModel):
    status: Literal["pending", "in_progress", "completed", "cancelled"]


class TransitionCreate(BaseModel):
    target_status: TicketStatus
    reason: str | None = Field(default=None, max_length=1000)


class SolutionCreate(BaseModel):
    solution_type: Literal["permanent", "workaround", "not_reproduced", "cancelled"]
    description: str = Field(min_length=3, max_length=20000)
    evidence: str | None = Field(default=None, max_length=20000)


class SolutionDecision(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)


class DuplicateCreate(BaseModel):
    canonical_ticket_id: UUID
    reason: str = Field(min_length=3, max_length=1000)


def _version_from_if_match(value: str | None) -> int:
    if not value:
        raise HTTPException(428, "If-Match header is required")
    try:
        return int(value.strip().strip('W/').strip('"'))
    except ValueError as error:
        raise HTTPException(400, "Invalid If-Match version") from error


def _enqueue(connection: Any, tenant_id: str, topic: str, incident_id: str, suffix: str, requested_by:str) -> None:
    enqueue_command(connection,tenant_id,topic,{"incident_id":incident_id},f"{topic}:{incident_id}:{suffix}",requested_by)


def require_ticket_access(connection: Any, actor: dict[str, Any], tenant_id: str, ticket_id: str, *, lock: bool = False) -> dict[str, Any]:
    """Authorize one ticket before any dependent read or mutation.

    Returning 404 avoids revealing that an inaccessible ticket exists.  This
    guard must run inside the same transaction as the write so a caller cannot
    bypass row_scope by addressing a record directly.
    """
    scope_sql, scope_params = incident_scope(actor)
    lock_sql = " FOR UPDATE" if lock else ""
    row = connection.execute(
        f"SELECT i.* FROM cm_incidents i WHERE i.tenant_id=%s AND i.id=%s AND i.inactive_at IS NULL AND {scope_sql}{lock_sql}",
        (tenant_id, ticket_id, *scope_params),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Ticket not found")
    return row


def ticket_filters(actor:dict[str,Any],tenant_id:str,*,q:str|None=None,ticket_status:str|None=None,priority:str|None=None,
    actionable:bool=False,team_id:UUID|str|None=None,assignee_user_id:UUID|str|None=None,mine:bool=False)->tuple[str,list[Any]]:
    """WHERE clause (alias ``i``) and params shared by the single-company and consolidated ticket lists."""
    scope_sql,scope_params=incident_scope(actor)
    filters=["i.tenant_id=%s","i.inactive_at IS NULL",scope_sql]
    params:list[Any]=[tenant_id,*scope_params]
    if q: filters.append("(i.title ILIKE %s OR i.description ILIKE %s OR CAST(i.glpi_ticket_id AS text)=%s)"); params += [f"%{q}%",f"%{q}%",q]
    if ticket_status: filters.append("i.status=%s"); params.append(ticket_status)
    if actionable: filters.append("i.status NOT IN ('resolved','closed','ignored')")
    if priority: filters.append("i.priority=%s"); params.append(priority)
    if team_id: filters.append("i.assignee_team_id=%s"); params.append(team_id)
    if assignee_user_id: filters.append("i.assignee_user_id=%s"); params.append(assignee_user_id)
    if mine:
        filters.append("(i.assignee_user_id=%s OR EXISTS(SELECT 1 FROM cm_team_members mine_tm WHERE mine_tm.tenant_id=i.tenant_id AND mine_tm.team_id=i.assignee_team_id AND mine_tm.user_id=%s))")
        params += [actor["id"],actor["id"]]
    return " AND ".join(filters),params


def ticket_summary_row(connection:Any,actor:dict[str,Any],tenant_id:str)->dict[str,Any]:
    """Counters of one company, narrowed by the actor's row scope. Caller owns the tenant connection."""
    scope_sql,scope_params=incident_scope(actor)
    return connection.execute(f"""SELECT count(*) FILTER(WHERE inactive_at IS NULL) AS total,
        count(*) FILTER(WHERE inactive_at IS NULL AND status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS open,
        count(*) FILTER(WHERE inactive_at IS NULL AND priority='critical' AND status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS critical,
        count(*) FILTER(WHERE inactive_at IS NULL AND resolution_due_at<now() AND status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS overdue,
        count(*) FILTER(WHERE inactive_at IS NULL AND assignee_user_id IS NULL AND assignee_team_id IS NULL AND status NOT IN ('resolved','closed','cancelled','duplicate','ignored')) AS unassigned,
        count(*) FILTER(WHERE inactive_at IS NULL AND sync_status='pending') AS pending_sync
        FROM cm_incidents i WHERE i.tenant_id=%s AND {scope_sql}""",(tenant_id,*scope_params)).fetchone()


@router.get("")
def list_tickets(q:str|None=None,ticket_status:str|None=Query(None,alias="status"),priority:str|None=None,actionable:bool=False,
    team_id:UUID|None=None,assignee_user_id:UUID|None=None,mine:bool=False,
    page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),tenant_id:str=Depends(tenant_header),
    identity:Identity=Depends(current_identity))->TicketListResponse:
    actor=authorize(identity,tenant_id)
    where,params=ticket_filters(actor,tenant_id,q=q,ticket_status=ticket_status,priority=priority,actionable=actionable,
        team_id=team_id,assignee_user_id=assignee_user_id,mine=mine)
    with tenant_connection(tenant_id) as c:
        total=c.execute(f"SELECT count(*) AS total FROM cm_incidents i WHERE {where}",tuple(params)).fetchone()["total"]
        items=list(c.execute(f"""SELECT i.*,u.display_name AS requester_name,team.name AS assignee_team_name,
            assignee.display_name AS assignee_name FROM cm_incidents i
            LEFT JOIN cm_users u ON u.id=i.requester_user_id LEFT JOIN cm_teams team ON team.id=i.assignee_team_id
            LEFT JOIN cm_users assignee ON assignee.id=i.assignee_user_id WHERE {where}
            ORDER BY i.updated_at DESC LIMIT %s OFFSET %s""",tuple(params+[page_size,(page-1)*page_size])).fetchall())
    applied_filters={"q": q, "status": ticket_status, "priority": priority, "actionable": actionable}
    if team_id: applied_filters["team_id"]=str(team_id)
    if assignee_user_id: applied_filters["assignee_user_id"]=str(assignee_user_id)
    if mine: applied_filters["mine"]="true"
    return TicketListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        pages=max(1, (total + page_size - 1) // page_size),
        data_as_of=datetime.now(UTC),
        sync_state="projected",
        filters_applied=applied_filters,
    )


@router.get("/summary")
def ticket_summary(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        row=ticket_summary_row(c,actor,tenant_id)
    return {**row,"data_as_of":datetime.now(UTC)}


@router.get("/export.csv")
def export_tickets(q:str|None=None,ticket_status:str|None=Query(None,alias="status"),priority:str|None=None,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->Response:
    actor=authorize(identity,tenant_id);scope_sql,scope_params=incident_scope(actor)
    filters=["i.tenant_id=%s","i.inactive_at IS NULL",scope_sql];params:list[Any]=[tenant_id,*scope_params]
    if q:filters.append("(title ILIKE %s OR description ILIKE %s OR CAST(glpi_ticket_id AS text)=%s)");params += [f"%{q}%",f"%{q}%",q]
    if ticket_status:filters.append("status=%s");params.append(ticket_status)
    if priority:filters.append("priority=%s");params.append(priority)
    with tenant_connection(tenant_id) as c:rows=list(c.execute(f"SELECT glpi_ticket_id,title,ticket_type,priority,status,sync_status,category,created_at,updated_at FROM cm_incidents i WHERE {' AND '.join(filters)} ORDER BY updated_at DESC",tuple(params)).fetchall())
    output=io.StringIO();writer=csv.writer(output,delimiter=";");writer.writerow(["glpi","titulo","tipo","prioridade","estado","sincronizacao","categoria","criado_em","atualizado_em"])
    for row in rows:writer.writerow([row[key] for key in ("glpi_ticket_id","title","ticket_type","priority","status","sync_status","category","created_at","updated_at")])
    return Response("\ufeff"+output.getvalue(),media_type="text/csv;charset=utf-8",headers={"Content-Disposition":'attachment; filename="chamados-conciliados.csv"',"X-Data-As-Of":datetime.now(UTC).isoformat()})


@router.post("",status_code=201)
def create_ticket(payload:TicketCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","client"))
    with tenant_connection(tenant_id) as c:
        if actor.get("row_scope")=="own" and payload.requester_user_id and str(payload.requester_user_id)!=str(actor["id"]):
            raise HTTPException(403,"Own scope can only create tickets for the authenticated user")
        if actor.get("row_scope")=="team_service":
            allowed_engagement=actor.get("engagement_id")
            if not allowed_engagement or str(payload.engagement_id or "")!=str(allowed_engagement):
                raise HTTPException(403,"Select the service engagement authorized by the access grant")
        if payload.engagement_id and not c.execute("SELECT 1 FROM cm_service_engagements WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.engagement_id)).fetchone():
            raise HTTPException(422,"Invalid service engagement")
        if payload.sla_policy_id and not c.execute("SELECT 1 FROM cm_sla_policies WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.sla_policy_id)).fetchone():
            raise HTTPException(422,"Invalid SLA policy")
        row=c.execute("""INSERT INTO cm_incidents(tenant_id,alert_id,status,title,description,priority,category,origin,
            ticket_type,requester_user_id,engagement_id,service_code,source_reference,assignee_team_id,assignee_user_id,sla_policy_id,sync_status)
            VALUES (%s,NULL,'new',%s,%s,%s,%s,'manual',%s,%s,%s,%s,%s,%s,%s,%s,'pending') RETURNING *""",
            (tenant_id,payload.title,payload.description,payload.priority,payload.category,payload.ticket_type,
             payload.requester_user_id or actor["id"],payload.engagement_id,payload.service_code,payload.source_reference,
             payload.assignee_team_id,payload.assignee_user_id,payload.sla_policy_id)).fetchone()
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'ticket.intent_created',%s)",(tenant_id,row["id"],actor["id"],Jsonb(payload.model_dump(mode="json"))))
        create_ticket_obligations(c,tenant_id,str(row["id"]),payload.priority)
        _enqueue(c,tenant_id,"incident.created",str(row["id"]),str(row["version"]),str(actor["id"]))
        return row


@router.get("/{ticket_id}")
def ticket_detail(ticket_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:
        restricted=actor["role"] in ("client","viewer")
        scope_sql,scope_params=incident_scope(actor)
        ticket_query=f"SELECT * FROM cm_incidents i WHERE i.tenant_id=%s AND i.id=%s AND i.inactive_at IS NULL AND {scope_sql}"
        ticket_params:list[Any]=[tenant_id,ticket_id,*scope_params]
        ticket=c.execute(ticket_query,tuple(ticket_params)).fetchone()
        if not ticket: raise HTTPException(404,"Ticket not found")
        visibility=" AND NOT is_internal" if restricted else ""
        entries=list(c.execute(f"SELECT * FROM cm_incident_comments WHERE tenant_id=%s AND incident_id=%s{visibility} ORDER BY created_at",(tenant_id,ticket_id)).fetchall())
        tasks=[] if restricted else list(c.execute("SELECT * FROM cm_ticket_tasks WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at",(tenant_id,ticket_id)).fetchall())
        solution_fields="id,solution_type,description,evidence,status,created_at" if restricted else "*"
        solutions=list(c.execute(f"SELECT {solution_fields} FROM cm_ticket_solutions WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at DESC",(tenant_id,ticket_id)).fetchall())
        history=[] if restricted else list(c.execute("SELECT * FROM cm_incident_history WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at DESC",(tenant_id,ticket_id)).fetchall())
        duplicate=None if restricted else c.execute("SELECT * FROM cm_ticket_duplicates WHERE tenant_id=%s AND incident_id=%s",(tenant_id,ticket_id)).fetchone()
    return {"ticket":ticket,"entries":entries,"tasks":tasks,"solutions":solutions,"history":history,"duplicate":duplicate,"data_as_of":datetime.now(UTC)}


@router.patch("/{ticket_id}")
def patch_ticket(ticket_id:str,payload:TicketPatch,if_match:str|None=Header(None,alias="If-Match"),tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator")); version=_version_from_if_match(if_match)
    fields=payload.model_dump(exclude_unset=True); allowed=set(TicketPatch.model_fields)
    if not fields: raise HTTPException(422,"No changes supplied")
    assignments=[]; params=[]
    for key,value in fields.items():
        if key in allowed: assignments.append(f"{key}=%s"); params.append(value)
    params += [tenant_id,ticket_id,version]
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        if payload.assignee_user_id:
            selected_team=payload.assignee_team_id
            if "assignee_team_id" not in payload.model_fields_set:
                existing=c.execute("SELECT assignee_team_id FROM cm_incidents WHERE tenant_id=%s AND id=%s",(tenant_id,ticket_id)).fetchone()
                selected_team=existing["assignee_team_id"] if existing else None
            if not selected_team: raise HTTPException(422,"Select a responsible team before assigning a technician")
            eligible_user=c.execute("""SELECT 1 FROM cm_access_grants g
                WHERE g.tenant_id=%s AND g.user_id=%s AND g.active AND g.valid_from<=now()
                  AND (g.valid_until IS NULL OR g.valid_until>now())
                  AND EXISTS(SELECT 1 FROM cm_team_members tm WHERE tm.tenant_id=g.tenant_id AND tm.team_id=%s AND tm.user_id=g.user_id)""",
                (tenant_id,payload.assignee_user_id,selected_team)).fetchone()
            if not eligible_user: raise HTTPException(422,"Assignee must have an active company grant and belong to the selected team")
        if payload.sla_policy_id and not c.execute("SELECT 1 FROM cm_sla_policies WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.sla_policy_id)).fetchone():
            raise HTTPException(422,"Invalid SLA policy")
        row=c.execute(f"UPDATE cm_incidents SET {','.join(assignments)},version=version+1,updated_at=now(),sync_status='pending' WHERE tenant_id=%s AND id=%s AND version=%s AND inactive_at IS NULL RETURNING *",tuple(params)).fetchone()
        if not row: raise HTTPException(409,"Ticket version conflict or not found")
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'ticket.updated',%s)",(tenant_id,ticket_id,actor["id"],Jsonb(payload.model_dump(mode="json",exclude_unset=True))))
        if payload.assignee_user_id:
            c.execute("""INSERT INTO cm_in_app_notifications(tenant_id,user_id,ticket_id,event,title,body)
                VALUES (%s,%s,%s,'ticket.assigned',%s,%s)
                ON CONFLICT(tenant_id,user_id,ticket_id,event) DO UPDATE SET body=excluded.body,read_at=NULL,created_at=now()""",
                (tenant_id,payload.assignee_user_id,ticket_id,"Novo chamado atribuído",row["title"]))
        _enqueue(c,tenant_id,"incident.status_changed",ticket_id,str(row["version"]),str(actor["id"]))
        return row


@router.post("/{ticket_id}/claim")
def claim_ticket(ticket_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        current=require_ticket_access(c,actor,tenant_id,ticket_id,lock=True)
        if current["status"] in ("resolved","closed","cancelled","duplicate","ignored"):
            raise HTTPException(409,"Closed tickets cannot be claimed")
        if current.get("assignee_user_id") and str(current["assignee_user_id"])!=str(actor["id"]):
            raise HTTPException(409,"Ticket is already assigned to another technician")
        team_id=current.get("assignee_team_id")
        if team_id and not c.execute("SELECT 1 FROM cm_team_members WHERE tenant_id=%s AND team_id=%s AND user_id=%s",(tenant_id,team_id,actor["id"])).fetchone():
            raise HTTPException(403,"You do not belong to the responsible team")
        target_status="in_progress" if current["status"] in ("new","triaged","assigned","acknowledged") else current["status"]
        row=c.execute("""UPDATE cm_incidents SET assignee_user_id=%s,status=%s,version=version+1,
            updated_at=now(),sync_status='pending' WHERE tenant_id=%s AND id=%s RETURNING *""",
            (actor["id"],target_status,tenant_id,ticket_id)).fetchone()
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'ticket.claimed',%s)",(tenant_id,ticket_id,actor["id"],Jsonb({"from":current["status"],"to":target_status})))
        c.execute("""INSERT INTO cm_in_app_notifications(tenant_id,user_id,ticket_id,event,title,body)
            VALUES (%s,%s,%s,'ticket.assigned',%s,%s)
            ON CONFLICT(tenant_id,user_id,ticket_id,event) DO UPDATE SET body=excluded.body,read_at=NULL,created_at=now()""",
            (tenant_id,actor["id"],ticket_id,"Chamado atribuído a você",row["title"]))
        _enqueue(c,tenant_id,"incident.status_changed",ticket_id,str(row["version"]),str(actor["id"]))
        return row


@router.post("/{ticket_id}/entries",status_code=201)
def create_entry(ticket_id:str,payload:EntryCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","client"))
    if payload.visibility=="private" and actor["role"]=="client": raise HTTPException(403,"Private notes are not available to clients")
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("INSERT INTO cm_incident_comments(tenant_id,incident_id,author_id,body,is_internal) SELECT %s,id,%s,%s,%s FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL RETURNING *",(tenant_id,actor["id"],payload.body,payload.visibility=="private",tenant_id,ticket_id)).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        if payload.visibility=="public" and actor["role"] in ("master","admin","manager","operator"):
            c.execute("UPDATE cm_incidents SET first_response_at=COALESCE(first_response_at,now()) WHERE tenant_id=%s AND id=%s",(tenant_id,ticket_id))
            c.execute("UPDATE cm_ticket_obligations SET state='met',completed_at=COALESCE(completed_at,now()),updated_at=now() WHERE tenant_id=%s AND ticket_id=%s AND kind='first_response' AND state IN ('running','paused')",(tenant_id,ticket_id))
        _enqueue(c,tenant_id,"incident.comment_created",ticket_id,str(row["id"]),str(actor["id"]))
        return row


@router.patch("/{ticket_id}/entries/{entry_id}")
def update_entry(ticket_id:str,entry_id:str,payload:EntryPatch,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","client"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("""UPDATE cm_incident_comments SET body=%s,updated_at=now(),edited_by=%s
            WHERE tenant_id=%s AND incident_id=%s AND id=%s AND (author_id=%s OR %s)
            RETURNING *""",(payload.body,actor["id"],tenant_id,ticket_id,entry_id,actor["id"],actor["role"] in ("master","admin","manager"))).fetchone()
        if not row: raise HTTPException(404,"Editable comment not found")
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'ticket.comment_edited',%s)",(tenant_id,ticket_id,actor["id"],Jsonb({"comment_id":entry_id})))
        return row


@router.post("/{ticket_id}/tasks",status_code=201)
def create_task(ticket_id:str,payload:TaskCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("""INSERT INTO cm_ticket_tasks(tenant_id,incident_id,title,description,assignee_user_id,due_at,created_by)
            SELECT %s,id,%s,%s,%s,%s,%s FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL RETURNING *""",
            (tenant_id,payload.title,payload.description,payload.assignee_user_id,payload.due_at,actor["id"],tenant_id,ticket_id)).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        return row


@router.patch("/{ticket_id}/tasks/{task_id}")
def patch_task(ticket_id:str,task_id:str,payload:TaskPatch,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("UPDATE cm_ticket_tasks SET status=%s,completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END,updated_at=now() WHERE tenant_id=%s AND incident_id=%s AND id=%s RETURNING *",(payload.status,payload.status,tenant_id,ticket_id,task_id)).fetchone()
        if not row: raise HTTPException(404,"Task not found")
        return row


@router.post("/{ticket_id}/transitions")
def transition_ticket(ticket_id:str,payload:TransitionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","client"))
    with tenant_connection(tenant_id) as c:
        current=require_ticket_access(c,actor,tenant_id,ticket_id,lock=True)
        if payload.target_status not in TRANSITIONS.get(current["status"],set()): raise HTTPException(409,f"Transition {current['status']} -> {payload.target_status} is not allowed")
        if actor["role"]=="client" and not (current["status"]=="resolved" and payload.target_status in ("closed","in_progress")): raise HTTPException(403,"Client can only accept or reject a proposed solution")
        if payload.target_status=="resolved":
            pending=c.execute("SELECT count(*) AS total FROM cm_ticket_tasks WHERE tenant_id=%s AND incident_id=%s AND status IN ('pending','in_progress')",(tenant_id,ticket_id)).fetchone()["total"]
            solution=c.execute("SELECT 1 FROM cm_ticket_solutions WHERE tenant_id=%s AND incident_id=%s AND status='proposed'",(tenant_id,ticket_id)).fetchone()
            if pending or not solution: raise HTTPException(409,"Complete pending tasks and propose a solution before resolving")
        row=c.execute("""UPDATE cm_incidents SET status=%s,version=version+1,updated_at=now(),sync_status='pending',
            resolved_at=CASE WHEN %s='resolved' THEN now() WHEN %s='in_progress' THEN NULL ELSE resolved_at END,
            closed_at=CASE WHEN %s='closed' THEN now() ELSE closed_at END WHERE tenant_id=%s AND id=%s RETURNING *""",
            (payload.target_status,payload.target_status,payload.target_status,payload.target_status,tenant_id,ticket_id)).fetchone()
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'ticket.transitioned',%s)",(tenant_id,ticket_id,actor["id"],Jsonb({"from":current["status"],"to":payload.target_status,"reason":payload.reason})))
        _enqueue(c,tenant_id,"incident.status_changed",ticket_id,str(row["version"]),str(actor["id"]))
        return row


@router.post("/{ticket_id}/solutions",status_code=201)
def propose_solution(ticket_id:str,payload:SolutionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        c.execute("UPDATE cm_ticket_solutions SET status='superseded' WHERE tenant_id=%s AND incident_id=%s AND status='proposed'",(tenant_id,ticket_id))
        row=c.execute("INSERT INTO cm_ticket_solutions(tenant_id,incident_id,solution_type,description,evidence,proposed_by) SELECT %s,id,%s,%s,%s,%s FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL RETURNING *",(tenant_id,payload.solution_type,payload.description,payload.evidence,actor["id"],tenant_id,ticket_id)).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        return row


@router.post("/{ticket_id}/solutions/{solution_id}/decision")
def decide_solution(ticket_id:str,solution_id:str,payload:SolutionDecision,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator","client"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("UPDATE cm_ticket_solutions SET status=%s,validated_by=%s,validated_at=now() WHERE tenant_id=%s AND incident_id=%s AND id=%s AND status='proposed' RETURNING *",(payload.decision,actor["id"],tenant_id,ticket_id,solution_id)).fetchone()
        if not row: raise HTTPException(404,"Proposed solution not found")
        target="closed" if payload.decision=="accepted" else "in_progress"
        c.execute("UPDATE cm_incidents SET status=%s,version=version+1,updated_at=now(),closed_at=CASE WHEN %s='closed' THEN now() ELSE NULL END WHERE tenant_id=%s AND id=%s AND status='resolved'",(target,target,tenant_id,ticket_id))
        c.execute("INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'solution.decided',%s)",(tenant_id,ticket_id,actor["id"],Jsonb(payload.model_dump())))
        return row


@router.post("/{ticket_id}/duplicate")
def mark_duplicate(ticket_id:str,payload:DuplicateCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->dict[str,Any]:
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        try:
            require_ticket_access(c,actor,tenant_id,str(payload.canonical_ticket_id))
        except HTTPException as error:
            raise HTTPException(422,"Canonical ticket not found") from error
        row=c.execute("INSERT INTO cm_ticket_duplicates(tenant_id,incident_id,canonical_incident_id,linked_by,reason) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,incident_id) DO UPDATE SET canonical_incident_id=excluded.canonical_incident_id,linked_by=excluded.linked_by,reason=excluded.reason,created_at=now() RETURNING *",(tenant_id,ticket_id,payload.canonical_ticket_id,actor["id"],payload.reason)).fetchone()
        c.execute("UPDATE cm_incidents SET status='duplicate',version=version+1,updated_at=now() WHERE tenant_id=%s AND id=%s",(tenant_id,ticket_id))
        return row


@router.delete("/{ticket_id}",status_code=status.HTTP_204_NO_CONTENT)
def inactivate_ticket(ticket_id:str,reason:str=Query(...,min_length=3,max_length=1000),tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity))->None:
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:
        require_ticket_access(c,actor,tenant_id,ticket_id)
        row=c.execute("UPDATE cm_incidents SET inactive_at=now(),inactive_reason=%s,updated_at=now() WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL RETURNING id",(reason,tenant_id,ticket_id)).fetchone()
        if not row: raise HTTPException(404,"Ticket not found")
        c.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'ticket.inactivate','ticket',%s,%s)",(tenant_id,actor["id"],ticket_id,Jsonb({"reason":reason})))
