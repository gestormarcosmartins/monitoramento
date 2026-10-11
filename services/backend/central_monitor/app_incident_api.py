import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .command_bus import enqueue_command
from .db import tenant_connection
from .obligations import create_ticket_obligations
from .operations import authorize
from .settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["app incident api"])

SCOPES = {
    "incidents:read",
    "incidents:create",
    "incidents:attend",
    "incidents:finish",
}
OPEN_STATES = (
    "new",
    "triaged",
    "assigned",
    "acknowledged",
    "in_progress",
    "waiting_customer",
    "waiting_supplier",
)


def _hash_token(value: str) -> str:
    return hmac.new(
        get_settings().central_secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def _request_hash(payload: BaseModel) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


class ApiClientCreate(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    scopes: list[str] = Field(min_length=1)
    actor_user_id: UUID | None = None
    valid_days: int | None = Field(default=365, ge=1, le=730)


class AppIncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    description: str = Field(min_length=3, max_length=20000)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    category: str = Field(default="general", max_length=100)
    service_code: str | None = Field(default=None, max_length=100)
    source_reference: str | None = Field(default=None, max_length=300)
    assignee_team_id: UUID | None = None


class AppIncidentAttend(BaseModel):
    note: str | None = Field(default=None, min_length=1, max_length=10000)
    visibility: Literal["public", "private"] = "public"


class AppIncidentFinish(BaseModel):
    solution_type: Literal["permanent", "workaround", "not_reproduced", "cancelled"]
    description: str = Field(min_length=3, max_length=20000)
    evidence: str | None = Field(default=None, max_length=20000)


def app_identity(
    authorization: str | None = Header(default=None),
    tenant_id: str = Depends(tenant_header),
) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer nxapp_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nexora app token required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        client_id = str(UUID(token.removeprefix("nxapp_").split(".", 1)[0]))
    except (ValueError, IndexError) as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Nexora app token") from error
    with tenant_connection(tenant_id) as connection:
        client = connection.execute(
            """SELECT id,tenant_id,name,token_hash,scopes,actor_user_id,active,expires_at
            FROM cm_api_clients WHERE tenant_id=%s AND id=%s""",
            (tenant_id, client_id),
        ).fetchone()
        if (
            not client
            or not client["active"]
            or (client["expires_at"] and client["expires_at"] <= datetime.now(UTC))
            or not hmac.compare_digest(client["token_hash"], _hash_token(token))
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired Nexora app token")
        connection.execute(
            "UPDATE cm_api_clients SET last_used_at=now() WHERE tenant_id=%s AND id=%s",
            (tenant_id, client_id),
        )
    return client


def require_scope(client: dict[str, Any], scope: str) -> None:
    if scope not in client["scopes"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing scope: {scope}")


def _idempotent_result(
    connection: Any,
    client: dict[str, Any],
    key: str,
    operation: str,
    request_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT operation,request_hash,response_body FROM cm_api_idempotency
        WHERE tenant_id=%s AND api_client_id=%s AND idempotency_key=%s AND expires_at>now()""",
        (client["tenant_id"], client["id"], key),
    ).fetchone()
    if not row:
        return None
    if row["operation"] != operation or row["request_hash"] != request_hash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Idempotency-Key was already used with a different request",
        )
    return row["response_body"]


def _save_idempotent_result(
    connection: Any,
    client: dict[str, Any],
    key: str,
    operation: str,
    request_hash: str,
    response_status: int,
    body: dict[str, Any],
) -> None:
    try:
        connection.execute(
            """INSERT INTO cm_api_idempotency
            (tenant_id,api_client_id,idempotency_key,operation,request_hash,response_status,response_body)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                client["tenant_id"],
                client["id"],
                key,
                operation,
                request_hash,
                response_status,
                Jsonb(body),
            ),
        )
    except UniqueViolation as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Concurrent idempotent request") from error


@router.post("/api-clients", status_code=201)
def create_api_client(
    payload: ApiClientCreate,
    tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    actor = authorize(identity, tenant_id, ("admin", "manager"))
    scopes = sorted(set(payload.scopes))
    invalid = sorted(set(scopes) - SCOPES)
    if invalid:
        raise HTTPException(422, f"Invalid scopes: {', '.join(invalid)}")
    actor_user_id = str(payload.actor_user_id or actor["id"])
    raw_secret = secrets.token_urlsafe(48)
    expires_at = (
        datetime.now(UTC) + timedelta(days=payload.valid_days)
        if payload.valid_days
        else None
    )
    with tenant_connection(tenant_id) as connection:
        if not connection.execute(
            """SELECT 1 FROM cm_users u WHERE u.id=%s AND u.active AND
            (u.is_master OR EXISTS(
                SELECT 1 FROM cm_access_grants g WHERE g.tenant_id=%s AND g.user_id=u.id
                AND g.active AND g.role IN ('admin','manager','operator') AND g.valid_from<=now()
                AND (g.valid_until IS NULL OR g.valid_until>now())
            ))""",
            (actor_user_id, tenant_id),
        ).fetchone():
            raise HTTPException(422, "Actor user must be active and authorized to operate this company")
        client_id = str(UUID(bytes=secrets.token_bytes(16), version=4))
        token = f"nxapp_{client_id}.{raw_secret}"
        row = connection.execute(
            """INSERT INTO cm_api_clients
            (id,tenant_id,name,token_hash,scopes,actor_user_id,expires_at,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id,name,scopes,actor_user_id,expires_at,created_at""",
            (
                client_id,
                tenant_id,
                payload.name,
                _hash_token(token),
                scopes,
                actor_user_id,
                expires_at,
                actor["id"],
            ),
        ).fetchone()
        connection.execute(
            """INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details)
            VALUES (%s,%s,'api_client.create','api_client',%s,%s)""",
            (tenant_id, actor["id"], client_id, Jsonb({"name": payload.name, "scopes": scopes})),
        )
    return {**row, "token": token}


@router.get("/api-clients")
def list_api_clients(
    tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> list[dict[str, Any]]:
    authorize(identity, tenant_id, ("admin", "manager", "auditor"))
    with tenant_connection(tenant_id) as connection:
        return list(
            connection.execute(
                """SELECT id,name,scopes,actor_user_id,active,expires_at,last_used_at,created_at,revoked_at
                FROM cm_api_clients WHERE tenant_id=%s ORDER BY name""",
                (tenant_id,),
            ).fetchall()
        )


@router.delete("/api-clients/{client_id}", status_code=204)
def revoke_api_client(
    client_id: UUID,
    tenant_id: str = Depends(tenant_header),
    identity: Identity = Depends(current_identity),
) -> None:
    actor = authorize(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        row = connection.execute(
            """UPDATE cm_api_clients SET active=false,revoked_at=now()
            WHERE tenant_id=%s AND id=%s AND active RETURNING id""",
            (tenant_id, client_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "API client not found")
        connection.execute(
            """INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id)
            VALUES (%s,%s,'api_client.revoke','api_client',%s)""",
            (tenant_id, actor["id"], str(client_id)),
        )


@router.get("/app/incidents")
def app_list_incidents(
    incident_status: str | None = Query(default="open", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    client: dict[str, Any] = Depends(app_identity),
) -> dict[str, Any]:
    require_scope(client, "incidents:read")
    tenant_id = str(client["tenant_id"])
    filters = ["tenant_id=%s", "inactive_at IS NULL"]
    params: list[Any] = [tenant_id]
    if incident_status == "open":
        filters.append("status=ANY(%s)")
        params.append(list(OPEN_STATES))
    elif incident_status:
        filters.append("status=%s")
        params.append(incident_status)
    where = " AND ".join(filters)
    with tenant_connection(tenant_id) as connection:
        total = connection.execute(
            f"SELECT count(*) AS total FROM cm_incidents WHERE {where}", tuple(params)
        ).fetchone()["total"]
        items = list(
            connection.execute(
                f"""SELECT id,glpi_ticket_id,title,description,priority,category,status,sync_status,
                assignee_team_id,assignee_user_id,created_at,updated_at,resolved_at,closed_at,version
                FROM cm_incidents WHERE {where} ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
        )
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "filters_applied": {"status": incident_status},
        "data_as_of": datetime.now(UTC),
        "sync_state": "projected",
    }


@router.get("/app/incidents/{incident_id}")
def app_incident_detail(
    incident_id: UUID, client: dict[str, Any] = Depends(app_identity)
) -> dict[str, Any]:
    require_scope(client, "incidents:read")
    tenant_id = str(client["tenant_id"])
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            """SELECT id,glpi_ticket_id,title,description,priority,category,status,sync_status,
            assignee_team_id,assignee_user_id,created_at,updated_at,resolved_at,closed_at,version
            FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL""",
            (tenant_id, incident_id),
        ).fetchone()
        if not incident:
            raise HTTPException(404, "Incident not found")
        entries = list(
            connection.execute(
                """SELECT id,body,is_internal,author_id,created_at,updated_at
                FROM cm_incident_comments WHERE tenant_id=%s AND incident_id=%s ORDER BY created_at""",
                (tenant_id, incident_id),
            ).fetchall()
        )
    return {"incident": incident, "entries": entries, "data_as_of": datetime.now(UTC)}


@router.post("/app/incidents", status_code=202)
def app_create_incident(
    payload: AppIncidentCreate,
    idempotency_key: str = Header(min_length=8, max_length=200, alias="Idempotency-Key"),
    client: dict[str, Any] = Depends(app_identity),
) -> dict[str, Any]:
    require_scope(client, "incidents:create")
    tenant_id = str(client["tenant_id"])
    digest = _request_hash(payload)
    with tenant_connection(tenant_id) as connection:
        repeated = _idempotent_result(connection, client, idempotency_key, "incident.create", digest)
        if repeated:
            return repeated
        if payload.assignee_team_id and not connection.execute(
            "SELECT 1 FROM cm_teams WHERE tenant_id=%s AND id=%s AND active",
            (tenant_id, payload.assignee_team_id),
        ).fetchone():
            raise HTTPException(422, "Invalid assignee team")
        row = connection.execute(
            """INSERT INTO cm_incidents
            (tenant_id,alert_id,status,title,description,priority,category,origin,ticket_type,
             requester_user_id,service_code,source_reference,assignee_team_id,sync_status)
            VALUES (%s,NULL,'new',%s,%s,%s,%s,'api','incident',%s,%s,%s,%s,'pending')
            RETURNING id,title,priority,category,status,sync_status,created_at,version""",
            (
                tenant_id,
                payload.title,
                payload.description,
                payload.priority,
                payload.category,
                client["actor_user_id"],
                payload.service_code,
                payload.source_reference,
                payload.assignee_team_id,
            ),
        ).fetchone()
        connection.execute(
            """INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details)
            VALUES (%s,%s,%s,'ticket.api_intent_created',%s)""",
            (tenant_id, row["id"], client["actor_user_id"], Jsonb({"api_client_id": str(client["id"])})),
        )
        create_ticket_obligations(connection, tenant_id, str(row["id"]), payload.priority)
        enqueue_command(
            connection,
            tenant_id,
            "incident.created",
            {"incident_id": str(row["id"])},
            f"incident.created:{row['id']}:{row['version']}",
            str(client["actor_user_id"]),
        )
        result = {**row, "operation_status": "pending", "remote_confirmed": False}
        _save_idempotent_result(connection, client, idempotency_key, "incident.create", digest, 202, result)
    return result


@router.post("/app/incidents/{incident_id}/attend", status_code=202)
def app_attend_incident(
    incident_id: UUID,
    payload: AppIncidentAttend,
    idempotency_key: str = Header(min_length=8, max_length=200, alias="Idempotency-Key"),
    client: dict[str, Any] = Depends(app_identity),
) -> dict[str, Any]:
    require_scope(client, "incidents:attend")
    tenant_id = str(client["tenant_id"])
    operation = f"incident.attend:{incident_id}"
    digest = _request_hash(payload)
    with tenant_connection(tenant_id) as connection:
        repeated = _idempotent_result(connection, client, idempotency_key, operation, digest)
        if repeated:
            return repeated
        current = connection.execute(
            "SELECT * FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL FOR UPDATE",
            (tenant_id, incident_id),
        ).fetchone()
        if not current:
            raise HTTPException(404, "Incident not found")
        if current["status"] not in OPEN_STATES:
            raise HTTPException(409, "Only open incidents can be attended")
        if current.get("assignee_user_id") and str(current["assignee_user_id"]) != str(client["actor_user_id"]):
            raise HTTPException(409, "Incident is assigned to another technician")
        if current.get("assignee_team_id") and not connection.execute(
            """SELECT 1 FROM cm_team_members
            WHERE tenant_id=%s AND team_id=%s AND user_id=%s""",
            (tenant_id, current["assignee_team_id"], client["actor_user_id"]),
        ).fetchone():
            raise HTTPException(403, "API client actor does not belong to the responsible team")
        row = connection.execute(
            """UPDATE cm_incidents SET assignee_user_id=%s,status='in_progress',sync_status='pending',
            version=version+1,updated_at=now() WHERE tenant_id=%s AND id=%s
            RETURNING id,status,sync_status,assignee_user_id,updated_at,version""",
            (client["actor_user_id"], tenant_id, incident_id),
        ).fetchone()
        if payload.note:
            comment = connection.execute(
                """INSERT INTO cm_incident_comments(tenant_id,incident_id,author_id,body,is_internal)
                VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (tenant_id, incident_id, client["actor_user_id"], payload.note, payload.visibility == "private"),
            ).fetchone()
            enqueue_command(connection, tenant_id, "incident.comment_created", {"incident_id": str(incident_id)}, f"incident.comment_created:{incident_id}:{comment['id']}", str(client["actor_user_id"]))
        connection.execute(
            """INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details)
            VALUES (%s,%s,%s,'ticket.api_attended',%s)""",
            (tenant_id, incident_id, client["actor_user_id"], Jsonb({"api_client_id": str(client["id"])})),
        )
        enqueue_command(connection, tenant_id, "incident.status_changed", {"incident_id": str(incident_id)}, f"incident.status_changed:{incident_id}:{row['version']}", str(client["actor_user_id"]))
        result = {**row, "operation_status": "pending", "remote_confirmed": False}
        _save_idempotent_result(connection, client, idempotency_key, operation, digest, 202, result)
    return result


@router.post("/app/incidents/{incident_id}/finish", status_code=202)
def app_finish_incident(
    incident_id: UUID,
    payload: AppIncidentFinish,
    idempotency_key: str = Header(min_length=8, max_length=200, alias="Idempotency-Key"),
    client: dict[str, Any] = Depends(app_identity),
) -> dict[str, Any]:
    require_scope(client, "incidents:finish")
    tenant_id = str(client["tenant_id"])
    operation = f"incident.finish:{incident_id}"
    digest = _request_hash(payload)
    with tenant_connection(tenant_id) as connection:
        repeated = _idempotent_result(connection, client, idempotency_key, operation, digest)
        if repeated:
            return repeated
        current = connection.execute(
            "SELECT * FROM cm_incidents WHERE tenant_id=%s AND id=%s AND inactive_at IS NULL FOR UPDATE",
            (tenant_id, incident_id),
        ).fetchone()
        if not current:
            raise HTTPException(404, "Incident not found")
        if current["status"] not in OPEN_STATES:
            raise HTTPException(409, "Only open incidents can be finished")
        if current.get("assignee_user_id") and str(current["assignee_user_id"]) != str(client["actor_user_id"]):
            raise HTTPException(409, "Incident is assigned to another technician")
        pending = connection.execute(
            """SELECT count(*) AS total FROM cm_ticket_tasks
            WHERE tenant_id=%s AND incident_id=%s AND status IN ('pending','in_progress')""",
            (tenant_id, incident_id),
        ).fetchone()["total"]
        if pending:
            raise HTTPException(409, "Complete pending tasks before finishing the incident")
        solution = connection.execute(
            """INSERT INTO cm_ticket_solutions
            (tenant_id,incident_id,solution_type,description,evidence,proposed_by)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,status""",
            (tenant_id, incident_id, payload.solution_type, payload.description, payload.evidence, client["actor_user_id"]),
        ).fetchone()
        row = connection.execute(
            """UPDATE cm_incidents SET status='resolved',resolved_at=now(),sync_status='pending',
            version=version+1,updated_at=now() WHERE tenant_id=%s AND id=%s
            RETURNING id,status,sync_status,resolved_at,updated_at,version""",
            (tenant_id, incident_id),
        ).fetchone()
        connection.execute(
            """INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details)
            VALUES (%s,%s,%s,'ticket.api_finished',%s)""",
            (tenant_id, incident_id, client["actor_user_id"], Jsonb({"api_client_id": str(client["id"]), "solution_id": str(solution["id"])})),
        )
        enqueue_command(connection, tenant_id, "incident.status_changed", {"incident_id": str(incident_id)}, f"incident.status_changed:{incident_id}:{row['version']}", str(client["actor_user_id"]))
        result = {**row, "solution_id": solution["id"], "operation_status": "pending", "remote_confirmed": False}
        _save_idempotent_result(connection, client, idempotency_key, operation, digest, 202, result)
    return result
