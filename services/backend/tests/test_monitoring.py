import unittest

from datetime import UTC, datetime, timedelta

from central_monitor.command_bus import canonical_payload_hash
from central_monitor.monitoring import channel_ready, episode_fingerprint, episode_transition


class MonitoringCorrelationTests(unittest.TestCase):
    def test_canonical_event_hash_deduplicates_equivalent_payloads(self):
        first={"eventId":"evt-1","attributes":{"b":2,"a":1}}
        second={"attributes":{"a":1,"b":2},"eventId":"evt-1"}
        self.assertEqual(canonical_payload_hash(first),canonical_payload_hash(second))

    def test_older_event_cannot_regress_episode_state(self):
        latest=datetime(2026,10,11,12,0,tzinfo=UTC)
        advances,status=episode_transition("recovering",latest,latest-timedelta(minutes=5),False)
        self.assertFalse(advances)
        self.assertEqual(status,"recovering")

    def test_newer_event_can_reopen_episode(self):
        latest=datetime(2026,10,11,12,0,tzinfo=UTC)
        advances,status=episode_transition("recovering",latest,latest+timedelta(minutes=1),False)
        self.assertTrue(advances)
        self.assertEqual(status,"open")

    def test_episode_fingerprint_is_stable_and_connection_scoped(self):
        first = episode_fingerprint("zabbix", "primary", "asset-1", "sql", "cpu", "seed")
        self.assertEqual(first, episode_fingerprint("zabbix", "primary", "asset-1", "sql", "cpu", "seed"))
        self.assertNotEqual(first, episode_fingerprint("zabbix", "secondary", "asset-1", "sql", "cpu", "seed"))

    def test_whatsapp_requires_full_homologation(self):
        capability = {"provider": "evolution", "identity_verified": True, "capability_verified": True,
                      "consent_verified": True, "template_approved": False}
        self.assertFalse(channel_ready(capability, "whatsapp"))
        capability["template_approved"] = True
        self.assertTrue(channel_ready(capability, "whatsapp"))

    def test_erp_requires_provider_identity_and_reliable_capability(self):
        self.assertTrue(channel_ready({"provider": "erp-x", "identity_verified": True,
                                       "capability_verified": True}, "erp"))
        self.assertFalse(channel_ready({"provider": "erp-x", "identity_verified": True,
                                        "capability_verified": False}, "erp"))


if __name__ == "__main__":
    unittest.main()
