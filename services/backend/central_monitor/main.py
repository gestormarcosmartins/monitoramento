from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .auth import Identity, current_identity, tenant_header
from .db import connect, migrate, pool_stats, tenant_connection
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
from .agent_api import router as agent_router
from .business import router as business_router, agent_router as business_agent_router
from .business_p1 import router as business_p1_router
from .business_overview import router as business_overview_router
from .business_glpi import router as business_glpi_router
from .business_monitors import master_router as business_monitor_master_router
from .business_governance import master_router as business_governance_router
from .business_releases import master_router as business_releases_master_router
from .business_releases import worker_router as business_releases_worker_router
from .agent_releases import agent_router as agent_releases_agent_router
from .agent_releases import master_router as agent_releases_master_router
from .business_monitors import read_router as business_monitor_read_router
from .business_monitors import worker_router as business_worker_router
from .consolidated_tickets import router as consolidated_tickets_router
from .consolidated_alerts import router as consolidated_alerts_router
from .consolidated_inventory import assets_router as consolidated_assets_router, monitors_router as consolidated_monitors_router
from .ticketing import router as ticketing_router
from .catalog import router as catalog_router
from .service_operations import router as service_operations_router
from .financial import router as financial_router
from .monitoring import ingest_mapped_event, router as monitoring_router
from .governance import router as governance_router
from .integrations import router as integrations_router
from .reconciliation import router as reconciliation_router
from .push import router as push_router
from .expo_push import router as expo_push_router
from .app_incident_api import router as app_incident_router
from .command_bus import canonical_payload_hash
from .access_scope import incident_scope
from .errors import install_error_contract, openapi_error_responses


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
    responses=openapi_error_responses(),
)
install_error_contract(app)
app.include_router(operations_router)
app.include_router(benner_router)
app.include_router(agent_router)
app.include_router(business_router)
app.include_router(business_agent_router)
app.include_router(business_p1_router)
app.include_router(business_overview_router)
app.include_router(business_glpi_router)
app.include_router(business_monitor_master_router)
app.include_router(business_governance_router)
app.include_router(business_releases_master_router)
app.include_router(business_releases_worker_router)
app.include_router(agent_releases_master_router)
app.include_router(agent_releases_agent_router)
app.include_router(business_monitor_read_router)
app.include_router(business_worker_router)
# Must precede ticketing_router: "/tickets/consolidated" would otherwise match "/tickets/{ticket_id}".
app.include_router(consolidated_tickets_router)
app.include_router(consolidated_alerts_router)
app.include_router(consolidated_assets_router)
app.include_router(consolidated_monitors_router)
app.include_router(ticketing_router)
app.include_router(catalog_router)
app.include_router(service_operations_router)
app.include_router(financial_router)
app.include_router(monitoring_router)
app.include_router(governance_router)
app.include_router(integrations_router)
app.include_router(reconciliation_router)
app.include_router(push_router)
app.include_router(expo_push_router)
app.include_router(app_incident_router)


def ensure_user(identity: Identity) -> dict[str, Any]:
    with connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(20261011)")
        return connection.execute(
            """
            INSERT INTO cm_users(external_subject, email, display_name, is_master)
            VALUES (%s, %s, %s, NOT EXISTS (SELECT 1 FROM cm_users WHERE is_master))
            ON CONFLICT (external_subject) DO UPDATE
            SET email = excluded.email, display_name = excluded.display_name
            RETURNING id, external_subject, email, display_name, active, is_master
            """,
            (identity.subject, identity.email, identity.display_name),
        ).fetchone()


TENANT_ROLES = ("admin", "manager", "operator", "auditor", "viewer", "client")


