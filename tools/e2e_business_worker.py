"""End-to-end check: central API (uvicorn) + PostgreSQL + the real Business Worker (.NET) running a monitor.

Needs: pip install pixeltable-pgserver (portable PostgreSQL), the backend requirements, and a built worker:
    dotnet build agent/Nexora.BusinessWorker -c Release
Run from the repository root:
    python tools/e2e_business_worker.py [--dotnet PATH_TO_dotnet.exe]

Everything runs on loopback against a throw-away database; nothing is deployed or contacted outside this machine.
"""
import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend"))
parser = argparse.ArgumentParser()
parser.add_argument("--dotnet", default="dotnet")
parser.add_argument("--port", type=int, default=8099)
args = parser.parse_args()

import pixeltable_pgserver as pgserver  # noqa: E402

DATA = pathlib.Path(tempfile.mkdtemp(prefix="nx-e2e-pg-"))
server = pgserver.get_server(DATA, cleanup_mode="stop")
host_port = server.get_uri().split("@")[1].split("/")[0]
host, port = host_port.split(":")
for statement in (
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='central_app') THEN CREATE ROLE central_app LOGIN PASSWORD 'pw' NOSUPERUSER; END IF; END $$;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='erp_ro') THEN CREATE ROLE erp_ro LOGIN PASSWORD 'ro' NOSUPERUSER; END IF; END $$;",
    "DROP DATABASE IF EXISTS central_monitor;", "CREATE DATABASE central_monitor OWNER central_app;",
    "DROP DATABASE IF EXISTS erp_demo;", "CREATE DATABASE erp_demo;",
):
    server.psql(statement)

import psycopg  # noqa: E402

with psycopg.connect(f"postgresql://postgres:@{host_port}/erp_demo", autocommit=True) as erp:
    erp.execute("CREATE TABLE contas(documento text, cliente text, cpf text, valor_vencido numeric(14,2), vencimento date)")
    erp.execute("""INSERT INTO contas VALUES ('CR-001','Exemplo A',NULL,700.50,current_date - 10),
        ('CR-002','Exemplo B','000.000.000-00',250.00,current_date - 3), ('CR-003','Futuro',NULL,999.99,current_date + 30)""")
    erp.execute("CREATE TABLE gravavel(x int)")
    erp.execute("GRANT SELECT ON contas TO erp_ro")  # read-only login: no write grants anywhere

signing_seed = base64.b64encode(os.urandom(32)).decode()
env = dict(os.environ, CENTRAL_DATABASE_URL=f"postgresql://central_app:pw@{host_port}/central_monitor", REDIS_URL="redis://localhost:6399/0",
           CENTRAL_SECRET_KEY="x" * 40, ZABBIX_API_URL="http://z", ZABBIX_API_TOKEN="t", BUSINESS_SIGNING_KEY=signing_seed,
           PUSH_TOKEN_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode(), PYTHONPATH=str(ROOT / "services" / "backend"))
os.environ.update(env)

from central_monitor import business, business_monitors as bm, db  # noqa: E402
from central_monitor.auth import Identity  # noqa: E402
from central_monitor.db import connect, tenant_connection  # noqa: E402
from central_monitor.main import ensure_user  # noqa: E402


def check(condition, label):
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        raise SystemExit(1)


db.migrate()
with connect() as c:
    ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
    tenant = str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,'Empresa E2E','e2e-'||substr(md5(random()::text),1,6)) RETURNING id", (ws,)).fetchone()["id"])
master_ident = Identity(subject="e2e-master", email="m@e2e.test", display_name="Master")
master = ensure_user(master_ident)
business.enable_business(business.EnableChange(enabled=True, reason="teste e2e local"), tenant, master_ident)
with tenant_connection(tenant) as c:
    indicator = c.execute("""INSERT INTO cm_business_indicators(tenant_id,code,name,unit,direction,warning_value,critical_value,stale_after_seconds,owner_user_id)
        VALUES (%s,'contas_vencidas','Contas vencidas','reais','above',100,5000,3600,%s) RETURNING id""", (tenant, master["id"])).fetchone()["id"]
    c.execute("INSERT INTO cm_business_states(tenant_id,indicator_id) VALUES (%s,%s)", (tenant, indicator))

