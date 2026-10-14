"""Generate signed job vectors for the Business Worker tests, using the REAL central signing code.

The C# tests verify these payloads with the pinned public key, so any drift between the central
canonicalization/signature and the worker verifier fails the test suite.

    python tools/gen_worker_vectors.py        (from the repository root)
"""
import base64
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "backend"))
os.environ.update({
    "CENTRAL_DATABASE_URL": "postgresql://x:x@localhost/x", "REDIS_URL": "redis://localhost/0", "CENTRAL_SECRET_KEY": "x" * 40,
    "ZABBIX_API_URL": "http://z", "ZABBIX_API_TOKEN": "t",
    "BUSINESS_SIGNING_KEY": base64.b64encode(bytes([7]) * 32).decode(),  # test-only seed
})
from central_monitor import business_releases as rel  # noqa: E402
from central_monitor import business_results as br  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
WORKER = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
NOW = "2026-10-14T10:00:00+00:00"


def manifest(**over):
    base = {
        "execution_id": "44444444-4444-4444-4444-444444444444", "monitor_id": "55555555-5555-5555-5555-555555555555", "revision": 3,
        "tenant_id": TENANT, "worker_id": WORKER, "expires_at": "2026-10-14T10:05:00+00:00", "lease_expires_at": "2026-10-14T10:01:30+00:00",
        "database_ref": "erp_principal", "dialect": "sqlserver",
        "query": "SELECT TOP (500) documento, cliente, valor_vencido FROM dbo.vw_nexora_cr WHERE vencimento < @hoje AND obs = N'ação'",
        "parameters": {"hoje": "2026-10-14", "limite": 10, "ativo": True, "nada": None},
        "constraints": {"timeout_seconds": 15, "max_rows": 500, "max_bytes": 5242880},
        "column_schema": [{"name": "documento", "type": "string"}, {"name": "cliente", "type": "string"}, {"name": "valor_vencido", "type": "decimal"}],
        "test": False, "correlation_id": "66666666-6666-6666-6666-666666666666",
    }
    return {**base, **over}


def signed(m):
    payload = br.canonical(m).decode()
    return {"payload": payload, "signature": br.sign_manifest(m)}


vectors = {
    "valid": signed(manifest()),
    "wrong_tenant": signed(manifest(tenant_id=OTHER)),
    "wrong_worker": signed(manifest(worker_id=OTHER)),
    "expired": signed(manifest(expires_at="2026-10-14T09:59:00+00:00")),
    "lease_expired": signed(manifest(lease_expires_at="2026-10-14T09:59:59+00:00")),
    "limits_too_big": signed(manifest(constraints={"timeout_seconds": 600, "max_rows": 500, "max_bytes": 5242880})),
    "bad_dialect": signed(manifest(dialect="oracle")),
}
# --- signed update manifests (business_releases.build_manifest / envelope: the central's real code) ---
import hashlib  # noqa: E402
from datetime import datetime  # noqa: E402

PACKAGE = b"package-1.1.0"
UPDATE_NOW = datetime.fromisoformat("2026-10-14T09:30:00+00:00")


def update(platform, version="1.1.0", rollback=False, tenant=TENANT, worker=WORKER, issued=UPDATE_NOW):
    release = {"version": version, "platform": platform, "sha256": hashlib.sha256(PACKAGE).hexdigest(), "size_bytes": len(PACKAGE)}
    m = rel.build_manifest({"tenant_id": tenant, "id": worker}, release, rollback, issued)
    return rel.envelope(m)


updates = {}
for platform in ("win-x64", "linux-x64"):
    updates[f"ok_{platform}"] = update(platform)
    updates[f"rollback_{platform}"] = update(platform, version="0.9.0", rollback=True)
    updates[f"downgrade_{platform}"] = update(platform, version="0.9.0")
    updates[f"same_{platform}"] = update(platform, version="1.0.0")
    updates[f"wrong_tenant_{platform}"] = update(platform, tenant=OTHER)
    updates[f"wrong_worker_{platform}"] = update(platform, worker=OTHER)
    updates[f"old_{platform}"] = update(platform, issued=datetime.fromisoformat("2026-10-12T08:00:00+00:00"))
    updates[f"expired_offer_{platform}"] = update(platform, issued=datetime.fromisoformat("2026-10-14T08:00:00+00:00"))
updates["wrong_platform_win-x64"] = update("linux-x64")
updates["wrong_platform_linux-x64"] = update("win-x64")

out = {"public_key": br.public_key_b64(), "tenant_id": TENANT, "worker_id": WORKER, "now": NOW, "vectors": vectors, "updates": updates, "package": PACKAGE.decode()}
target = ROOT / "agent" / "Nexora.BusinessWorker.Tests" / "TestVectors.json"
target.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print("wrote", target)
