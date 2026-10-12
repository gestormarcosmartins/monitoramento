import unittest

from fastapi import HTTPException

from central_monitor.ticketing import TRANSITIONS, _version_from_if_match


class TicketingRulesTests(unittest.TestCase):
    def test_if_match_requires_and_parses_version(self) -> None:
        self.assertEqual(_version_from_if_match('W/"7"'), 7)
        with self.assertRaises(HTTPException) as context:
            _version_from_if_match(None)
        self.assertEqual(context.exception.status_code, 428)

    def test_closed_ticket_can_reopen_but_cancelled_ticket_is_terminal(self) -> None:
        self.assertIn("in_progress", TRANSITIONS["closed"])
        self.assertEqual(TRANSITIONS["cancelled"], set())

    def test_ticket_cannot_skip_from_new_to_resolved(self) -> None:
        self.assertNotIn("resolved", TRANSITIONS["new"])


if __name__ == "__main__":
    unittest.main()
