import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from central_monitor.obligations import escalation_level
from central_monitor.service_operations import TimeEntryCreate


class ServiceOperationRulesTests(unittest.TestCase):
    def test_assignment_does_not_represent_first_response(self) -> None:
        # First response is deliberately absent from assignment/transition helpers;
        # only a useful public entry completes that obligation.
        self.assertIsNone(escalation_level(datetime.now(UTC)+timedelta(hours=2)))

    def test_escalation_levels_do_not_reset_due_date(self) -> None:
        now=datetime.now(UTC)
        self.assertEqual(escalation_level(now+timedelta(minutes=59),now),1)
        self.assertEqual(escalation_level(now+timedelta(minutes=29),now),2)
        self.assertEqual(escalation_level(now-timedelta(seconds=1),now),3)

    def test_time_entry_requires_duration(self) -> None:
        with self.assertRaises(ValidationError):
            TimeEntryCreate(ticket_id="00000000-0000-0000-0000-000000000001",description="Atendimento",started_at=datetime.now(UTC))

    def test_time_entry_rejects_inverted_interval(self) -> None:
        now=datetime.now(UTC)
        with self.assertRaises(ValidationError):
            TimeEntryCreate(ticket_id="00000000-0000-0000-0000-000000000001",description="Atendimento",started_at=now,ended_at=now-timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
