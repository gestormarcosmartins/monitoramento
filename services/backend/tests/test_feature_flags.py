import unittest

from central_monitor.feature_flags import feature_mode_allows, require_feature


class _Connection:
    def __init__(self, mode):
        self.mode = mode

    def execute(self, query, params):
        assert "cm_feature_flags" in query
        assert params[1] == "view:billing"
        return self

    def fetchone(self):
        return None if self.mode is None else {"mode": self.mode}


class FeatureFlagTests(unittest.TestCase):
    def test_absent_flag_preserves_existing_behavior(self):
        self.assertTrue(feature_mode_allows(None))
        self.assertEqual(require_feature(_Connection(None), "tenant", "view:billing"), "enabled")

    def test_only_pilot_and_enabled_modes_serve_user_traffic(self):
        self.assertFalse(feature_mode_allows("off"))
        self.assertFalse(feature_mode_allows("shadow"))
        self.assertTrue(feature_mode_allows("write_pilot"))
        self.assertTrue(feature_mode_allows("enabled"))

    def test_disabled_feature_fails_closed(self):
        with self.assertRaisesRegex(Exception, "not enabled"):
            require_feature(_Connection("off"), "tenant", "view:billing")


if __name__ == "__main__":
    unittest.main()
