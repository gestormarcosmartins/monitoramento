from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .db import tenant_connection
from .operations import authorize

router=APIRouter(prefix="/api/v1",tags=["governance"])
ALLOWED_RUNBOOK_STEP_TYPES={"http_request","restart_service","collect_diagnostics","wait","verify_monitor"}
TRANSITIONS={
    "knowledge":{"draft":{"review"},"review":{"published","draft"},"published":{"retired"},"retired":set()},
    "problem":{"investigating":{"root_cause_known","known_error","resolved"},"root_cause_known":{"known_error","resolved"},"known_error":{"resolved"},"resolved":{"closed","investigating"},"closed":set()},
    "change":{"draft":{"review","cancelled"},"review":{"approved","draft","cancelled"},"approved":{"scheduled","cancelled"},"scheduled":{"running","cancelled"},"running":{"validating","rolled_back"},"validating":{"completed","rolled_back"},"completed":set(),"rolled_back":set(),"cancelled":set()},
    "runbook":{"draft":{"review"},"review":{"published","draft"},"published":{"retired"},"retired":set()},
}


def require_transition(kind:str,current:str,target:str)->None:
    if target not in TRANSITIONS.get(kind,{}).get(current,set()):raise ValueError(f"Invalid {kind} transition: {current} -> {target}")


def validate_runbook_steps(steps:list[dict[str,Any]])->None:
    for step in steps:
        if step.get("type") not in ALLOWED_RUNBOOK_STEP_TYPES:
            raise ValueError("Runbook step is not in the closed action catalog")
        serialized=str(step).lower()
        if any(token in serialized for token in ("shell","powershell","cmd.exe","/bin/sh")):
            raise ValueError("Arbitrary shell is forbidden")


def validate_parameters(schema:dict[str,Any],values:dict[str,Any])->None:
    properties=schema.get("properties",{});required=schema.get("required",[])
    missing=[key for key in required if key not in values]
    if missing:raise ValueError(f"Missing required parameters: {', '.join(missing)}")
    python_types={"string":str,"integer":int,"number":(int,float),"boolean":bool,"object":dict,"array":list}
    for key,value in values.items():
        if key not in properties:raise ValueError(f"Unknown parameter: {key}")
        expected=python_types.get(properties[key].get("type"))
        if expected and (not isinstance(value,expected) or isinstance(value,bool) and properties[key].get("type") in ("integer","number")):
            raise ValueError(f"Invalid type for parameter: {key}")


class FeatureFlagPayload(BaseModel):
    mode:Literal["off","shadow","write_pilot","enabled"]
    reason:str=Field(min_length=3,max_length=1000)


class KnowledgeCreate(BaseModel):
    title:str=Field(min_length=3,max_length=240);content:str=Field(min_length=3,max_length=30000)
    visibility:Literal["internal","customer","public"]="internal";acl_roles:list[str]=Field(default_factory=lambda:["admin","manager","operator"])
    glpi_article_id:int|None=None


class ProblemCreate(BaseModel):
    title:str=Field(min_length=3,max_length=240);description:str=Field(min_length=3,max_length=10000);hypothesis:str|None=None;workaround:str|None=None


class ChangeCreate(BaseModel):
    title:str=Field(min_length=3,max_length=240);change_type:Literal["standard","normal","emergency"];risk:Literal["low","medium","high","critical"]
    implementation_plan:str=Field(min_length=3,max_length=20000);validation_plan:str=Field(min_length=3,max_length=20000);rollback_plan:str=Field(min_length=3,max_length=20000)
    window_start:datetime|None=None;window_end:datetime|None=None


class RunbookCreate(BaseModel):
    code:str=Field(pattern=r"^[A-Z0-9_.-]+$",max_length=80);name:str=Field(min_length=3,max_length=240);risk:Literal["low","medium","high","critical"]
    parameters_schema:dict[str,Any]=Field(default_factory=dict);target_selector:dict[str,Any]=Field(default_factory=dict)
    preconditions:list[dict[str,Any]]=Field(default_factory=list);steps:list[dict[str,Any]];postconditions:list[dict[str,Any]]=Field(default_factory=list);lock_key:str=Field(min_length=1,max_length=200);approval_required:bool=True


class StatusPayload(BaseModel):
    status:str;reason:str=Field(min_length=3,max_length=1000)


