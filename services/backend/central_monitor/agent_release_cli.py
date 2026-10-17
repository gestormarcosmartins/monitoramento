"""Utilitário de linha de comando para publicar versões do agente (até existir a tela do painel).

Roda dentro do container da API, onde o diretório de pacotes (BUSINESS_RELEASES_DIR) e o banco já estão configurados:

  docker compose exec -T api python -m central_monitor.agent_release_cli list
  docker compose exec -T api python -m central_monitor.agent_release_cli register --version 2.2.1 --platform win-x64 --file Nexora-Agent-2.2.1-win-x64.zip --notes "..."
  docker compose exec -T api python -m central_monitor.agent_release_cli ring --agent "JRI - SRVJRIBD" --ring canary
  docker compose exec -T api python -m central_monitor.agent_release_cli promote --version 2.2.1 --platform win-x64
  docker compose exec -T api python -m central_monitor.agent_release_cli revoke --version 2.2.1 --platform win-x64 --reason "motivo"

Nada aqui apaga dados. As mesmas operações existem na API do Master; este utilitário usa o mesmo código.
"""
from __future__ import annotations

import argparse
import sys

from fastapi import HTTPException

from . import agent_releases as ar
from .db import connect, tenant_connection


def _release_id(version: str, platform: str):
    with connect() as db:
        row = db.execute("SELECT id FROM cm_agent_releases WHERE version=%s AND platform=%s", (version, platform)).fetchone()
    if not row:
        raise HTTPException(404, "Release not found")
    return row["id"]


def _agents() -> list[dict]:
    with connect() as db:
        tenants = db.execute("SELECT id,name FROM cm_tenants WHERE active ORDER BY name").fetchall()
    found = []
    for tenant in tenants:
        with tenant_connection(str(tenant["id"])) as db:
            for row in db.execute("""SELECT id,name,version,update_ring,platform,update_status,update_version,update_error,status,last_seen_at
                FROM cm_agents WHERE tenant_id=%s ORDER BY name""", (str(tenant["id"]),)).fetchall():
                found.append({**row, "tenant_id": str(tenant["id"]), "tenant_name": tenant["name"]})
    return found


def cmd_list(_: argparse.Namespace) -> None:
    with connect() as db:
        releases = db.execute("SELECT version,platform,channel,file_name,left(sha256,12) AS sha,size_bytes,created_at::timestamp(0) AS criada FROM cm_agent_releases ORDER BY platform,created_at DESC").fetchall()
    print("== versões publicadas")
    for r in releases:
        print(f"  {r['version']:<9} {r['platform']:<10} {r['channel']:<8} {r['file_name']}  {r['size_bytes']} bytes  sha {r['sha']}…  {r['criada']}")
    if not releases:
        print("  (nenhuma)")
    print("== agentes (anel, versão e último resultado de atualização)")
    for a in _agents():
        print(f"  {a['tenant_name']:<8} {a['name']:<26} anel={a['update_ring']:<6} versão={a['version'] or '-':<8} {a['status']:<8} atualização={a['update_status'] or '-'} {a['update_version'] or ''} {a['update_error'] or ''}")


def cmd_register(args: argparse.Namespace) -> None:
    row = ar.register_release(args.version, args.platform, args.file, args.notes)
    print(f"registrada: {row['version']} {row['platform']} canal={row['channel']} sha256={row['sha256']} {row['size_bytes']} bytes")


def cmd_promote(args: argparse.Namespace) -> None:
    release = ar.promote_release(_release_id(args.version, args.platform))
    print(f"promovida para stable: {release['version']} {release['platform']}")


def cmd_revoke(args: argparse.Namespace) -> None:
    row = ar.revoke_release(_release_id(args.version, args.platform))
    print(f"revogada: {row['version']} {row['platform']} (motivo: {args.reason})")


def cmd_ring(args: argparse.Namespace) -> None:
    matches = [a for a in _agents() if a["name"] == args.agent]
    if len(matches) != 1:
        raise HTTPException(404 if not matches else 409, f"{len(matches)} agente(s) com o nome {args.agent!r}")
    ar.set_ring(matches[0]["tenant_id"], matches[0]["id"], args.ring)
    print(f"anel de {args.agent}: {args.ring}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_release_cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    reg = sub.add_parser("register")
    reg.add_argument("--version", required=True); reg.add_argument("--platform", required=True, choices=ar.PLATFORMS)
    reg.add_argument("--file", required=True); reg.add_argument("--notes", default="")
    reg.set_defaults(func=cmd_register)
    for name, func in (("promote", cmd_promote), ("revoke", cmd_revoke)):
        p = sub.add_parser(name)
        p.add_argument("--version", required=True); p.add_argument("--platform", required=True, choices=ar.PLATFORMS)
        if name == "revoke":
            p.add_argument("--reason", required=True)
        p.set_defaults(func=func)
    ring = sub.add_parser("ring")
    ring.add_argument("--agent", required=True); ring.add_argument("--ring", required=True, choices=("canary", "stable", "hold"))
    ring.set_defaults(func=cmd_ring)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except HTTPException as error:
        print(f"erro: {error.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