def authorize_tenant(identity: Identity, tenant_id: str, roles: tuple[str, ...] = TENANT_ROLES) -> dict[str, Any]:
    user = ensure_user(identity)
    if not user["active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    if user["is_master"]:
        return {**user, "role": "master"}
    with tenant_connection(tenant_id) as connection:
        membership = connection.execute(
            """SELECT role,row_scope,engagement_id,department_id,team_id FROM cm_access_grants
            WHERE user_id=%s AND tenant_id=%s AND active
              AND valid_from<=now() AND (valid_until IS NULL OR valid_until>now())
            ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'manager' THEN 2 WHEN 'operator' THEN 3
                WHEN 'auditor' THEN 4 WHEN 'viewer' THEN 5 ELSE 6 END LIMIT 1""",
            (user["id"], tenant_id),
        ).fetchone()
    if not membership or membership["role"] not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied")
    return {**user, **membership}


@app.get("/api/v1/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "up"}


@app.get("/api/v1/health/ready", tags=["health"])
def ready() -> dict[str, Any]:
    dependencies = check_dependencies()
    if not dependencies.ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=dependencies.as_dict())
    return {**dependencies.as_dict(), "database_pool": pool_stats()}


@app.get("/api/v1/auth/me", tags=["auth"])
def me(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = ensure_user(identity)
    with connect() as connection:
        if user["is_master"]:
            tenants = list(connection.execute(
                """SELECT t.id,t.workspace_id,t.name,t.slug,t.timezone,t.active,t.created_at,'master' AS role
                FROM cm_tenants t ORDER BY t.name"""
            ).fetchall())
        else:
            candidates = list(connection.execute(
                "SELECT id,workspace_id,name,slug,timezone,active,created_at FROM cm_tenants WHERE active ORDER BY name"
            ).fetchall())
            tenants = []
            for tenant in candidates:
                with tenant_connection(str(tenant["id"])) as tenant_db:
                    grant = tenant_db.execute(
                        """SELECT role FROM cm_access_grants
                        WHERE tenant_id=%s AND user_id=%s AND active AND valid_from<=now()
                          AND (valid_until IS NULL OR valid_until>now())
                        ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'manager' THEN 2 WHEN 'operator' THEN 3
                            WHEN 'auditor' THEN 4 WHEN 'viewer' THEN 5 ELSE 6 END LIMIT 1""",
                        (tenant["id"], user["id"]),
                    ).fetchone()
                if grant:
                    tenants.append({**tenant, "role": grant["role"]})
    return {**user, "tenants": tenants}


@app.get("/api/v1/me/context", tags=["auth"])
def context(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user_context = me(identity)
    tenant_ids = [str(item["id"]) for item in user_context["tenants"]]
    with connect() as connection:
        workspaces = list(connection.execute(
            """SELECT DISTINCT w.id,w.code,w.name,w.active,w.authz_version
            FROM cm_workspaces w JOIN cm_tenants t ON t.workspace_id=w.id
            WHERE t.id=ANY(%s::uuid[]) ORDER BY w.name""",
            (tenant_ids,),
        ).fetchall()) if tenant_ids else []
        permissions = []
    if not user_context["is_master"]:
        for tenant_id in tenant_ids:
            with tenant_connection(tenant_id) as tenant_db:
                permissions.extend(tenant_db.execute(
                    """SELECT DISTINCT g.tenant_id,rp.permission_code
                    FROM cm_access_grants g JOIN cm_role_permissions rp ON rp.role=g.role
                    WHERE g.tenant_id=%s AND g.user_id=%s AND g.active AND g.valid_from<=now()
                      AND (g.valid_until IS NULL OR g.valid_until>now())
                    ORDER BY g.tenant_id,rp.permission_code""",
                    (tenant_id, user_context["id"]),
                ).fetchall())
    permissions_by_tenant: dict[str, list[str]] = {}
    for permission in permissions:
        permissions_by_tenant.setdefault(str(permission["tenant_id"]), []).append(permission["permission_code"])
    if user_context["is_master"]:
        with connect() as connection:
            all_permissions = [row["code"] for row in connection.execute("SELECT code FROM cm_permission_catalog ORDER BY code")]
        permissions_by_tenant = {tenant_id: all_permissions for tenant_id in tenant_ids}
    feature_modes_by_tenant: dict[str, dict[str, str]] = {}
    for tenant_id in tenant_ids:
        with tenant_connection(tenant_id) as tenant_db:
            flags = tenant_db.execute(
                "SELECT feature,mode FROM cm_feature_flags WHERE tenant_id=%s ORDER BY feature",
                (tenant_id,),
            ).fetchall()
        feature_modes_by_tenant[tenant_id] = {row["feature"]: row["mode"] for row in flags}
    return {
        "user": {key: user_context[key] for key in ("id", "external_subject", "email", "display_name", "active", "is_master")},
        "workspaces": workspaces,
        "companies": user_context["tenants"],
        "permissions_by_company": permissions_by_tenant,
        "feature_modes_by_company": feature_modes_by_tenant,
    }


@app.post("/api/v1/tenants", status_code=status.HTTP_201_CREATED, tags=["tenants"])
def create_tenant(payload: TenantCreate, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = ensure_user(identity)
    if not user["active"] or not user["is_master"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Master access required")
    try:
        with connect() as connection:
            tenant = connection.execute(
                """INSERT INTO cm_tenants(workspace_id,name,slug,timezone)
                VALUES ((SELECT id FROM cm_workspaces WHERE code='default'),%s,%s,%s) RETURNING *""",
                (payload.name, payload.slug, payload.timezone),
            ).fetchone()
            connection.execute(
                "INSERT INTO cm_user_tenants(user_id,tenant_id,role) VALUES (%s,%s,'admin')",
                (user["id"], tenant["id"]),
            )
            connection.execute(
                """INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,granted_by,reason)
                VALUES (%s,%s,%s,'admin','company',%s,'Criador da empresa')""",
                (tenant["workspace_id"],tenant["id"],user["id"],user["id"]),
            )
            connection.execute(
                """INSERT INTO cm_service_engagements(workspace_id,tenant_id,operator_company_id,kind,name)
                VALUES (%s,%s,%s,'internal','Atendimento interno')""",
                (tenant["workspace_id"],tenant["id"],tenant["id"]),
            )
            connection.execute(
                """INSERT INTO cm_sla_policies(tenant_id,name,priority,response_minutes,resolution_minutes) VALUES
                (%s,'SLA Baixo','low',240,2880),(%s,'SLA Médio','medium',120,1440),
                (%s,'SLA Alto','high',30,480),(%s,'SLA Crítico','critical',15,240)
                ON CONFLICT (tenant_id,priority) DO NOTHING""",
                (tenant["id"],tenant["id"],tenant["id"],tenant["id"]),
            )
            # cm_outbox has forced RLS: scope this transaction to the new company before queueing its GLPI entity.
            connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant["id"]),))
            connection.execute(
                "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'tenant.created',%s)",
                (tenant["id"], Jsonb({"tenant_id": str(tenant["id"])})),
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
def dashboard(tenant_ids: str | None = Query(default=None), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    tenants = me(identity)["tenants"]
    selected = {value for value in (tenant_ids or "").split(",") if value}
    if selected:
        tenants = [tenant for tenant in tenants if str(tenant["id"]) in selected]
    companies: list[dict[str, Any]] = []
    recent_incidents: list[dict[str, Any]] = []
    critical_services: list[dict[str, Any]] = []
    environments: list[dict[str, Any]] = []
    totals = {"critical_alerts": 0, "attention_alerts": 0, "open_incidents": 0, "services_active": 0, "services_failed": 0, "services_normalizing": 0, "services_inactive": 0}
    status_tenants: dict[str, list[str]] = {"active": [], "failed": [], "normalizing": [], "inactive": []}
    severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
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
                (SELECT count(*) FROM cm_incidents WHERE tenant_id=%s AND status NOT IN ('resolved','closed','ignored')) AS open_incidents,
                (SELECT count(*) FROM cm_monitors WHERE tenant_id=%s AND active AND last_status='up') AS services_active,
                (SELECT count(*) FROM cm_monitors WHERE tenant_id=%s AND active AND last_status='down') AS services_failed,
                (SELECT count(*) FROM cm_monitors WHERE tenant_id=%s AND active AND last_status='unknown') AS services_normalizing,
                (SELECT count(*) FROM cm_monitors WHERE tenant_id=%s AND NOT active) AS services_inactive""",
                (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
            ).fetchone()
            incident_rows = list(connection.execute(
                """SELECT i.id,i.title,i.priority,i.status,i.created_at,m.name AS service_name
                FROM cm_incidents i LEFT JOIN cm_alerts al ON al.id=i.alert_id
                LEFT JOIN cm_monitors m ON m.id=al.monitor_id
                WHERE i.tenant_id=%s ORDER BY i.created_at DESC LIMIT 8""", (tenant_id,)
            ).fetchall())
            service_rows = list(connection.execute(
                """SELECT m.id,m.name,m.active,m.last_status,
                count(i.id) FILTER (WHERE i.created_at >= now()-interval '7 days') AS incidents_7d
                FROM cm_monitors m LEFT JOIN cm_alerts al ON al.monitor_id=m.id
                LEFT JOIN cm_incidents i ON i.alert_id=al.id
                WHERE m.tenant_id=%s GROUP BY m.id,m.name,m.active,m.last_status
                ORDER BY incidents_7d DESC,m.name LIMIT 6""", (tenant_id,)
            ).fetchall())
            environment_rows = list(connection.execute(
                """SELECT a.id,a.name,count(m.id) AS services,
                count(m.id) FILTER (WHERE m.active AND m.last_status='down') AS failures
                FROM cm_assets a LEFT JOIN cm_monitors m ON m.asset_id=a.id
                WHERE a.tenant_id=%s AND a.active GROUP BY a.id,a.name ORDER BY a.name LIMIT 8""", (tenant_id,)
            ).fetchall())
        row = {"id": tenant["id"], "name": tenant["name"], "slug": tenant["slug"], **metrics}
        row["open_alerts"] = row["critical_alerts"] + row["attention_alerts"]
        companies.append(row)
        for key in totals:
            totals[key] += row[key]
        for status, metric in (("active", "services_active"), ("failed", "services_failed"), ("normalizing", "services_normalizing"), ("inactive", "services_inactive")):
            if row[metric]:
                status_tenants[status].append(tenant_id)
        for incident in incident_rows:
            item = dict(incident)
            item["company"] = tenant["name"]
            item["tenant_id"] = tenant_id
            recent_incidents.append(item)
            if item.get("priority") in severity: severity[item["priority"]] += 1
        for service in service_rows:
            item = dict(service)
            item["company"] = tenant["name"]
            item["tenant_id"] = tenant_id
            item["availability"] = 100.0 if item["last_status"] == "up" else (0.0 if item["last_status"] == "down" else 99.0)
            critical_services.append(item)
        for environment in environment_rows:
            item = dict(environment)
            item["company"] = tenant["name"]
            item["tenant_id"] = tenant_id
            item["status"] = "attention" if item["failures"] else "healthy"
            environments.append(item)
    totals["companies_with_alerts"] = sum(1 for company in companies if company["open_alerts"] > 0)
    service_total = totals["services_active"] + totals["services_failed"] + totals["services_normalizing"] + totals["services_inactive"]
    totals["availability"] = round((totals["services_active"] / service_total * 100), 1) if service_total else 100.0
    return {"totals": totals, "companies": companies, "severity": severity, "status_tenants": status_tenants,
            "recent_incidents": sorted(recent_incidents,key=lambda row: row["created_at"],reverse=True)[:8],
            "critical_services": sorted(critical_services,key=lambda row: int(row["incidents_7d"] or 0),reverse=True)[:6],
            "environments": environments[:8]}


@app.get("/api/v1/dashboard/noc", tags=["dashboard"])
def dashboard_noc(tenant_ids: str | None = Query(default=None), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """Read-only NOC view: one card per environment, limited to the caller's authorized companies."""
    tenants = me(identity)["tenants"]
    selected = {value for value in (tenant_ids or "").split(",") if value}
    if selected:
        tenants = [tenant for tenant in tenants if str(tenant["id"]) in selected]
    now = datetime.now(UTC)
    cards: list[dict[str, Any]] = []
    for tenant in tenants:
        if not tenant["active"]:
            continue
        tenant_id = str(tenant["id"])
        with tenant_connection(tenant_id) as connection:
            assets = list(connection.execute(
                "SELECT id,name,kind,criticality FROM cm_assets WHERE tenant_id=%s AND active ORDER BY name", (tenant_id,)
            ).fetchall())
            monitors = list(connection.execute(
                """SELECT id,asset_id,name,monitor_type,active,last_status,last_checked_at,interval_seconds,severity
                FROM cm_monitors WHERE tenant_id=%s ORDER BY name""", (tenant_id,)
            ).fetchall())
            maintenance = list(connection.execute(
                """SELECT asset_id FROM cm_maintenance_windows
                WHERE tenant_id=%s AND active AND now() BETWEEN starts_at AND ends_at""", (tenant_id,)
            ).fetchall())
            open_alerts = {str(row["asset_id"]): row["total"] for row in connection.execute(
                """SELECT m.asset_id,count(*) AS total FROM cm_alerts al JOIN cm_monitors m ON m.id=al.monitor_id
                WHERE al.tenant_id=%s AND al.status='open' GROUP BY m.asset_id""", (tenant_id,)
            ).fetchall()}
        tenant_in_maintenance = any(row["asset_id"] is None for row in maintenance)
        assets_in_maintenance = {str(row["asset_id"]) for row in maintenance if row["asset_id"] is not None}
        for asset in assets:
            asset_id = str(asset["id"])
            services = []
            for monitor in monitors:
                if str(monitor["asset_id"]) != asset_id:
                    continue
                checked = monitor["last_checked_at"]
                stale = checked is None or (now - checked).total_seconds() > max(300, 3 * int(monitor["interval_seconds"]))
                # Section 5 of the monitoring spec: a monitor without reliable data is never counted as OK; a failed
                # critical monitor is "stopped" (red) while a failed non-critical one is only an "alert" (yellow).
                critical = monitor["severity"] in ("high", "disaster")
                if not monitor["active"]:
                    state = "inactive"
                elif stale or monitor["last_status"] == "unknown":
                    state = "unknown"
                elif monitor["last_status"] == "down":
                    state = "stopped" if critical else "alert"
                else:
                    state = "ok"
                services.append({"id": str(monitor["id"]), "name": monitor["name"], "type": monitor["monitor_type"], "state": state,
                                 "critical": critical, "last_checked_at": checked})
            active = [item for item in services if item["state"] != "inactive"]
            counts = {key: sum(1 for item in active if item["state"] == key) for key in ("ok", "stopped", "alert", "unknown")}
            alerts = int(open_alerts.get(asset_id, 0))
            in_maintenance = tenant_in_maintenance or asset_id in assets_in_maintenance
            if in_maintenance:
                status_value = "maintenance"
            elif not active or counts["unknown"] == len(active):
                status_value = "unknown"
            elif counts["stopped"]:
                status_value = "critical"
            elif counts["alert"] or counts["unknown"] or alerts:
                status_value = "attention"
            else:
                status_value = "healthy"
            checks = [item["last_checked_at"] for item in active if item["last_checked_at"]]
            cards.append({
                "asset_id": asset_id, "environment": asset["name"], "kind": asset["kind"], "criticality": asset["criticality"],
                "company_id": tenant_id, "company": tenant["name"], "status": status_value,
                "services_total": len(active), "counts": counts, "open_alerts": alerts,
                "last_checked_at": max(checks) if checks else None, "services": services,
            })
    order = {"critical": 0, "attention": 1, "unknown": 2, "maintenance": 3, "healthy": 4}
    cards.sort(key=lambda card: (order[card["status"]], card["company"], card["environment"], card["asset_id"]))
    summary = {key: sum(1 for card in cards if card["status"] == key) for key in order}
    return {"summary": {"companies": len({card["company_id"] for card in cards}), "environments": len(cards),
                        "services": sum(card["services_total"] for card in cards), **summary},
            "environments": cards, "generated_at": now}


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
    user=authorize_tenant(identity, tenant_id, ("admin",))
    with connect() as connection:
        inactivated = connection.execute("UPDATE cm_tenants SET active=false,updated_at=now() WHERE id=%s AND active RETURNING id", (tenant_id,)).fetchone()
        if not inactivated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','tenant',%s)",(tenant_id,user["id"],tenant_id))


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
            if not payload.active:
                connection.execute(
                    "UPDATE cm_monitors SET active=false WHERE asset_id=%s AND tenant_id=%s AND active",
                    (asset_id, tenant_id),
                )
            connection.execute(
                "INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'update','asset',%s)",
                (tenant_id, user["id"], asset_id),
            )
            return asset
    except UniqueViolation as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset name already exists") from error


@app.delete("/api/v1/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["assets"])
def delete_asset(asset_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    user=authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        inactivated = connection.execute("UPDATE cm_assets SET active=false WHERE id=%s AND tenant_id=%s AND active RETURNING id", (asset_id, tenant_id)).fetchone()
        if not inactivated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
        connection.execute("UPDATE cm_monitors SET active=false WHERE asset_id=%s AND tenant_id=%s AND active",(asset_id,tenant_id))
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id,details) VALUES (%s,%s,'inactivate','asset',%s,%s)",(tenant_id,user["id"],asset_id,Jsonb({"dependent_monitors":"inactivated"})))


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
    user=authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        inactivated = connection.execute("UPDATE cm_monitors SET active=false WHERE id=%s AND tenant_id=%s AND active RETURNING id", (monitor_id, tenant_id)).fetchone()
        if not inactivated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitor not found")
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','monitor',%s)",(tenant_id,user["id"],monitor_id))


@app.post("/api/v1/events", status_code=status.HTTP_202_ACCEPTED, tags=["events"])
def receive_event(payload: CustomEvent, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize_tenant(identity, tenant_id, ("admin", "operator"))
    with tenant_connection(tenant_id) as connection:
        serialized=payload.model_dump(mode="json",by_alias=True)
        payload_hash=canonical_payload_hash(serialized)
        connection_key=str(payload.attributes.get("connection_key") or "default")
        inbox=connection.execute("""INSERT INTO cm_integration_inbox(tenant_id,source,connection_key,external_event_id,occurred_at,payload_hash,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source,connection_key,external_event_id) DO NOTHING RETURNING id""",
            (tenant_id,payload.source,connection_key,payload.event_id,payload.occurred_at,payload_hash,Jsonb(serialized))).fetchone()
        if not inbox:
            existing=connection.execute("SELECT id,payload_hash,state,result FROM cm_integration_inbox WHERE tenant_id=%s AND source=%s AND connection_key=%s AND external_event_id=%s FOR UPDATE",
                (tenant_id,payload.source,connection_key,payload.event_id)).fetchone()
            if existing["payload_hash"]!=payload_hash:
                connection.execute("UPDATE cm_integration_inbox SET state='rejected',last_error='external_event_id_payload_mismatch',processed_at=now() WHERE tenant_id=%s AND id=%s",(tenant_id,existing["id"]))
                connection.commit()
                raise HTTPException(status_code=409,detail="External event id was reused with a different payload")
            return {"status":"duplicate","inbox_id":str(existing["id"]),"original_state":existing["state"],"result":existing["result"]}
        result = ingest_mapped_event(connection, tenant_id, payload)
        inbox_state=result["status"] if result["status"] in ("correlated","quarantined","duplicate") else "failed"
        connection.execute("UPDATE cm_integration_inbox SET state=%s,result=%s,processed_at=now() WHERE tenant_id=%s AND id=%s",(inbox_state,Jsonb(jsonable_encoder(result)),tenant_id,inbox["id"]))
        if result["status"] == "correlated":
            connection.execute(
                "INSERT INTO cm_outbox(tenant_id,topic,payload) VALUES (%s,'event.received',%s)",
                (tenant_id, Jsonb({"event_id": str(result["id"]), "episode_id": str(result["episode_id"])})),
            )
        return {**result,"inbox_id":str(inbox["id"])}


@app.get("/api/v1/alerts", tags=["alerts"])
def list_alerts(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    authorize_tenant(identity, tenant_id)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute("SELECT * FROM cm_alerts WHERE tenant_id=%s AND inactive_at IS NULL ORDER BY opened_at DESC", (tenant_id,)).fetchall())


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
    user=authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        inactivated = connection.execute("UPDATE cm_alerts SET inactive_at=now(),inactive_reason='Administrative inactivation' WHERE id=%s AND tenant_id=%s AND inactive_at IS NULL RETURNING id", (alert_id, tenant_id)).fetchone()
        if not inactivated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','alert',%s)",(tenant_id,user["id"],alert_id))


@app.get("/api/v1/incidents", tags=["incidents"])
def list_incidents(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> list[dict[str, Any]]:
    actor=authorize_tenant(identity, tenant_id)
    scope_sql,scope_params=incident_scope(actor)
    with tenant_connection(tenant_id) as connection:
        return list(connection.execute(f"SELECT * FROM cm_incidents i WHERE i.tenant_id=%s AND i.inactive_at IS NULL AND {scope_sql} ORDER BY i.updated_at DESC", (tenant_id,*scope_params)).fetchall())


@app.patch("/api/v1/incidents/{incident_id}", tags=["incidents"])
def update_incident(incident_id: str, payload: IncidentUpdate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    user = authorize_tenant(identity, tenant_id, ("admin", "operator"))
    scope_sql,scope_params=incident_scope(user)
    with tenant_connection(tenant_id) as connection:
        selected_sla=None
        if "sla_policy_id" in payload.model_fields_set and payload.sla_policy_id:
            selected_sla=connection.execute("SELECT response_minutes,resolution_minutes FROM cm_sla_policies WHERE tenant_id=%s AND id=%s AND active",(tenant_id,payload.sla_policy_id)).fetchone()
            if not selected_sla:
                raise HTTPException(status_code=422,detail="Invalid SLA policy")
        if payload.status=="resolved":
            pending=connection.execute("SELECT count(*) AS total FROM cm_ticket_tasks WHERE tenant_id=%s AND incident_id=%s AND status IN ('pending','in_progress')",(tenant_id,incident_id)).fetchone()["total"]
            solution=connection.execute("SELECT 1 FROM cm_ticket_solutions WHERE tenant_id=%s AND incident_id=%s AND status='proposed'",(tenant_id,incident_id)).fetchone()
            if pending or not solution:
                raise HTTPException(status_code=409,detail="Conclua as tarefas pendentes e registre uma solução antes de resolver o chamado")
        incident = connection.execute(
            f"""UPDATE cm_incidents i SET status=%s,title=COALESCE(%s,title),description=COALESCE(%s,description),
            priority=COALESCE(%s,priority),category=COALESCE(%s,category),
            assignee_user_id=CASE WHEN %s THEN %s ELSE assignee_user_id END,
            assignee_team_id=CASE WHEN %s THEN %s ELSE assignee_team_id END,
            sla_policy_id=CASE WHEN %s THEN %s ELSE sla_policy_id END,
            supplier_id=COALESCE(%s,supplier_id),supplier_protocol=COALESCE(%s,supplier_protocol),
            acknowledged_at=CASE WHEN %s='acknowledged' THEN COALESCE(acknowledged_at,now()) ELSE acknowledged_at END,
            resolved_at=CASE WHEN %s='resolved' THEN COALESCE(resolved_at,now()) ELSE resolved_at END,
            closed_at=CASE WHEN %s='closed' THEN COALESCE(closed_at,now()) ELSE closed_at END,
            response_due_at=CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN response_due_at+(now()-sla_paused_at) ELSE response_due_at END,
            resolution_due_at=CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN resolution_due_at+(now()-sla_paused_at) ELSE resolution_due_at END,
            sla_paused_seconds=sla_paused_seconds+CASE WHEN sla_paused_at IS NOT NULL AND %s NOT IN ('waiting_customer','waiting_supplier') THEN EXTRACT(EPOCH FROM (now()-sla_paused_at))::bigint ELSE 0 END,
            sla_paused_at=CASE WHEN %s IN ('waiting_customer','waiting_supplier') THEN COALESCE(sla_paused_at,now()) ELSE NULL END,
            version=version+1,updated_at=now(),sync_status='pending'
            WHERE id=%s AND tenant_id=%s AND version=%s AND {scope_sql} RETURNING *""",
            (payload.status,payload.title,payload.description,payload.priority,payload.category,
             "assignee_user_id" in payload.model_fields_set,payload.assignee_user_id,
             "assignee_team_id" in payload.model_fields_set,payload.assignee_team_id,
             "sla_policy_id" in payload.model_fields_set,payload.sla_policy_id,payload.supplier_id,payload.supplier_protocol,
             payload.status,payload.status,payload.status,payload.status,payload.status,payload.status,payload.status,
             incident_id,tenant_id,payload.version,*scope_params),
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
        if payload.assignee_user_id:
            connection.execute("""INSERT INTO cm_in_app_notifications(tenant_id,user_id,ticket_id,event,title,body)
                VALUES (%s,%s,%s,'ticket.assigned',%s,%s)
                ON CONFLICT(tenant_id,user_id,ticket_id,event) DO UPDATE SET body=excluded.body,read_at=NULL,created_at=now()""",
                (tenant_id,payload.assignee_user_id,incident_id,"Novo chamado atribuído",incident["title"]))
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
        if selected_sla:
            incident=connection.execute("""UPDATE cm_incidents SET response_due_at=created_at+(%s*interval '1 minute'),
                resolution_due_at=created_at+(%s*interval '1 minute') WHERE tenant_id=%s AND id=%s RETURNING *""",
                (selected_sla["response_minutes"],selected_sla["resolution_minutes"],tenant_id,incident_id)).fetchone()
        return incident


@app.delete("/api/v1/incidents/{incident_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["incidents"])
def delete_incident(incident_id: str, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> None:
    user=authorize_tenant(identity, tenant_id, ("admin",))
    with tenant_connection(tenant_id) as connection:
        inactivated = connection.execute("UPDATE cm_incidents SET inactive_at=now(),inactive_reason='Administrative inactivation',updated_at=now() WHERE id=%s AND tenant_id=%s AND inactive_at IS NULL RETURNING id", (incident_id, tenant_id)).fetchone()
        if not inactivated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'inactivate','incident',%s)",(tenant_id,user["id"],incident_id))


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


@app.post("/api/v1/integrations/glpi/capabilities/refresh", tags=["integrations"])
def refresh_glpi_capabilities(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    actor = authorize_tenant(identity, tenant_id, ("admin", "manager"))
    try:
        manifest = GlpiClient().capabilities()
    except Exception as error:
        with tenant_connection(tenant_id) as connection:
            connection.execute("""INSERT INTO cm_integration_capabilities(tenant_id,integration,last_error)
                VALUES (%s,'glpi',%s) ON CONFLICT(tenant_id,integration) DO UPDATE
                SET last_error=excluded.last_error,discovered_at=now()""",(tenant_id,str(error)))
        raise HTTPException(status_code=502, detail=f"GLPI capability discovery failed: {error}") from error
    with tenant_connection(tenant_id) as connection:
        connection.execute("""INSERT INTO cm_integration_capabilities(tenant_id,integration,version,manifest,last_error)
            VALUES (%s,'glpi',%s,%s,NULL) ON CONFLICT(tenant_id,integration) DO UPDATE
            SET version=excluded.version,manifest=excluded.manifest,last_error=NULL,discovered_at=now()""",
            (tenant_id,manifest.get("version"),Jsonb(manifest)))
        connection.execute("INSERT INTO cm_audit_log(tenant_id,user_id,action,object_type,object_id) VALUES (%s,%s,'capabilities.refresh','integration','glpi')",(tenant_id,actor["id"]))
    return manifest


@app.get("/api/v1/integrations/glpi/capabilities", tags=["integrations"])
def get_glpi_capabilities(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    authorize_tenant(identity, tenant_id, ("admin", "manager", "operator", "viewer"))
    with tenant_connection(tenant_id) as connection:
        row=connection.execute("SELECT version,manifest,discovered_at,last_error FROM cm_integration_capabilities WHERE tenant_id=%s AND integration='glpi'",(tenant_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404,detail="GLPI capabilities have not been discovered")
    return row


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