class ProblemUpdate(BaseModel):
    status:Literal["investigating","root_cause_known","known_error","resolved","closed"]
    hypothesis:str|None=None;root_cause:str|None=None;workaround:str|None=None;reason:str=Field(min_length=3,max_length=1000)


class RunbookExecutionCreate(BaseModel):
    parameters:dict[str,Any]=Field(default_factory=dict);target_asset_id:str|None=None


class OncallScheduleCreate(BaseModel):
    name:str=Field(min_length=3,max_length=200);timezone:str="America/Sao_Paulo";recurrence_rule:str=Field(min_length=6,max_length=500)
    starts_at:datetime;ends_at:datetime|None=None;team_id:str|None=None


class OncallSubstitutionCreate(BaseModel):
    original_user_id:str;substitute_user_id:str;starts_at:datetime;ends_at:datetime;reason:str=Field(min_length=3,max_length=1000)


def transition(c:Any,tenant_id:str,kind:str,table:str,object_id:str,target:str,actor_id:str,reason:str)->dict[str,Any]:
    row=c.execute(f"SELECT * FROM {table} WHERE tenant_id=%s AND id=%s FOR UPDATE",(tenant_id,object_id)).fetchone()
    if not row:raise HTTPException(404,f"{kind.title()} not found")
    try:require_transition(kind,row["status"],target)
    except ValueError as error:raise HTTPException(409,str(error)) from error
    approved=",approved_by=%s,approved_at=now()" if kind=="change" and target=="approved" else ""
    params=(target,actor_id,tenant_id,object_id) if approved else (target,tenant_id,object_id)
    updated=c.execute(f"UPDATE {table} SET status=%s,updated_at=now(){approved} WHERE tenant_id=%s AND id=%s RETURNING *",params).fetchone()
    c.execute("INSERT INTO cm_governance_history(tenant_id,object_type,object_id,from_status,to_status,actor_id,details) VALUES (%s,%s,%s,%s,%s,%s,%s)",(tenant_id,kind,object_id,row["status"],target,actor_id,Jsonb({"reason":reason})))
    return updated


@router.get("/feature-flags")
def flags(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id); 
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_feature_flags WHERE tenant_id=%s ORDER BY feature",(tenant_id,)).fetchall())


