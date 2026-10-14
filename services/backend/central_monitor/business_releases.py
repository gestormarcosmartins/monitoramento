"""Signed remote update of the Business Worker, with a canary ring (spec P0-09).

Trust chain: the technical Master registers a release (the package must already be in BUSINESS_RELEASES_DIR; the server
hashes it). The central offers an update to a worker as an Ed25519-signed manifest bound to that tenant, worker, platform,
hash, size and a short validity: the Worker verifies it with the public key pinned at install time, downloads the package
through the API, checks size and SHA-256 and only stages it. Applying (swap + rollback) is done by the installer scripts,
outside the running process.

Rollout: a release is born `canary`. Only workers in the `canary` ring receive it. After a canary worker reports APPLIED
and is online, the Master promotes it to `stable`. A `revoked` release is never offered and workers running it are told
to go back to the newest stable one (the manifest then carries rollback=true).
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import Identity, current_identity, tenant_header
from .business_monitors import _audit, _master, worker_identity
from .business_results import canonical, sign_manifest
from .db import connect, tenant_connection
from .settings import get_settings

master_router = APIRouter(prefix="/api/v1/master/business", tags=["business-releases"])
worker_router = APIRouter(prefix="/api/v1/agents/business", tags=["business-worker-update"])

VERSION_RE = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
PLATFORMS = ("win-x64", "linux-x64")
MANIFEST_TTL = timedelta(hours=1)
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
CANARY_ONLINE_WINDOW = "10 minutes"


def version_key(version: str | None) -> tuple[int, int, int] | None:
    if not version or not VERSION_RE.fullmatch(version.strip()):
        return None
    major, minor, patch = (int(x) for x in version.strip().split("."))
    return major, minor, patch


def select_update(ring: str, current_version: str | None, releases: list[dict[str, Any]]) -> tuple[dict[str, Any], bool] | None:
    """Pure decision: which release (if any) a worker should move to. Returns (release, rollback).
    `releases` are rows of one platform. A worker with an unparsable version is never updated (fail closed)."""
    current = version_key(current_version)
    if current is None or ring == "hold":
        return None
    allowed = {"canary", "stable"} if ring == "canary" else {"stable"}
    candidates = [r for r in releases if r["channel"] in allowed and version_key(r["version"])]
    revoked = {r["version"] for r in releases if r["channel"] == "revoked"}
    if current_version and current_version.strip() in revoked:
        stable = [r for r in releases if r["channel"] == "stable" and version_key(r["version"]) and version_key(r["version"]) != current]
        stable = [r for r in stable if version_key(r["version"]) < current] or stable
        return (max(stable, key=lambda r: version_key(r["version"])), True) if stable else None
    if not candidates:
        return None
    best = max(candidates, key=lambda r: version_key(r["version"]))
    return (best, False) if version_key(best["version"]) > current else None


def build_manifest(worker: dict[str, Any], release: dict[str, Any], rollback: bool, now: datetime | None = None) -> dict[str, Any]:
    issued = now or datetime.now(UTC)
    return {"type": "worker_update", "tenant_id": str(worker["tenant_id"]), "worker_id": str(worker["id"]), "version": release["version"],
            "platform": release["platform"], "sha256": release["sha256"], "size_bytes": int(release["size_bytes"]), "rollback": rollback,
            "issued_at": issued.isoformat(), "expires_at": (issued + MANIFEST_TTL).isoformat()}


def envelope(manifest: dict[str, Any]) -> dict[str, str]:
    """Same shape as a job: the worker verifies the signature over exactly these bytes, then parses them."""
    return {"payload": canonical(manifest).decode(), "signature": sign_manifest(manifest)}


def _releases_dir() -> Path:
    return Path(get_settings().business_releases_dir)


def _platform_releases(db: Any, platform: str) -> list[dict[str, Any]]:
    return list(db.execute("SELECT id,version,platform,file_name,sha256,size_bytes,channel FROM cm_business_worker_releases WHERE platform=%s", (platform,)).fetchall())


def _hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                raise HTTPException(422, "Package exceeds the 200 MB limit")
    return digest.hexdigest(), size


# ------------------------------------------------------------------ master
class ReleaseCreate(BaseModel):
    version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
    platform: Literal["win-x64", "linux-x64"]
    file_name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,120}$")
    notes: str = Field(default="", max_length=500)


@master_router.get("/releases")
def list_releases(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _master(identity, tenant_id)
    with connect() as db:
        return list(db.execute("""SELECT id,version,platform,file_name,sha256,size_bytes,channel,notes,created_at,promoted_at,revoked_at
            FROM cm_business_worker_releases ORDER BY platform,created_at DESC LIMIT 200""").fetchall())


@master_router.post("/releases", status_code=201)
def register_release(payload: ReleaseCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    base = _releases_dir().resolve()
    path = (base / payload.file_name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(422, "Package not found in the releases directory")
    digest, size = _hash_file(path)
    with connect() as db:
        try:
            row = db.execute("""INSERT INTO cm_business_worker_releases(version,platform,file_name,sha256,size_bytes,notes,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,version,platform,file_name,sha256,size_bytes,channel""",
                             (payload.version, payload.platform, payload.file_name, digest, size, payload.notes, actor["id"])).fetchone()
        except Exception as error:  # unique (version, platform)
            if "cm_business_worker_releases_version_platform_key" in str(error):
                raise HTTPException(409, "This version already exists for the platform") from error
            raise
    with tenant_connection(tenant_id) as audit_db:
        _audit(audit_db, tenant_id, actor["id"], "release_register", "business_worker_release", row["id"], {"version": payload.version, "platform": payload.platform})
    return row


def canary_evidence(version: str, platform: str) -> int:
    """Canary workers (any company) that run this version, reported APPLIED and are online right now."""
    with connect() as db:
        tenants = [str(r["id"]) for r in db.execute("SELECT id FROM cm_tenants WHERE active").fetchall()]
    total = 0
    for tenant in tenants:
        with tenant_connection(tenant) as db:
            total += db.execute(f"""SELECT count(*) AS n FROM cm_business_workers WHERE tenant_id=%s AND update_ring='canary' AND platform=%s
                AND version=%s AND update_status='APPLIED' AND update_version=%s AND status='ONLINE'
                AND last_heartbeat_at>now()-interval '{CANARY_ONLINE_WINDOW}'""", (tenant, platform, version, version)).fetchone()["n"]
    return total


@master_router.post("/releases/{release_id}/promote")
def promote_release(release_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with connect() as db:
        release = db.execute("SELECT id,version,platform,channel FROM cm_business_worker_releases WHERE id=%s", (release_id,)).fetchone()
    if not release:
        raise HTTPException(404, "Release not found")
    if release["channel"] != "canary":
        raise HTTPException(409, f"Only a canary release can be promoted (it is {release['channel']})")
    if canary_evidence(release["version"], release["platform"]) < 1:
        raise HTTPException(409, "Promotion needs at least one online canary worker that applied this version")
    with connect() as db:
        db.execute("UPDATE cm_business_worker_releases SET channel='stable',promoted_at=now() WHERE id=%s", (release_id,))
    with tenant_connection(tenant_id) as audit_db:
        _audit(audit_db, tenant_id, actor["id"], "release_promote", "business_worker_release", release_id, {"version": release["version"]})
    return {"status": "stable"}


class RevokeBody(BaseModel):
    reason: str = Field(min_length=4, max_length=300)


@master_router.post("/releases/{release_id}/revoke")
def revoke_release(release_id: UUID, payload: RevokeBody, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with connect() as db:
        row = db.execute("UPDATE cm_business_worker_releases SET channel='revoked',revoked_at=now() WHERE id=%s RETURNING version", (release_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Release not found")
    with tenant_connection(tenant_id) as audit_db:
        _audit(audit_db, tenant_id, actor["id"], "release_revoke", "business_worker_release", release_id, {"version": row["version"], "reason": payload.reason})
    return {"status": "revoked"}


class RingBody(BaseModel):
    ring: Literal["canary", "stable", "hold"]


@master_router.put("/workers/{worker_id}/update-ring")
def set_update_ring(worker_id: UUID, payload: RingBody, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _master(identity, tenant_id)
    with tenant_connection(tenant_id) as db:
        row = db.execute("UPDATE cm_business_workers SET update_ring=%s WHERE tenant_id=%s AND id=%s AND status<>'REVOKED' RETURNING id",
                         (payload.ring, tenant_id, worker_id)).fetchone()
        if not row:
            raise HTTPException(404, "Worker not found")
        _audit(db, tenant_id, actor["id"], "update_ring", "business_worker", worker_id, {"ring": payload.ring})
    return {"ring": payload.ring}


# ------------------------------------------------------------------ worker
def _offer(worker: dict[str, Any], platform: str) -> tuple[dict[str, Any], bool] | None:
    with connect() as db:
        releases = _platform_releases(db, platform)
    return select_update(worker["update_ring"], worker["version"], releases)


@worker_router.get("/update")
def check_update(platform: str = Query(pattern=r"^(win-x64|linux-x64)$"), worker: dict[str, Any] = Depends(worker_identity)):
    tenant_id = str(worker["tenant_id"])
    with tenant_connection(tenant_id) as db:
        db.execute("UPDATE cm_business_workers SET platform=%s WHERE tenant_id=%s AND id=%s AND platform IS DISTINCT FROM %s", (platform, tenant_id, worker["id"], platform))
    offer = _offer(worker, platform)
    if offer is None:
        return {"update": None}
    release, rollback = offer
    return {"update": envelope(build_manifest(worker, release, rollback))}


@worker_router.get("/releases/{version}/{platform}/download")
def download_release(version: str, platform: str, worker: dict[str, Any] = Depends(worker_identity)):
    if not VERSION_RE.fullmatch(version) or platform not in PLATFORMS:
        raise HTTPException(404, "Release not found")
    offer = _offer(worker, platform)
    if offer is None or offer[0]["version"] != version:  # a worker may only fetch what it is currently offered
        raise HTTPException(404, "Release not found")
    base = _releases_dir().resolve()
    path = (base / offer[0]["file_name"]).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(404, "Release not found")
    return FileResponse(path, media_type="application/octet-stream", filename=offer[0]["file_name"], headers={"X-Package-SHA256": offer[0]["sha256"]})


class UpdateStatus(BaseModel):
    version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
    status: Literal["DOWNLOADED", "APPLIED", "FAILED", "ROLLED_BACK"]
    error: str = Field(default="", max_length=200)


@worker_router.post("/update/status")
def report_update(payload: UpdateStatus, worker: dict[str, Any] = Depends(worker_identity)):
    tenant_id = str(worker["tenant_id"])
    with tenant_connection(tenant_id) as db:
        db.execute("""UPDATE cm_business_workers SET update_status=%s,update_version=%s,update_error=%s,update_reported_at=now()
            WHERE tenant_id=%s AND id=%s""", (payload.status, payload.version, payload.error or None, tenant_id, worker["id"]))
        _audit(db, tenant_id, None, f"worker_update_{payload.status.lower()}", "business_worker", worker["id"], {"version": payload.version, "error": payload.error})
    return {"status": "recorded"}
