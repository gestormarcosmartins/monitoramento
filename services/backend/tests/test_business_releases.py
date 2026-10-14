import unittest
from datetime import UTC, datetime

from central_monitor import business_releases as br


def rel(version, channel="stable", platform="linux-x64"):
    return {"id": version, "version": version, "platform": platform, "channel": channel, "sha256": "a" * 64, "size_bytes": 10, "file_name": f"w-{version}.tar.gz"}


class SelectUpdateTests(unittest.TestCase):
    def test_stable_ring_only_sees_stable_releases(self):
        releases = [rel("1.0.0"), rel("1.1.0", "canary")]
        chosen, rollback = br.select_update("stable", "1.0.0", releases) or (None, None)
        self.assertIsNone(chosen)  # the newer one is canary only
        chosen, rollback = br.select_update("stable", "0.9.0", releases)
        self.assertEqual((chosen["version"], rollback), ("1.0.0", False))

    def test_canary_ring_sees_canary_and_stable_and_takes_the_newest(self):
        releases = [rel("1.0.0"), rel("1.1.0", "canary"), rel("1.0.5")]
        chosen, rollback = br.select_update("canary", "1.0.0", releases)
        self.assertEqual((chosen["version"], rollback), ("1.1.0", False))

    def test_hold_ring_and_unparsable_versions_never_update(self):
        releases = [rel("2.0.0")]
        self.assertIsNone(br.select_update("hold", "1.0.0", releases))
        self.assertIsNone(br.select_update("stable", None, releases))
        self.assertIsNone(br.select_update("stable", "garbage", releases))
        self.assertIsNone(br.select_update("stable", "2.0.0", releases))  # already there
        self.assertIsNone(br.select_update("stable", "3.0.0", releases))  # never downgrades by itself

    def test_versions_compare_numerically_not_alphabetically(self):
        releases = [rel("1.2.0"), rel("1.10.0")]
        self.assertEqual(br.select_update("stable", "1.9.0", releases)[0]["version"], "1.10.0")

    def test_revoked_current_version_rolls_back_to_the_newest_older_stable(self):
        releases = [rel("1.0.0"), rel("1.1.0"), rel("1.2.0", "revoked")]
        chosen, rollback = br.select_update("stable", "1.2.0", releases)
        self.assertEqual((chosen["version"], rollback), ("1.1.0", True))
        self.assertIsNone(br.select_update("stable", "1.2.0", [rel("1.2.0", "revoked")]))  # nothing to go back to

    def test_a_revoked_release_is_never_offered(self):
        self.assertIsNone(br.select_update("canary", "1.0.0", [rel("1.0.0"), rel("1.1.0", "revoked")]))


class ManifestTests(unittest.TestCase):
    def test_manifest_is_bound_to_worker_and_signed_over_exact_bytes(self):
        import base64, os
        os.environ.setdefault("BUSINESS_SIGNING_KEY", base64.b64encode(os.urandom(32)).decode())
        from central_monitor import business_results as results
        from central_monitor.settings import get_settings
        get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
        worker = {"tenant_id": "11111111-1111-1111-1111-111111111111", "id": "22222222-2222-2222-2222-222222222222"}
        manifest = br.build_manifest(worker, rel("1.1.0"), False, datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
        self.assertEqual((manifest["tenant_id"], manifest["worker_id"], manifest["platform"], manifest["type"]),
                         (worker["tenant_id"], worker["id"], "linux-x64", "worker_update"))
        self.assertEqual(manifest["expires_at"], "2026-10-14T13:00:00+00:00")
        try:
            env = br.envelope(manifest)
        except RuntimeError:
            self.skipTest("signing key not configured in this environment")
        import json
        self.assertTrue(results.verify_manifest(json.loads(env["payload"]), env["signature"], results.public_key_b64()))
        tampered = json.loads(env["payload"]); tampered["sha256"] = "b" * 64
        self.assertFalse(results.verify_manifest(tampered, env["signature"], results.public_key_b64()))


if __name__ == "__main__":
    unittest.main()