worker = bm.create_worker(bm.WorkerCreate(name="worker-e2e"), tenant, master_ident)
source = bm.create_source(bm.SourceCreate(name="erp", worker_id=worker["id"], dialect="postgres", database_ref="erp_principal"), tenant, master_ident)
monitor = bm.create_monitor(bm.MonitorCreate(
    name="contas-vencidas", indicator_id=indicator, source_id=source["id"], interval_seconds=60,
    query="SELECT documento, cliente, cpf, valor_vencido FROM contas WHERE vencimento < @hoje ORDER BY documento", parameters={"hoje": "$today"},
    columns=[{"name": "documento", "type": "string"}, {"name": "cliente", "type": "string"}, {"name": "cpf", "type": "string"},
             {"name": "valor_vencido", "type": "decimal"}],
    metric={"kind": "sum", "column": "valor_vencido"}), tenant, master_ident)
bm.test_revision(monitor["id"], 1, tenant, master_ident)

RELEASES = pathlib.Path(tempfile.mkdtemp(prefix="nx-e2e-rel-"))
UPDATES = pathlib.Path(tempfile.mkdtemp(prefix="nx-e2e-upd-"))
env["BUSINESS_RELEASES_DIR"] = str(RELEASES)
api = subprocess.Popen([sys.executable, "-m", "uvicorn", "central_monitor.main:app", "--port", str(args.port), "--log-level", "warning"],
                       env=env, cwd=str(ROOT / "services" / "backend"))