@router.put("/feature-flags/{feature}")
def set_flag(feature:str,payload:FeatureFlagPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin",))
    with tenant_connection(tenant_id) as c:return c.execute("""INSERT INTO cm_feature_flags(tenant_id,feature,mode,approved_by,reason) VALUES (%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,feature) DO UPDATE SET mode=excluded.mode,approved_by=excluded.approved_by,reason=excluded.reason,updated_at=now() RETURNING *""",(tenant_id,feature,payload.mode,actor["id"],payload.reason)).fetchone()


@router.get("/knowledge")
def knowledge(q:str|None=Query(default=None,max_length=200),tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id);role=actor.get("role","viewer")
    search=(q or "").strip()
    with tenant_connection(tenant_id) as c:
        return list(c.execute(
            """SELECT id,title,visibility,status,version,updated_at
            FROM cm_knowledge_articles
            WHERE tenant_id=%s
              AND (%s='master' OR %s=ANY(acl_roles))
              AND (%s='' OR title ILIKE %s OR content ILIKE %s)
            ORDER BY updated_at DESC""",
            (tenant_id,role,role,search,f"%{search}%",f"%{search}%"),
        ).fetchall())


@router.post("/knowledge",status_code=201)
def create_knowledge(payload:KnowledgeCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:return c.execute("INSERT INTO cm_knowledge_articles(tenant_id,glpi_article_id,title,content,visibility,acl_roles,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.glpi_article_id,payload.title,payload.content,payload.visibility,payload.acl_roles,actor["id"])).fetchone()


@router.patch("/knowledge/{article_id}/status")
def knowledge_status(article_id:str,payload:StatusPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:return transition(c,tenant_id,"knowledge","cm_knowledge_articles",article_id,payload.status,actor["id"],payload.reason)


@router.get("/problems")
def problems(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_problems WHERE tenant_id=%s ORDER BY updated_at DESC",(tenant_id,)).fetchall())


@router.post("/problems",status_code=201)
def create_problem(payload:ProblemCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:return c.execute("INSERT INTO cm_problems(tenant_id,title,description,hypothesis,workaround,owner_user_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.title,payload.description,payload.hypothesis,payload.workaround,actor["id"])).fetchone()


@router.patch("/problems/{problem_id}")
def update_problem(problem_id:str,payload:ProblemUpdate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        if payload.status in ("root_cause_known","known_error","resolved","closed") and not payload.root_cause:raise HTTPException(422,"Confirmed root cause is required for this state")
        row=transition(c,tenant_id,"problem","cm_problems",problem_id,payload.status,actor["id"],payload.reason)
        return c.execute("UPDATE cm_problems SET hypothesis=COALESCE(%s,hypothesis),root_cause=COALESCE(%s,root_cause),workaround=COALESCE(%s,workaround) WHERE tenant_id=%s AND id=%s RETURNING *",(payload.hypothesis,payload.root_cause,payload.workaround,tenant_id,row["id"])).fetchone()


@router.get("/changes")
def changes(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_changes WHERE tenant_id=%s ORDER BY created_at DESC",(tenant_id,)).fetchall())


@router.post("/changes",status_code=201)
def create_change(payload:ChangeCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    if payload.window_start and payload.window_end and payload.window_end<=payload.window_start:raise HTTPException(422,"Change window end must be after start")
    with tenant_connection(tenant_id) as c:return c.execute("""INSERT INTO cm_changes(tenant_id,title,change_type,risk,implementation_plan,validation_plan,rollback_plan,window_start,window_end,owner_user_id)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(tenant_id,payload.title,payload.change_type,payload.risk,payload.implementation_plan,payload.validation_plan,payload.rollback_plan,payload.window_start,payload.window_end,actor["id"])).fetchone()


@router.patch("/changes/{change_id}/status")
def change_status(change_id:str,payload:StatusPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        current=c.execute("SELECT owner_user_id,change_type,risk,window_start,window_end FROM cm_changes WHERE tenant_id=%s AND id=%s",(tenant_id,change_id)).fetchone()
        if not current:raise HTTPException(404,"Change not found")
        if payload.status=="approved" and str(current["owner_user_id"])==str(actor["id"]):raise HTTPException(409,"Change owner cannot approve their own change")
        if payload.status=="scheduled" and (not current["window_start"] or not current["window_end"]):raise HTTPException(422,"Scheduled changes require a valid execution window")
        return transition(c,tenant_id,"change","cm_changes",change_id,payload.status,actor["id"],payload.reason)


@router.get("/runbooks")
def runbooks(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_runbooks WHERE tenant_id=%s ORDER BY name",(tenant_id,)).fetchall())


@router.post("/runbooks",status_code=201)
def create_runbook(payload:RunbookCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    try:validate_runbook_steps(payload.steps)
    except ValueError as error:raise HTTPException(422,str(error)) from error
    with tenant_connection(tenant_id) as c:
        row=c.execute("INSERT INTO cm_runbooks(tenant_id,code,name,current_version) VALUES (%s,%s,%s,1) RETURNING *",(tenant_id,payload.code,payload.name)).fetchone()
        c.execute("""INSERT INTO cm_runbook_versions(tenant_id,runbook_id,version,risk,parameters_schema,target_selector,preconditions,steps,postconditions,lock_key,approval_required,created_by)
          VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(tenant_id,row["id"],payload.risk,Jsonb(payload.parameters_schema),Jsonb(payload.target_selector),Jsonb(payload.preconditions),Jsonb(payload.steps),Jsonb(payload.postconditions),payload.lock_key,payload.approval_required,actor["id"]))
        return row


@router.patch("/runbooks/{runbook_id}/status")
def runbook_status(runbook_id:str,payload:StatusPayload,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:return transition(c,tenant_id,"runbook","cm_runbooks",runbook_id,payload.status,actor["id"],payload.reason)


@router.get("/runbook-executions")
def runbook_executions(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT e.*,r.code,r.name FROM cm_runbook_executions e JOIN cm_runbooks r ON r.id=e.runbook_id WHERE e.tenant_id=%s ORDER BY e.created_at DESC LIMIT 200",(tenant_id,)).fetchall())


@router.post("/runbooks/{runbook_id}/executions",status_code=201)
def request_runbook(runbook_id:str,payload:RunbookExecutionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager","operator"))
    with tenant_connection(tenant_id) as c:
        version=c.execute("SELECT v.* FROM cm_runbooks r JOIN cm_runbook_versions v ON v.tenant_id=r.tenant_id AND v.runbook_id=r.id AND v.version=r.current_version WHERE r.tenant_id=%s AND r.id=%s AND r.status='published'",(tenant_id,runbook_id)).fetchone()
        if not version:raise HTTPException(409,"Only a published runbook version can execute")
        try:validate_parameters(version["parameters_schema"],payload.parameters)
        except ValueError as error:raise HTTPException(422,str(error)) from error
        initial="requested" if version["approval_required"] or version["risk"] in ("high","critical") else "approved"
        row=c.execute("INSERT INTO cm_runbook_executions(tenant_id,runbook_id,version,status,parameters,target_asset_id,requested_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,runbook_id,version["version"],initial,Jsonb(payload.parameters),payload.target_asset_id,actor["id"])).fetchone()
        if initial=="approved":c.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key) VALUES (%s,'runbook.approved',%s,%s)",(tenant_id,Jsonb({"execution_id":str(row["id"])}),f"runbook:{row['id']}"))
        return row


@router.post("/runbook-executions/{execution_id}/approve")
def approve_runbook(execution_id:str,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    with tenant_connection(tenant_id) as c:
        row=c.execute("UPDATE cm_runbook_executions SET status='approved',approved_by=%s WHERE tenant_id=%s AND id=%s AND status='requested' AND requested_by<>%s RETURNING *",(actor["id"],tenant_id,execution_id,actor["id"])).fetchone()
        if not row:raise HTTPException(409,"Execution is not pending approval or requester cannot self-approve")
        c.execute("INSERT INTO cm_outbox(tenant_id,topic,payload,idempotency_key) VALUES (%s,'runbook.approved',%s,%s)",(tenant_id,Jsonb({"execution_id":execution_id}),f"runbook:{execution_id}"))
        return row


@router.get("/oncall-schedules")
def oncall_schedules(tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id)
    with tenant_connection(tenant_id) as c:return list(c.execute("SELECT * FROM cm_oncall_schedules WHERE tenant_id=%s ORDER BY name",(tenant_id,)).fetchall())


@router.post("/oncall-schedules",status_code=201)
def create_oncall_schedule(payload:OncallScheduleCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    authorize(identity,tenant_id,("admin","manager"))
    if not payload.recurrence_rule.startswith("FREQ="):raise HTTPException(422,"Recurrence must use an explicit FREQ rule")
    if payload.ends_at and payload.ends_at<=payload.starts_at:raise HTTPException(422,"Schedule end must be after start")
    with tenant_connection(tenant_id) as c:return c.execute("INSERT INTO cm_oncall_schedules(tenant_id,name,timezone,recurrence_rule,starts_at,ends_at,team_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",(tenant_id,payload.name,payload.timezone,payload.recurrence_rule,payload.starts_at,payload.ends_at,payload.team_id)).fetchone()


@router.post("/oncall-schedules/{schedule_id}/substitutions",status_code=201)
def create_substitution(schedule_id:str,payload:OncallSubstitutionCreate,tenant_id:str=Depends(tenant_header),identity:Identity=Depends(current_identity)):
    actor=authorize(identity,tenant_id,("admin","manager"))
    if payload.ends_at<=payload.starts_at:raise HTTPException(422,"Substitution end must be after start")
    with tenant_connection(tenant_id) as c:
        overlap=c.execute("SELECT 1 FROM cm_oncall_substitutions WHERE tenant_id=%s AND schedule_id=%s AND tstzrange(starts_at,ends_at,'[)') && tstzrange(%s,%s,'[)')",(tenant_id,schedule_id,payload.starts_at,payload.ends_at)).fetchone()
        if overlap:raise HTTPException(409,"Substitution overlaps an existing substitution")
        row=c.execute("""INSERT INTO cm_oncall_substitutions(tenant_id,schedule_id,original_user_id,substitute_user_id,starts_at,ends_at,reason,approved_by)
          SELECT %s,id,%s,%s,%s,%s,%s,%s FROM cm_oncall_schedules WHERE tenant_id=%s AND id=%s RETURNING *""",(tenant_id,payload.original_user_id,payload.substitute_user_id,payload.starts_at,payload.ends_at,payload.reason,actor["id"],tenant_id,schedule_id)).fetchone()
        if not row:raise HTTPException(404,"On-call schedule not found")
        return row
