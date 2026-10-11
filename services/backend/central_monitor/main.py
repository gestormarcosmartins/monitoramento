from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .auth import Identity, current_identity, tenant_header
from .db import connect, migrate, tenant_connection
from .health import check_dependencies
from .glpi import GlpiClient
from .schemas import (
    ActionExecutionRequest,
    ActionGrantCreate,
    AlertUpdate,
    AssetCreate,
    AssetUpdate,
    CustomEvent,
    IncidentUpdate,
    MonitorCreate,
    MonitorUpdate,
    TenantCreate,
    TenantUpdate,
)
from .zabbix import ZabbixClient
from .operations import router as operations_router
from .benner import router as benner_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    yield


app = FastAPI(
    title="Central Monitor API",
    version="0.2.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.include_router(operations_router)
app.include_router(benner_router)


def ensure_user(identity: Identity) -> dict[str, Any]:
    with connect() as connection:
        return connection.execute(
            """
            INSERT INTO cm_users(external_subject, email, display_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (external_subject) DO UPDATE
            SET email = excluded.email, display_name = excluded.display_name
            RETURNING id, external_subject, email, display_name
            """,
            (identity.subject, identity.email, identity.display_name),
        ).fetchone()


def authorize_tenant(identity: Identity, tenant_id: str, roles: tuple[str, ...] = ("admin", "operator", "viewer")) -> dict[str, Any]:
    user = ensure_user(identity)
    with connect() as connection:
        membership = connection.execute(
            "SELECT role FROM cm_user_tenants WHERE user_id=%s AND tenant_id=%s",
            (user["id"], tenant_id),
        ).fetchone()
    if not membership or membership["role"] not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied")
    return {**user, "role": membership["role"]}


@app.get("/api/v1/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "up"}


@app.get("/api/v1/health/ready", tags=["health"])
def ready() -> dict[str, str | bool]:
    dependencies = check_dependencies()
    if not dependencies.ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=dependencies.as_dict())
    return dependencies.as_dict()


@app.get("/api/v1/auth/me", tags=["auth"])
def me(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = ensure_user(identity)
    with connect() as connection:
        tenants = list(
            connection.execute(
                """
                SELECT t.id, t.name, t.slug, t.timezone, t.active, t.created_at, ut.role
                FROM cm_user_tenants ut JOIN cm_tenants t ON t.id=ut.tenant_id
                WHERE ut.user_id=%s ORDER BY t.name
                """,
                (user["id"],),
            ).fetchall()
        )
    return {**user, "tenants": tenants}


@app.post("/api/v1/tenants", status_code=status.HTTP_201_CREATED, tags=["tenants"])
def create_tenant(payload: TenantCreate, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = ensure_user(identity)
    try:
        with connect() as connection:
            tenant = connection.execute(
                "INSERT INTO cm_tenants(name,slug,timezone) VALUES (%s,%s,%s) RETURNING *",
                (payload.name, payload.slug, payload.timezone),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_user_tenants(user_id,tenant_id,role) VALUES (%s,%s,'admin')",
                (user["id"], tenant["id"]),
            )
            connection.execute(
                """INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes) VALUES
                (%s,'SLA Baixo','low',240,2880),(%s,'SLA Médio','medium',120,1440),
                (%s,'SLA Alto','high',30,480),(%s,'SLA Crítico','critical',15,240)
                ON CONFLICT (tenant_id,priority) DO NOTHING""",
                (tenant["id"],tenant["id"],tenant["id"],tenant["id"]),
            )
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'create','tenant',%s)",
                (tenant["id"], user["id"], str(tenant["id"])),
            )
            return tenant
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant slug already exists") from error


@app.get("/api/v1/tenants", tags=["tenants"])
def list_tenants(identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    return me(identity)["tenants"]


@app.get("/api/v1/dashboard", tags=["dashboard"])
def dashboard(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    tenants = me(identity)["tenants"]
    companies: list[dict[str, Any]] = []
    totals = {"critical_alerts": 0, "attention_alerts": 0, "open_incidents": 0}
    for tenant in tenants:
        if not tenant["active"]:
            continue
        tenant_id = str(tenant["id"])
        with tenant_connection(tenant_id) as connection:
            metrics = connection.execute(
                """SELECT
                (SELECT count(*) FROM cm_assets WHERE tenant_id=%s AND active) AS environments,
                (SELECT count(*) FROM cm_monitors WHERE tenant_id=%s AND active) AS services,
                (SELECT count(*) FROM cm_alerts WHERE tenant_id=%s AND status='open' AND severity IN ('high','disaster')) AS critical_alerts,
                (SELECT count(*) FROM cm_alerts WHERE tenant_id=%s AND status='open' AND severity IN ('warning','average')) AS attention_alerts,
                (SELECT count(*) FROM cm_incidents WHERE tenant_id=%s AND status NOT IN ('resolved','ignored')) AS open_incidents""",
                (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
            ).fetchone()
        row = {"id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], **metrics}
        row["open_alerts"] = row["critical_alerts"] + row["attention_alerts"]
        companies.append(row)
        for key in totals:
            totals[key] += row[key]
    totals["companies_with_alerts"] = sum(1 for company in companies if company["open_alerts"] > 0)
    return {"totals": totals, "companies": companies}


@app.put("/api/v1/tenants/{tenant_id}", tags=["tenants"])
def update_tenant(tenant_id: str, payload: TenantUpdate, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin",))
    try:
        with connect() as connection:
            tenant = connection.execute(
                """UPDATE cm_tenants SET name=%s,slug=%s,timezone=%s,active=%s
                WHERE id=%s RETURNING *""",
                (payload.name, payload.slug, payload.timezone, payload.active, tenant_id),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','tenant',%s)",
                (tenant_id, user["id"], tenant_id),
            )
            return tenant
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tenant slug already exists") from error


@app.delete("/api/v1/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tenants"])
def delete_tenant(tenant_id: str, identity: Identity = Depends(current_identity)) -> None:
    authorize_tenant(identity, tenant_id, ("admin",))
    with connect() as connection:
        deleted = connection.execute("DELETE FROM cm_tenants WHERE id=%s RETURNING id", (tenant_id,)).fetchone()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")


@app.post("/api/v1/assets", status_code=status.HTTP_201_CREATED, tags=["assets"])
def create_asset(payload: AssetCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    try:
        with tenant_connection(tenant_id) as connection:
            asset = connection.execute(
                """
                INSERT INTO cm_assets(tenant_id,name,kind,address,criticality,metadata)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING *
                """,
                (tenant_id, payload.name, payload.kind, payload.address, payload.criticality, Jsonb(payload.metadata)),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'create','asset',%s)",
                (tenant_id, user["id"], str(asset["id"])),
            )
            return asset
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset name already exists") from error


@app.get("/api/v1/assets", tags=["assets"])
def list_assets(active: bool | None = Query(default=None), tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    query = "SELECT * FROM cm_assets WHERE tenant_id=%s"
    params: tuple[Any, ...] = (tenant_id,)
    if active is not None:
        query += " AND active=%s"
        params += (active,)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(query + " ORDER BY name", params).fetchall())


@app.put("/api/v1/assets/{asset_id}", tags=["assets"])
def update_asset(asset_id: str, payload: AssetUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    try:
        with tenant_connection(tenant_id) as connection:
            asset = connection.execute(
                """UPDATE cm_assets SET name=%s,kind=%s,address=%s,criticality=%s,metadata=%s,active=%s
                WHERE id=%s AND tenant_id=%s RETURNING *""",
                (payload.name, payload.kind, payload.address, payload.criticality, Jsonb(payload.metadata), payload.active, asset_id, tenant_id),
            ).fetchone()
            if not asset:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','asset',%s)",
                (tenant_id, user["id"], asset_id),
            )
            return asset
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset name already exists") from error


@app.delete("/api/v1/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["assets"])
def delete_asset(asset_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        deleted = connection.execute("DELETE FROM cm_assets WHERE id=%s AND tenant_id=%s RETURNING id", (asset_id, tenant_id)).fetchone()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")


@app.post("/api/v1/monitors", status_code=status.HTTP_201_CREATED, tags=["monitors"])
def create_monitor(payload: MonitorCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    try:
        with tenant_connection(tenant_id) as connection:
            monitor = connection.execute(
                """
                INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,interval_seconds,severity,
                    responsible_team_id,responsible_user_id,notify_on_failure,notify_on_recovery,auto_resolve_incident)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
                """,
                (tenant_id, payload.asset_id, payload.name, payload.monitor_type, Jsonb(payload.configuration), payload.interval_seconds, payload.severity,
                 payload.responsible_team_id,payload.responsible_user_id,payload.notify_on_failure,payload.notify_on_recovery,payload.auto_resolve_incident),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'monitor.created',%s)",
                (tenant_id, Jsonb({"monitor_id": str(monitor["id"])})),
            )
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'create','monitor',%s)",
                (tenant_id, user["id"], str(monitor["id"])),
            )
            return monitor
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Monitor name already exists") from error


@app.get("/api/v1/monitors", tags=["monitors"])
def list_monitors(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(
            """SELECT m.*,a.name AS asset_name,t.name AS responsible_team_name,u.display_name AS responsible_user_name FROM cm_monitors m
            JOIN cm_assets a ON a.id=m.asset_id LEFT JOIN cm_teams t ON t.id=m.responsible_team_id
            LEFT JOIN cm_users u ON u.id=m.responsible_user_id WHERE m.tenant_id=%s ORDER BY m.name""",
            (tenant_id,),
        ).fetchall())


@app.put("/api/v1/monitors/{monitor_id}", tags=["monitors"])
def update_monitor(monitor_id: str, payload: MonitorUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    try:
        with tenant_connection(tenant_id) as connection:
            monitor = connection.execute(
                """UPDATE cm_monitors SET asset_id=%s,name=%s,monitor_type=%s,configuration=%s,
                interval_seconds=%s,severity=%s,active=%s,responsible_team_id=%s,responsible_user_id=%s,
                notify_on_failure=%s,notify_on_recovery=%s,auto_resolve_incident=%s
                WHERE id=%s AND tenant_id=%s RETURNING *""",
                (payload.asset_id, payload.name, payload.monitor_type, Jsonb(payload.configuration), payload.interval_seconds, payload.severity, payload.active,
                 payload.responsible_team_id,payload.responsible_user_id,payload.notify_on_failure,payload.notify_on_recovery,payload.auto_resolve_incident,monitor_id,tenant_id),
            ).fetchone()
            if not monitor:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','monitor',%s)",
                (tenant_id, user["id"], monitor_id),
            )
            return monitor
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Monitor name already exists") from error


@app.delete("/api/v1/monitors/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["monitors"])
def delete_monitor(monitor_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        deleted = connection.execute("DELETE FROM cm_monitors WHERE id=%s AND tenant_id=%s RETURNING id", (monitor_id, tenant_id)).fetchone()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")


@app.post("/api/v1/events", status_code=status.HTTP_202_ACCEPTED, tags=["events"])
def receive_event(payload: CustomEvent, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize_tenant(identity, tenant_id, ("admin", "operator"))
    try:
        with tenant_connection(tenant_id) as connection:
            event = connection.execute(
                """
                INSERT INTO cm_events(tenant_id,external_event_id,source,event_type,severity,occurred_at,attributes)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at
                """,
                (tenant_id, payload.event_id, payload.source, payload.event_type, payload.severity, payload.occurred_at, Jsonb(payload.attributes)),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'event.received',%s)",
                (tenant_id, Jsonb({"event_id": str(event["id"])})),
            )
            return {"status": "accepted", **event}
    except UniqueViolation:
        return {"status": "duplicate", "eventId": payload.event_id}


@app.get("/api/v1/alerts", tags=["alerts"])
def list_alerts(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute("SELECT * FROM cm_alerts WHERE tenant_id=%s ORDER BY opened_at DESC", (tenant_id,)).fetchall())


@app.put("/api/v1/alerts/{alert_id}", tags=["alerts"])
def update_alert(alert_id: str, payload: AlertUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    with tenant_connection(tenant_id) as connection:
        alert = connection.execute(
            """UPDATE cm_alerts SET title=%s,severity=%s,status=%s,
            resolved_at=CASE WHEN %s='resolved' THEN now() ELSE NULL END
            WHERE id=%s AND tenant_id=%s RETURNING *""",
            (payload.title, payload.severity, payload.status, payload.status, alert_id, tenant_id),
        ).fetchone()
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        connection.execute(
            "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','alert',%s)",
            (tenant_id, user["id"], alert_id),
        )
        return alert


@app.delete("/api/v1/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["alerts"])
def delete_alert(alert_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        deleted = connection.execute("DELETE FROM cm_alerts WHERE id=%s AND tenant_id=%s RETURNING id", (alert_id, tenant_id)).fetchone()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")


@app.get("/api/v1/incidents", tags=["incidents"])
def list_incidents(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute("SELECT * FROM cm_incidents WHERE tenant_id=%s ORDER BY updated_at DESC", (tenant_id,)).fetchall())


@app.patch("/api/v1/incidents/{incident_id}", tags=["incidents"])
def update_incident(incident_id: str, payload: IncidentUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    with tenant_connection(tenant_id) as connection:
        incident = connection.execute(
            """UPDATE cm_incidents SET status=%s,title=COALESCE(%s,title),description=COALESCE(%s,description),
            priority=COALESCE(%s,priority),category=COALESCE(%s,category),
            assignee_user_id=CASE WHEN %s THEN %s ELSE assignee_user_id END,
            assignee_team_id=CASE WHEN %s THEN %s ELSE assignee_team_id END,
            supplier_id=COALESCE(%s,supplier_id),supplier_protocol=COALESCE(%s,supplier_protocol),
            first_response_at=CASE WHEN %s IN ('acknowledged','in_progress') THEN COALESCE(first_response_at,now()) ELSE first_response_at END,
            acknowledged_at=CASE WHEN %s='acknowledged' THEN COALESCE(acknowledged_at,now()) ELSE acknowledged_at END,
            resolved_at=CASE WHEN %s='resolved' THEN COALESCE(resolved_at,now()) ELSE resolved_at END,
            closed_at=CASE WHEN %s='closed' THEN COALESCE(closed_at,now()) ELSE closed_at END,
            response_due_at=CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN response_due_at+(now()-sla_paused_at) ELSE response_due_at END,
            resolution_due_at=CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN resolution_due_at+(now()-sla_paused_at) ELSE resolution_due_at END,
            sla_paused_seconds=sla_paused_seconds+CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN EXTRACT(EPOCH FROM (now()-sla_paused_at))::bigint ELSE 0 END,
            sla_paused_at=CASE WHEN %s IN ('waiting_customer','waiting_supplier') THEN COALESCE(sla_paused_at,now()) ELSE NULL END,
            version=version+1,updated_at=now(),sync_status='pending'
            WHERE id=%s AND tenant_id=%s AND version=%s RETURNING *""",
            (payload.status,payload.title,payload.description,payload.priority,payload.category,
             "assignee_user_id" in payload.model_fields_set,payload.assignee_user_id,
             "assignee_team_id" in payload.model_fields_set,payload.assignee_team_id,payload.supplier_id,payload.supplier_protocol,
             payload.status,payload.status,payload.status,payload.status,payload.status,payload.status,payload.status,payload.status,
             incident_id,tenant_id,payload.version),
        ).fetchone()
        if not incident:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Incident version conflict or not found")
        connection.execute(
            "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'update','incident',%s,%s)",
            (tenant_id, user["id"], incident_id, Jsonb({"status": payload.status})),
        )
        connection.execute(
            "INSERT INTO cm_incident_history(tenant_id,incident_id,actor_id,event_type,details) VALUES (%s,%s,%s,'incident.updated',%s)",
            (tenant_id,incident_id,user["id"],Jsonb(payload.model_dump(mode="json",exclude_none=True))),
        )
        connection.execute(
            "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'incident.status_changed',%s)",
            (tenant_id, Jsonb({"incident_id": incident_id, "status": payload.status})),
        )
        connection.execute(
            "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'notification.requested',%s)",
            (tenant_id,Jsonb({"incident_id":incident_id,"event":f"incident.{payload.status}"})),
        )
        if payload.supplier_id:
            connection.execute(
                """UPDATE cm_incidents i SET supplier_due_at=now()+(s.resolution_minutes*interval '1 minute')
                FROM cm_suppliers s WHERE i.tenant_id=%s AND i.id=%s AND s.tenant_id=i.tenant_id AND s.id=i.supplier_id""",
                (tenant_id,incident_id),
            )
        return incident


@app.delete("/api/v1/incidents/{incident_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["incidents"])
def delete_incident(incident_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        deleted = connection.execute("DELETE FROM cm_incidents WHERE id=%s AND tenant_id=%s RETURNING id", (incident_id, tenant_id)).fetchone()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")


@app.get("/api/v1/integrations/zabbix/status", tags=["integrations"])
def zabbix_status(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize_tenant(identity, tenant_id)
    try:
        return ZabbixClient().status()
    except (RuntimeError, OSError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@app.get("/api/v1/integrations/zabbix/problems", tags=["integrations"])
def zabbix_problems(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    try:
        return ZabbixClient().problems()
    except (httpx.HTTPError,RuntimeError,OSError) as error:
        raise HTTPException(status_code=503,detail=str(error)) from error


@app.get("/api/v1/integrations/glpi/status", tags=["integrations"])
def glpi_status(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize_tenant(identity, tenant_id)
    try:
        return GlpiClient().status()
    except (httpx.HTTPError, RuntimeError, OSError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@app.post("/api/v1/actions/grants", status_code=status.HTTP_201_CREATED, tags=["actions"])
def create_action_grant(payload: ActionGrantCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        grant = connection.execute(
            """
            INSERT INTO cm_action_grants(tenant_id,action_id,asset_id,enabled,max_per_hour,cooldown_seconds)
            SELECT %s,id,%s,%s,%s,%s FROM cm_action_definitions WHERE code=%s
            ON CONFLICT (tenant_id,action_id,asset_id) DO UPDATE
            SET enabled=excluded.enabled,max_per_hour=excluded.max_per_hour,cooldown_seconds=excluded.cooldown_seconds
            RETURNING *
            """,
            (tenant_id, payload.asset_id, payload.enabled, payload.max_per_hour, payload.cooldown_seconds, payload.action_code),
        ).fetchone()
        connection.execute(
            "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'grant','action_grant',%s,%s)",
            (tenant_id, user["id"], str(grant["id"]), Jsonb({"enabled": payload.enabled})),
        )
        return grant


@app.post("/api/v1/actions/execute", status_code=status.HTTP_202_ACCEPTED, tags=["actions"])
def request_action(payload: ActionExecutionRequest, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    with tenant_connection(tenant_id) as connection:
        grant = connection.execute(
            """
            SELECT g.*,d.id AS definition_id,d.risk_level FROM cm_action_grants g
            JOIN cm_action_definitions d ON d.id=g.action_id
            WHERE g.tenant_id=%s AND g.asset_id=%s AND d.code=%s AND g.enabled AND d.enabled
            """,
            (tenant_id, payload.asset_id, payload.action_code),
        ).fetchone()
        if not grant:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Action is not granted for this asset")
        if grant["risk_level"] != "low":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only low-risk actions are accepted")
        recent = connection.execute(
            """SELECT count(*) AS total FROM cm_action_executions
            WHERE tenant_id=%s AND action_id=%s AND asset_id=%s AND created_at >= now()-interval '1 hour'""",
            (tenant_id, grant["action_id"], payload.asset_id),
        ).fetchone()["total"]
        if recent >= grant["max_per_hour"]:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Action hourly limit reached")
        last_execution = connection.execute(
            """SELECT created_at FROM cm_action_executions
            WHERE tenant_id=%s AND action_id=%s AND asset_id=%s
            ORDER BY created_at DESC LIMIT 1""",
            (tenant_id, grant["action_id"], payload.asset_id),
        ).fetchone()
        if last_execution:
            elapsed = (datetime.now(UTC) - last_execution["created_at"]).total_seconds()
            if elapsed < grant["cooldown_seconds"]:
                retry_after = int(grant["cooldown_seconds"] - elapsed) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Action cooldown active; retry in {retry_after} seconds",
                    headers={"Retry-After": str(retry_after)},
                )
        execution = connection.execute(
            """
            INSERT INTO cm_action_executions(tenant_id,action_id,asset_id,requested_by,idempotency_key,status,parameters)
            VALUES (%s,%s,%s,%s,%s,'validated',%s)
            ON CONFLICT (tenant_id,idempotency_key) DO UPDATE SET idempotency_key=excluded.idempotency_key
            RETURNING id,status,created_at
            """,
            (tenant_id, grant["action_id"], payload.asset_id, user["id"], payload.idempotency_key, Jsonb(payload.parameters)),
        ).fetchone()
        connection.execute(
            "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'action.validated',%s)",
            (tenant_id, Jsonb({"execution_id": str(execution["id"])})),
        )
        return execution

