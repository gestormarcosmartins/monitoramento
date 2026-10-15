import unittest

from central_monitor.failure_grace import effective_grace_seconds, must_wait


class GraceRules(unittest.TestCase):
    def test_agent_monitors_use_the_push_default_and_others_are_immediate(self):
        self.assertEqual(effective_grace_seconds({"monitor_type": "push"}, 600), 600)
        self.assertEqual(effective_grace_seconds({"monitor_type": "http"}, 600), 0)
        self.assertEqual(effective_grace_seconds({"monitor_type": "tcp"}, 600), 0)

    def test_explicit_setting_wins_including_zero(self):
        self.assertEqual(effective_grace_seconds({"monitor_type": "push", "failure_grace_seconds": 0}, 600), 0)
        self.assertEqual(effective_grace_seconds({"monitor_type": "http", "failure_grace_seconds": 120}, 600), 120)

    def test_waits_until_the_failure_lasts_long_enough(self):
        self.assertTrue(must_wait(0, 600, False))
        self.assertTrue(must_wait(599.9, 600, False))
        self.assertFalse(must_wait(600, 600, False))
        self.assertFalse(must_wait(3600, 600, False))

    def test_zero_grace_never_waits_and_open_alert_never_returns_to_grace(self):
        self.assertFalse(must_wait(0, 0, False))
        self.assertFalse(must_wait(5, 600, True))

    def test_missing_timing_counts_as_just_started(self):
        self.assertTrue(must_wait(None, 600, False))


if __name__ == "__main__":
    unittest.main()
