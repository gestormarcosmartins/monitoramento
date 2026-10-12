import unittest
from datetime import UTC, date, datetime

from central_monitor.sla import add_sla_minutes


class SlaCalendarTests(unittest.TestCase):
    @staticmethod
    def business_policy(start: str = "08:00", end: str = "18:00", days: list[int] | None = None) -> dict:
        return {
            "coverage": "business_hours",
            "business_start": datetime.strptime(start, "%H:%M").time(),
            "business_end": datetime.strptime(end, "%H:%M").time(),
            "business_days": days if days is not None else [1, 2, 3, 4, 5],
        }

    def test_24x7_adds_elapsed_minutes(self) -> None:
        start=datetime(2026,10,10,12,0,tzinfo=UTC)
        self.assertEqual(add_sla_minutes(start,60,{"coverage":"24x7"},"America/Sao_Paulo",set()),datetime(2026,10,10,13,0,tzinfo=UTC))

    def test_business_calendar_skips_weekend_and_holiday(self) -> None:
        start=datetime(2026,10,9,20,50,tzinfo=UTC)
        result=add_sla_minutes(start,20,self.business_policy(),"America/Sao_Paulo",{date(2026,10,12)})
        self.assertEqual(result,datetime(2026,10,13,11,10,tzinfo=UTC))

    def test_business_calendar_preserves_local_hours_across_dst_change(self) -> None:
        # Europe/Berlin changes from UTC+1 to UTC+2 during this weekend.
        start = datetime(2026, 3, 27, 15, 50, tzinfo=UTC)
        result = add_sla_minutes(start, 20, self.business_policy("08:00", "17:00"), "Europe/Berlin", set())
        self.assertEqual(result, datetime(2026, 3, 30, 6, 10, tzinfo=UTC))

    def test_business_calendar_uses_tenant_timezone_not_server_timezone(self) -> None:
        start = datetime(2026, 7, 6, 20, 55, tzinfo=UTC)  # 16:55 in New York.
        result = add_sla_minutes(start, 10, self.business_policy("09:00", "17:00"), "America/New_York", set())
        self.assertEqual(result, datetime(2026, 7, 7, 13, 5, tzinfo=UTC))

    def test_exact_close_rolls_remaining_minutes_to_next_business_day(self) -> None:
        start = datetime(2026, 10, 9, 20, 59, tzinfo=UTC)  # Friday 17:59 in São Paulo.
        result = add_sla_minutes(start, 2, self.business_policy(), "America/Sao_Paulo", set())
        self.assertEqual(result, datetime(2026, 10, 12, 11, 1, tzinfo=UTC))

    def test_zero_minutes_preserves_exact_instant(self) -> None:
        start = datetime(2026, 10, 9, 20, 59, 37, 123456, tzinfo=UTC)
        self.assertIs(add_sla_minutes(start, 0, self.business_policy(), "America/Sao_Paulo", set()), start)

    def test_invalid_calendar_fails_fast(self) -> None:
        start = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
        invalid_policies = (
            self.business_policy(days=[]),
            self.business_policy(days=[0, 1]),
            self.business_policy("18:00", "08:00"),
        )
        for policy in invalid_policies:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                add_sla_minutes(start, 10, policy, "America/Sao_Paulo", set())

    def test_rejects_naive_start_and_negative_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            add_sla_minutes(datetime(2026, 10, 9, 12, 0), 10, self.business_policy(), "UTC", set())
        with self.assertRaisesRegex(ValueError, "negative"):
            add_sla_minutes(datetime(2026, 10, 9, 12, 0, tzinfo=UTC), -1, self.business_policy(), "UTC", set())


if __name__ == "__main__": unittest.main()
