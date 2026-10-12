import re
import unittest
from pathlib import Path


class RetentionPolicyTests(unittest.TestCase):
    def test_historical_and_referenced_objects_are_not_physically_deleted(self) -> None:
        package = Path(__file__).resolve().parents[1] / "central_monitor"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
        protected = (
            "cm_tenants",
            "cm_assets",
            "cm_monitors",
            "cm_alerts",
            "cm_incidents",
            "cm_suppliers",
            "cm_holidays",
            "cm_sla_policies",
            "cm_teams",
            "cm_notification_rules",
            "cm_maintenance_windows",
            "cm_agents",
            "cm_action_grants",
        )
        violations = [table for table in protected if re.search(rf"DELETE\s+FROM\s+{table}\b", source, re.IGNORECASE)]
        self.assertEqual(violations, [], f"Physical deletion bypasses retention for: {violations}")

    def test_retention_migration_adds_alert_and_holiday_inactivation_state(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "central_monitor"
            / "migrations"
            / "033_retention_inactivation.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("cm_alerts ADD COLUMN IF NOT EXISTS inactive_at", migration)
        self.assertIn("cm_holidays ADD COLUMN IF NOT EXISTS active", migration)


if __name__ == "__main__":
    unittest.main()