worker_proc = None
try:
    base = f"http://127.0.0.1:{args.port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/v1/health/live", timeout=2)
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("API did not start")
    headers = {"X-Tenant-ID": tenant, "X-Worker-ID": str(worker["id"]), "X-Worker-Token": worker["worker_token"]}
    pinned = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/v1/agents/business/signing-key", headers=headers)))["public_key"]

    dll = ROOT / "agent" / "Nexora.BusinessWorker" / "bin" / "Release" / "net8.0" / "Nexora.BusinessWorker.dll"
    wenv = dict(os.environ, NEXORA_BW_SERVER_URL=base, NEXORA_BW_TENANT_ID=tenant, NEXORA_BW_WORKER_ID=str(worker["id"]),
                NEXORA_BW_SIGNING_KEY=pinned, NEXORA_BW_TOKEN=worker["worker_token"], NEXORA_BW_POLL_SECONDS="5",
                NEXORA_BW_ALLOW_PLAINTEXT_LOOPBACK_DB="1",
                NEXORA_BW_UPDATE_DIRECTORY=str(UPDATES), NEXORA_BW_UPDATE_START_DELAY_SECONDS="1", NEXORA_BW_UPDATE_CHECK_MINUTES="5", NEXORA_BW_UPDATE_CHECK_SECONDS="3",
                NEXORA_BW_DB_ERP_PRINCIPAL=f"Host={host};Port={port};Database=erp_demo;Username=erp_ro;Password=ro",
                DOTNET_ROOT=str(pathlib.Path(args.dotnet).parent))
    worker_proc = subprocess.Popen([args.dotnet, str(dll)], env=wenv, cwd=str(dll.parent))

    def wait(label, predicate, seconds=90):
        deadline = time.time() + seconds
        while time.time() < deadline:
            bm.run_scheduler(connect)
            value = predicate()
            if value:
                return value
            if worker_proc.poll() is not None:
                raise SystemExit(f"worker exited early with code {worker_proc.returncode}")
            time.sleep(2)
        raise SystemExit(f"TIMEOUT waiting for: {label}")

    def revision_state():
        with tenant_connection(tenant) as c:
            return c.execute("SELECT state FROM cm_business_monitor_revisions WHERE monitor_id=%s AND revision=1", (monitor["id"],)).fetchone()["state"]

    wait("test execution to complete", lambda: revision_state() == "TESTED")
    check(True, "worker ran the test execution through the real API (revision TESTED)")
    bm.publish_revision(monitor["id"], 1, tenant, master_ident)

    def done():
        with tenant_connection(tenant) as c:
            return c.execute("SELECT * FROM cm_business_executions WHERE monitor_id=%s AND NOT is_test AND state='SUCCEEDED'", (monitor["id"],)).fetchone()

    execution = wait("scheduled execution to succeed", done)
    check(execution["row_count"] == 2 and execution["truncated"] is False, "2 overdue rows returned (future one filtered by the bound $today parameter)")
    with tenant_connection(tenant) as c:
        result = c.execute("SELECT columns,rows FROM cm_business_execution_results WHERE execution_id=%s", (execution["id"],)).fetchone()
        state = c.execute("SELECT state,last_value FROM cm_business_states WHERE indicator_id=%s", (indicator,)).fetchone()
        beat = c.execute("SELECT status,version,last_heartbeat_at FROM cm_business_workers WHERE id=%s", (worker["id"],)).fetchone()
    check([c["name"] for c in result["columns"]] == ["documento", "cliente", "cpf", "valor_vencido"], "columns present and ordered")
    check(result["rows"][0] == {"documento": "CR-001", "cliente": "Exemplo A", "cpf": None, "valor_vencido": "700.50"},
          "null preserved (cpf), exact decimal as text")
    check(str(state["last_value"]) == "950.500000" and state["state"] == "atencao", "indicator fed: sum=950.50 -> atencao")
    check(beat["status"] == "ONLINE" and beat["version"], "independent heartbeat registered")

    # A write attempt must fail at the database even if a query slipped past validation: read-only login + session.
    import psycopg as pg
    try:
        with pg.connect(f"postgresql://erp_ro:ro@{host_port}/erp_demo") as bad:
            bad.execute("INSERT INTO gravavel VALUES (1)")
        check(False, "read-only login cannot write")
    except pg.errors.InsufficientPrivilege:
        check(True, "the read-only database login cannot write")

    # ---- signed remote update: register -> canary offer -> download -> verify -> stage -> restart request -> re-verification
    import hashlib
    from central_monitor import business_releases as brel
    platform = "win-x64" if os.name == "nt" else "linux-x64"
    package = b"fake-release-package-1.9.0"
    (RELEASES / "w-1.9.0.pkg").write_bytes(package)
    brel._releases_dir = lambda: RELEASES  # this process registers the release; the API process reads BUSINESS_RELEASES_DIR
    release = brel.register_release(brel.ReleaseCreate(version="1.9.0", platform=platform, file_name="w-1.9.0.pkg"), tenant, master_ident)
    check(release["channel"] == "canary", "a new release is born canary")
    deadline = time.time() + 20
    time.sleep(8)  # stable-ring worker: the canary release must NOT be staged
    check(not (UPDATES / "ready" / "pending.json").exists(), "a stable-ring worker is not offered a canary release")
    brel.set_update_ring(worker["id"], brel.RingBody(ring="canary"), tenant, master_ident)
    deadline = time.time() + 120
    while time.time() < deadline and not (UPDATES / "ready" / "pending.json").exists():
        if worker_proc.poll() is not None and not (UPDATES / "ready" / "pending.json").exists():
            raise SystemExit(f"worker exited before staging (code {worker_proc.returncode})")
        time.sleep(2)
    check((UPDATES / "ready" / "pending.json").exists(), "canary worker downloaded and staged the signed update over real HTTP")
    check((UPDATES / "ready" / "package.pkg").read_bytes() == package, "staged package is byte-identical")
    check(hashlib.sha256(package).hexdigest() == release["sha256"], "central hash matches the package")
    try:
        worker_proc.wait(60)
    except subprocess.TimeoutExpired:
        raise SystemExit("worker did not request a restart after staging")
    check(worker_proc.returncode == 75, "worker exited with code 75 to apply the update on restart")
    with tenant_connection(tenant) as c:
        status = c.execute("SELECT update_status,update_version,platform FROM cm_business_workers WHERE id=%s", (worker["id"],)).fetchone()
    check((status["update_status"], status["update_version"], status["platform"]) == ("DOWNLOADED", "1.9.0", platform), "central recorded DOWNLOADED 1.9.0")
    verdict = subprocess.run([args.dotnet, str(dll), "--verify-staged", str(UPDATES)], env=wenv, cwd=str(dll.parent), capture_output=True, text=True)
    check(verdict.stdout.strip().splitlines()[-1] == "ok" and verdict.returncode == 0, "the installed binary re-verifies the staged package (--verify-staged = ok)")
    (UPDATES / "ready" / "package.pkg").write_bytes(b"tampered-after-staging-xx")
    verdict = subprocess.run([args.dotnet, str(dll), "--verify-staged", str(UPDATES)], env=wenv, cwd=str(dll.parent), capture_output=True, text=True)
    check(verdict.returncode == 3 and verdict.stdout.strip().splitlines()[-1] in ("size_mismatch", "hash_mismatch"), "a package swapped after staging is refused")
    print("E2E OK")
finally:
    for proc in (worker_proc, api):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()
