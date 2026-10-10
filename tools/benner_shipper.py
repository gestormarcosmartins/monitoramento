"""Envia snapshots do Benner Health Monitor para a API Nexora."""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def send(path: Path, url: str, tenant: str, token: str, historical: bool) -> str:
    endpoint = url.rstrip("/") + "/api/v1/agents/benner/snapshots"
    endpoint += "?" + urllib.parse.urlencode({"activate_alerts": str(not historical).lower()})
    request = urllib.request.Request(
        endpoint, data=path.read_bytes(), method="POST",
        headers={"Content-Type": "application/json", "X-Tenant-ID": tenant, "X-Agent-Token": token},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))["status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Integra o Benner Health Monitor ao Nexora")
    parser.add_argument("archive", type=Path, help="Pasta logs/archive ou um snapshot JSON")
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--historical", action="store_true")
    args = parser.parse_args()
    files = [args.archive] if args.archive.is_file() else sorted(args.archive.rglob("benner_health_*.json"))
    accepted = duplicates = failed = 0
    for path in files:
        try:
            result = send(path, args.url, args.tenant, args.token, args.historical)
            accepted += result == "accepted"
            duplicates += result == "duplicate"
            print(f"{result}: {path.name}")
        except (OSError, urllib.error.HTTPError, ValueError) as error:
            failed += 1
            print(f"failed: {path.name}: {error}", file=sys.stderr)
    print(json.dumps({"files": len(files), "accepted": accepted, "duplicates": duplicates, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
