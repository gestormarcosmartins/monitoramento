import unittest

from central_monitor.reconciliation import reconciliation_findings


class ReconciliationTests(unittest.TestCase):
    def test_missing_local_child_is_imported_by_explicit_finding(self):
        findings = reconciliation_findings(
            [("task", "10")],
            [("task", "10"), ("followup", "20")],
        )
        self.assertEqual(findings, [{
            "child_kind": "followup",
            "external_id": "20",
            "finding_type": "import_required",
        }])

    def test_missing_remote_child_requires_review_not_blind_recreation(self):
        findings = reconciliation_findings(
            [("task", "10"), ("followup", "20")],
            [("task", "10")],
        )
        self.assertEqual(findings, [{
            "child_kind": "followup",
            "external_id": "20",
            "finding_type": "remote_missing_requires_review",
        }])

    def test_repeated_equivalent_snapshot_has_no_findings(self):
        local = [("task", "10"), ("followup", "20")]
        observed = [("followup", "20"), ("task", "10"), ("task", "10")]
        self.assertEqual(reconciliation_findings(local, observed), [])


if __name__ == "__main__":
    unittest.main()
