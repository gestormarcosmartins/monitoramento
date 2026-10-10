import unittest

from pydantic import ValidationError

from central_monitor.orchestrator import ActionRequest, ActionType


class ActionRequestTests(unittest.TestCase):
    def test_accepts_catalog_action(self) -> None:
        request = ActionRequest(
            tenant_id="tenant-1",
            asset_id="asset-1",
            action=ActionType.COLLECT_DIAGNOSTICS,
            idempotency_key="1234567890abcdef",
        )
        self.assertEqual(request.action, ActionType.COLLECT_DIAGNOSTICS)

    def test_rejects_arbitrary_command(self) -> None:
        with self.assertRaises(ValidationError):
            ActionRequest(
                tenant_id="tenant-1",
                asset_id="asset-1",
                action="powershell",
                idempotency_key="1234567890abcdef",
            )


if __name__ == "__main__":
    unittest.main()

