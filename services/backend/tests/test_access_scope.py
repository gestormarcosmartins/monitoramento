import unittest

from central_monitor.access_scope import incident_scope


class IncidentAccessScopeTests(unittest.TestCase):
    def test_master_and_company_scope_can_read_company_rows(self) -> None:
        self.assertEqual(incident_scope({"role": "master", "id": "m"}), ("TRUE", []))
        self.assertEqual(incident_scope({"role": "viewer", "row_scope": "company", "id": "u"}), ("TRUE", []))

    def test_own_scope_matches_requester_or_assignee(self) -> None:
        clause, params = incident_scope({"id": "user-1", "role": "viewer", "row_scope": "own"}, "ticket")
        self.assertIn("ticket.requester_user_id=%s", clause)
        self.assertIn("ticket.assignee_user_id=%s", clause)
        self.assertEqual(params, ["user-1", "user-1"])

    def test_department_scope_requires_explicit_target(self) -> None:
        self.assertEqual(incident_scope({"id": "u", "row_scope": "department"}), ("FALSE", []))
        clause, params = incident_scope({"id": "u", "row_scope": "department", "department_id": "dep-1"})
        self.assertIn("cm_user_departments", clause)
        self.assertEqual(params, ["dep-1"])

    def test_team_service_scope_combines_only_explicit_targets(self) -> None:
        clause, params = incident_scope({"id": "u", "row_scope": "team_service", "team_id": "team-1", "engagement_id": "eng-1"})
        self.assertIn("assignee_team_id=%s", clause)
        self.assertIn("engagement_id=%s", clause)
        self.assertEqual(params, ["team-1", "eng-1"])
        self.assertEqual(incident_scope({"id": "u", "row_scope": "team_service"}), ("FALSE", []))

    def test_unknown_scope_fails_closed(self) -> None:
        self.assertEqual(incident_scope({"id": "u", "row_scope": "unexpected"}), ("FALSE", []))


if __name__ == "__main__":
    unittest.main()
