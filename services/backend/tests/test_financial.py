import unittest
from datetime import date

from central_monitor.financial import billing_external_key, commercial_allocation_delta, commercial_delta, content_hash, referenced_source_key, rounded_minutes


class FinancialRulesTests(unittest.TestCase):
    def test_rounding_is_deterministic_and_preserves_sign(self) -> None:
        self.assertEqual(rounded_minutes(1,15),15)
        self.assertEqual(rounded_minutes(16,15),30)
        self.assertEqual(rounded_minutes(-16,15),-30)
        self.assertEqual(rounded_minutes(0,15),0)

    def test_approval_snapshot_hash_is_stable(self) -> None:
        self.assertEqual(content_hash({"lines":[2,1],"period":"2026-10"}),content_hash({"period":"2026-10","lines":[2,1]}))
        self.assertNotEqual(content_hash({"lines":[2,1]}),content_hash({"lines":[1,2]}))

    def test_commercial_review_preserves_technical_effort_and_returns_only_delta(self) -> None:
        self.assertEqual(commercial_delta(120,90),-30)
        self.assertEqual(commercial_delta(120,150),30)
        self.assertEqual(commercial_delta(120,120),0)

    def test_commercial_adjustment_idempotency_key_is_stable(self) -> None:
        self.assertEqual(referenced_source_key("review-01"),"commercial-review:review-01")

    def test_commercial_adjustment_respects_franchise_and_reduces_excess_first(self) -> None:
        self.assertEqual(commercial_allocation_delta(60,30,-45,0),(-15,-30))
        self.assertEqual(commercial_allocation_delta(60,0,45,15),(15,30))

    def test_billing_revision_keys_are_stable_and_distinct(self) -> None:
        competence=date(2026,10,1)
        self.assertEqual(billing_external_key("contract-1",competence,1),"NEXORA-contract-1-202610")
        self.assertEqual(billing_external_key("contract-1",competence,2),"NEXORA-contract-1-202610-R2")


if __name__=="__main__":unittest.main()
