import base64
import json
import unittest
from pathlib import Path
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from central_monitor import agent_release_cli, agent_releases as ar, business_results

KEY = Ed25519PrivateKey.from_seed(bytes(range(32))) if hasattr(Ed25519PrivateKey, "from_seed") else Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


def agent(**over):
    base = {"id": uuid4(), "tenant_id": uuid4(), "version": "2.2.0", "update_ring": "canary"}
    base.update(over)
    return base


def release(version="2.2.1", channel="stable", platform="win-x64"):
    return {"version": version, "platform": platform, "channel": channel, "sha256": "a" * 64, "size_bytes": 1234, "file_name": f"a-{version}.zip"}


class ManifestTests(unittest.TestCase):
    def test_manifest_is_bound_to_tenant_agent_platform_hash_and_size(self):
        who = agent()
        now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        manifest = ar.build_manifest(who, release(), False, now)
        self.assertEqual(manifest["type"], "agent_update")
        self.assertEqual((manifest["tenant_id"], manifest["agent_id"]), (str(who["tenant_id"]), str(who["id"])))
        self.assertEqual((manifest["version"], manifest["platform"], manifest["sha256"], manifest["size_bytes"]), ("2.2.1", "win-x64", "a" * 64, 1234))
        self.assertFalse(manifest["rollback"])
        self.assertEqual(datetime.fromisoformat(manifest["expires_at"]) - datetime.fromisoformat(manifest["issued_at"]), timedelta(hours=1))

    def test_envelope_is_signed_over_exactly_the_payload_bytes(self):
        with patch.object(business_results, "_private_key", return_value=KEY):
            env = ar.envelope(ar.build_manifest(agent(), release(), False))
        KEY.public_key().verify(base64.b64decode(env["signature"]), env["payload"].encode())  # lança se a assinatura não bater
        self.assertEqual(json.loads(env["payload"])["type"], "agent_update")
        tampered = env["payload"].replace('"version":"2.2.1"', '"version":"9.9.9"')
        self.assertNotEqual(tampered, env["payload"])
        with self.assertRaises(Exception):
            KEY.public_key().verify(base64.b64decode(env["signature"]), tampered.encode())


class OfferTests(unittest.TestCase):
    def test_default_ring_is_hold_so_nothing_updates_until_someone_chooses(self):
        migration = (Path(__file__).resolve().parents[1] / "central_monitor" / "migrations" / "057_agent_releases.sql").read_text(encoding="utf-8")
        self.assertIn("update_ring text NOT NULL DEFAULT 'hold'", migration)

    def test_hold_ring_gets_no_offer_and_canary_gets_canary_and_stable(self):
        releases = [release("2.2.1", "canary"), release("2.2.0", "stable")]
        self.assertIsNone(ar.select_update("hold", "2.1.0", releases))
        self.assertEqual(ar.select_update("canary", "2.2.0", releases)[0]["version"], "2.2.1")
        self.assertIsNone(ar.select_update("stable", "2.2.0", releases))  # a nova ainda é canary

    def test_old_agents_with_a_fixed_version_string_never_match_a_newer_unparsable_version(self):
        self.assertIsNone(ar.select_update("canary", "garbage", [release("2.2.1", "stable")]))


class PublishingContractTests(unittest.TestCase):
    def test_register_refuses_path_tricks_and_missing_files(self):
        for bad in ("../x.zip", "a/b.zip", ".." + chr(92) + "x.zip", ""):
            with self.assertRaises(HTTPException) as caught:
                ar.register_release("2.2.1", "win-x64", bad, "")
            self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            ar.register_release("2.2", "win-x64", "ok.zip", "")
        with self.assertRaises(HTTPException):
            ar.register_release("2.2.1", "mac", "ok.zip", "")

    def test_routes_exist(self):
        master = {(tuple(sorted(r.methods)), r.path) for r in ar.master_router.routes}
        agent_routes = {(tuple(sorted(r.methods)), r.path) for r in ar.agent_router.routes}
        self.assertIn((("GET",), "/api/v1/master/agent-releases"), master)
        self.assertIn((("POST",), "/api/v1/master/agent-releases"), master)
        self.assertIn((("PUT",), "/api/v1/master/agents/{agent_id}/update-ring"), master)
        self.assertIn((("GET",), "/api/v1/agent/update"), agent_routes)
        self.assertIn((("GET",), "/api/v1/agent/releases/{version}/{platform}/download"), agent_routes)
        self.assertIn((("POST",), "/api/v1/agent/update/status"), agent_routes)

    def test_every_agent_route_requires_the_agent_identity(self):
        for route in ar.agent_router.routes:
            self.assertIn("agent_identity", [d.call.__name__ for d in route.dependant.dependencies], route.path)

    def test_every_master_route_requires_a_logged_in_identity(self):
        for route in ar.master_router.routes:
            self.assertIn("current_identity", [d.call.__name__ for d in route.dependant.dependencies], route.path)

    def test_cli_parses_the_documented_commands(self):
        parser = agent_release_cli.build_parser()
        self.assertEqual(parser.parse_args(["ring", "--agent", "JRI - SRVJRIBD", "--ring", "canary"]).ring, "canary")
        self.assertEqual(parser.parse_args(["register", "--version", "2.2.1", "--platform", "win-x64", "--file", "a.zip"]).file, "a.zip")
        with self.assertRaises(SystemExit):
            parser.parse_args(["ring", "--agent", "x", "--ring", "todos"])


if __name__ == "__main__":
    unittest.main()
