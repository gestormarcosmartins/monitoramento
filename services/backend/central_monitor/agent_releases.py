"""Atualização remota, assinada e em anéis do agente de monitoramento.

Mesma cadeia de confiança do Business Worker: o Master registra uma versão (o pacote já está em BUSINESS_RELEASES_DIR; o servidor
calcula o SHA-256). A Central oferece a um agente um manifesto assinado (Ed25519) preso àquela empresa, àquele agente, à
plataforma, ao hash, ao tamanho e a uma validade curta. O agente verifica com a chave pública, baixa pela API, confere tamanho e
SHA-256 e só deixa o pacote preparado; a troca (com período de teste e volta automática) é feita pelo script de aplicação, fora
do processo em execução.

Anéis: uma versão nasce `canary`; só agentes do anel canary a recebem. Depois que um agente canary reporta APPLIED e está online,
o Master promove para `stable`. Uma versão `revoked` nunca é oferecida e quem a roda volta para a estável mais nova.
O anel padrão de todo agente é `hold`: nada se atualiza sozinho até alguém escolher o anel.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent_api import agent_identity
from .auth import Identity, current_identity, tenant_header
from .business_monitors import _audit
from .business_releases import FILE_RE, MANIFEST_TTL, MAX_PACKAGE_BYTES, PLATFORMS, VERSION_RE, select_update
from .business_results import canonical, sign_manifest
from .db import connect, tenant_connection
from .settings import get_settings

master_router = APIRouter(prefix="/api/v1/master", tags=["agent-releases"])
agent_router = APIRouter(prefix="/api/v1/agent", tags=["agent-update"])

ONLINE_WINDOW = "10 minutes"


def build_manifest(agent: dict[str, Any], release: dict[str, Any], rollback: bool, now: datetime | None = None) -> dict[str, Any]:
    issued = now or datetime.now(UTC)
    return {"type": "agent_update", "tenant_id": str(agent["tenant_id"]), "agent_id": str(agent["id"]), "version": release["version"],
            "platform": release["platform"], "sha256": release["sha256"], "size_bytes": int(release["size_bytes"]), "rollback": rollback,
            "issued_at": issued.isoformat(), "expires_at": (issued + MANIFEST_TTL).isoformat()}


def envelope(manifest: dict[str, Any]) -> dict[str, str]:
    """Mesmo formato dos jobs: o agente verifica a assinatura sobre exatamente estes bytes e só então interpreta."""
    return {"payload": canonical(manifest).decode(), "signature": sign_manifest(manifest)}


def _releases_dir() -> Path:
    return Path(get_settings().business_releases_dir)


def _hash_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            if size > MAX_PACKAGE_BYTES:
                raise HTTPException(422, "Package exceeds the 200 MB limit")
    return digest.hexdigest(), size


def _platform_master(identity: Identity, tenant_id: str) -> dict[str, Any]:
    """Versões e anéis de agentes são da plataforma: só o Master técnico, sem exigir o módulo de negócios."""
    from .business import _actor
    actor = _actor(identity, tenant_id)
    if not actor.get("is_master"):
        raise HTTPException(403, "Only the Master may manage agent releases")
    return actor


# ------------------------------------------------------------------ operações (usadas pela API e pelo utilitário de linha de comando)
def register_release(version: str, platform: str, file_name: str, notes: str, created_by: Any = None) -> dict[str, Any]:
    if not VERSION_RE.fullmatch(version) or platform not in PLATFORMS or not FILE_RE.fullmatch(file_name):
        raise HTTPException(422, "Invalid version, platform or file name")
    base = _releases_dir().resolve()
    path = (base / file_name).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(422, "Package not found in the releases directory")
    digest, size = _hash_file(path)
    with connect() as db:
        try:
            return db.execute("""INSERT INTO cm_agent_releases(version,platform,file_name,sha256,size_bytes,notes,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,version,platform,file_name,sha256,size_bytes,channel""",
                              (version, platform, file_name, digest, size, notes, created_by)).fetchone()
        except Exception as error:  # unique (version, platform)
            if "cm_agent_releases_version_platform_key" in str(error):
                raise HTTPException(409, "This version already exists for the platform") from error
            raise


def canary_evidence(version: str, platform: str) -> int:
    """Agentes canary (de qualquer empresa) que rodam esta versão, reportaram APPLIED e estão online agora."""
    with connect() as db:
        tenants = [str(r["id"]) for r in db.execute("SELECT id FROM cm_tenants WHERE active").fetchall()]
    total = 0
    for tenant in tenants:
        with tenant_connection(tenant) as db:
            total += db.execute(f"""SELECT count(*) AS n FROM cm_agents WHERE tenant_id=%s AND update_ring='canary' AND platform=%s
                AND version=%s AND update_status='APPLIED' AND update_version=%s AND status='online'
                AND last_seen_at>now()-interval '{ONLINE_WINDOW}'""", (tenant, platform, version, version)).fetchone()["n"]
    return total


def promote_release(release_id: UUID) -> dict[str, Any]:
    with connect() as db:
        release = db.execute("SELECT id,version,platform,channel FROM cm_agent_releases WHERE id=%s", (release_id,)).fetchone()
    if not release:
        raise HTTPException(404, "Release not found")
    if release["channel"] != "canary":
        raise HTTPException(409, f"Only a canary release can be promoted (it is {release['channel']})")
    if canary_evidence(release["version"], release["platform"]) < 1:
        raise HTTPException(409, "Promotion needs at least one online canary agent that applied this version")
    with connect() as db:
        db.execute("UPDATE cm_agent_releases SET channel='stable',promoted_at=now() WHERE id=%s", (release_id,))
    return release


def revoke_release(release_id: UUID) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("UPDATE cm_agent_releases SET channel='revoked',revoked_at=now() WHERE id=%s RETURNING id,version,platform", (release_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Release not found")
    return row


def set_ring(tenant_id: str, agent_id: UUID, ring: str) -> dict[str, Any]:
    with tenant_connection(tenant_id) as db:
        row = db.execute("UPDATE cm_agents SET update_ring=%s WHERE tenant_id=%s AND id=%s RETURNING id,name", (ring, tenant_id, agent_id)).fetchone()
    if not row:
        raise HTTPException(404, "Agent not found")
    return row


# ------------------------------------------------------------------ master
class ReleaseCreate(BaseModel):
    version: str = Field(pattern=r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
    platform: Literal["win-x64", "linux-x64"]
    file_name: str = Field(pattern=r"^[A-Za-z0-9._-]{1,120}$")
    notes: str = Field(default="", max_length=500)


class RevokeBody(BaseModel):
    reason: str = Field(min_length=4, max_length=300)


class RingBody(BaseModel):
    ring: Literal["canary", "stable", "hold"]


@master_router.get("/agent-releases")
def list_releases(tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    _platform_master(identity, tenant_id)
    with connect() as db:
        return list(db.execute("""SELECT id,version,platform,file_name,sha256,size_bytes,channel,notes,created_at,promoted_at,revoked_at
            FROM cm_agent_releases ORDER BY platform,created_at DESC LIMIT 200""").fetchall())


@master_router.post("/agent-releases", status_code=201)
def register(payload: ReleaseCreate, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _platform_master(identity, tenant_id)
    row = register_release(payload.version, payload.platform, payload.file_name, payload.notes, actor["id"])
    with tenant_connection(tenant_id) as db:
        _audit(db, tenant_id, actor["id"], "agent_release_register", "agent_release", row["id"], {"version": payload.version, "platform": payload.platform})
    return row


@master_router.post("/agent-releases/{release_id}/promote")
def promote(release_id: UUID, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _platform_master(identity, tenant_id)
    release = promote_release(release_id)
    with tenant_connection(tenant_id) as db:
        _audit(db, tenant_id, actor["id"], "agent_release_promote", "agent_release", release_id, {"version": release["version"]})
    return {"status": "stable"}


@master_router.post("/agent-releases/{release_id}/revoke")
def revoke(release_id: UUID, payload: RevokeBody, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _platform_master(identity, tenant_id)
    row = revoke_release(release_id)
    with tenant_connection(tenant_id) as db:
        _audit(db, tenant_id, actor["id"], "agent_release_revoke", "agent_release", release_id, {"version": row["version"], "reason": payload.reason})
    return {"status": "revoked"}


@master_router.put("/agents/{agent_id}/update-ring")
def update_ring(agent_id: UUID, payload: RingBody, tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor = _platform_master(identity, tenant_id)
    row = set_ring(tenant_id, agent_id, payload.ring)
    with tenant_connection(tenant_id) as db:
        _audit(db, tenant_id, actor["id"], "agent_update_ring", "agent", agent_id, {"ring": payload.ring, "name": row["name"]})
    return {"ring": payload.ring}


# ------------------------------------------------------------------ agente
def _offer(agent: dict[str, Any], platform: str) -> tuple[dict[str, Any], bool] | None:
    with connect() as db:
        releases = list(db.execute("SELECT id,version,platform,file_name,sha256,size_bytes,channel FROM cm_agent_releases WHERE platform=%s", (platform,)).fetchall())
    return select_update(agent["update_ring"], agent["version"], releases)


@agent_router.get("/update")
def check_update(platform: str = Query(pattern=r"^(win-x64|linux-x64)$"), agent: dict[str, Any] = Depends(agent_identity)):
    tenant_id = str(agent["tenant_id"])
    with tenant_connection(tenant_id) as db:
        db.execute("UPDATE cm_agents SET platform=%s WHERE tenant_id=%s AND id=%s AND platform IS DISTINCT FROM %s", (platform, tenant_id, agent["id"], platform))
    offer = _offer(agent, platform)
    if offer is None:
        return {"update": None}
    release, rollback = offer
    return {"update": envelope(build_manifest(agent, release, rollback))}


@agent_router.get("/releases/{version}/{platform}/download")
def download_release(version: str, platform: str, agent: dict[str, Any] = Depends(agent_identity)):
    if not VERSION_RE.fullmatch(version) or platform not in PLATFORMS:
        raise HTTPException(404, "Release not found")
    offer = _offer(agent, platform)
    if offer is None or offer[0]["version"] != version:  # um agente só baixa o que está sendo oferecido a ele agora
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


@agent_router.post("/update/status")
def report_update(payload: UpdateStatus, agent: dict[str, Any] = Depends(agent_identity)):
    tenant_id = str(agent["tenant_id"])
    with tenant_connection(tenant_id) as db:
        db.execute("""UPDATE cm_agents SET update_status=%s,update_version=%s,update_error=%s,update_reported_at=now()
            WHERE tenant_id=%s AND id=%s""", (payload.status, payload.version, payload.error or None, tenant_id, agent["id"]))
        _audit(db, tenant_id, None, f"agent_update_{payload.status.lower()}", "agent", agent["id"], {"version": payload.version, "error": payload.error})
    return {"status": "recorded"}
