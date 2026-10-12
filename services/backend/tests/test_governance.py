import unittest

from central_monitor.governance import require_transition, validate_parameters, validate_runbook_steps
from central_monitor.worker import runbook_authorization_failure


class GovernanceTests(unittest.TestCase):
    def test_closed_catalog_accepts_typed_step(self):
        validate_runbook_steps([{"type": "collect_diagnostics", "timeout_seconds": 30}])

    def test_arbitrary_shell_is_forbidden(self):
        with self.assertRaises(ValueError):
            validate_runbook_steps([{"type": "shell", "command": "whoami"}])

    def test_shell_payload_is_forbidden_even_in_allowed_step(self):
        with self.assertRaises(ValueError):
            validate_runbook_steps([{"type": "http_request", "note": "run powershell"}])

    def test_change_cannot_skip_review_and_approval(self):
        with self.assertRaises(ValueError):
            require_transition("change", "draft", "running")
        require_transition("change", "draft", "review")

    def test_published_knowledge_can_only_retire(self):
        require_transition("knowledge", "published", "retired")
        with self.assertRaises(ValueError):
            require_transition("knowledge", "published", "draft")

    def test_runbook_parameters_are_typed_and_closed(self):
        schema={"type":"object","required":["minutes"],"properties":{"minutes":{"type":"integer"}}}
        validate_parameters(schema,{"minutes":5})
        with self.assertRaises(ValueError):validate_parameters(schema,{"minutes":"5"})
        with self.assertRaises(ValueError):validate_parameters(schema,{"minutes":5,"command":"whoami"})

    def test_runbook_revalidates_requester_and_approver_before_execution(self):
        self.assertEqual(runbook_authorization_failure(False,True,True),"requester_access_revoked")
        self.assertEqual(runbook_authorization_failure(True,True,False),"approver_access_revoked")
        self.assertIsNone(runbook_authorization_failure(True,True,True))


if __name__ == "__main__":
    unittest.main()
