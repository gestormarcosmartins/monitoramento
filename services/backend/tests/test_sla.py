import unittest
from datetime import UTC, date, datetime

from central_monitor.sla import add_sla_minutes


class SlaCalendarTests(unittest.TestCase):
    def test_24x7_adds_elapsed_minutes(self) -> None:
        start=datetime(2026,10,10,12,0,tzinfo=UTC)
        self.assertEqual(add_sla_minutes(start,60,{"coverage":"24x7"},"America/Sao_Paulo",set()),datetime(2026,10,10,13,0,tzinfo=UTC))

    def test_business_calendar_skips_weekend_and_holiday(self) -> None:
        start=datetime(2026,10,9,20,50,tzinfo=UTC)
        policy={"coverage":"business_hours","business_start":datetime.strptime("08:00","%H:%M").time(),"business_end":datetime.strptime("18:00","%H:%M").time(),"business_days":[1,2,3,4,5]}
        result=add_sla_minutes(start,20,policy,"America/Sao_Paulo",{date(2026,10,12)})
        self.assertEqual(result,datetime(2026,10,13,11,10,tzinfo=UTC))


if __name__ == "__main__": unittest.main()
