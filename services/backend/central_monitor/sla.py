from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def add_sla_minutes(
    start: datetime,
    minutes: int,
    policy: dict[str, Any],
    timezone: str,
    holidays: set[date],
) -> datetime:
    if start.tzinfo is None:
        raise ValueError("SLA start must be timezone-aware")
    if minutes < 0:
        raise ValueError("SLA minutes must not be negative")
    if minutes == 0:
        return start
    if policy.get("coverage") != "business_hours":
        return start + timedelta(minutes=minutes)
    zone=ZoneInfo(timezone)
    current=start.astimezone(zone).replace(second=0,microsecond=0)
    remaining=minutes
    business_days=set(policy.get("business_days", [1,2,3,4,5]))
    opens=policy["business_start"]
    closes=policy["business_end"]
    if not business_days or any(day < 1 or day > 7 for day in business_days):
        raise ValueError("SLA business_days must contain ISO weekdays from 1 to 7")
    if opens >= closes:
        raise ValueError("SLA business_start must be before business_end")
    while remaining>0:
        if current.isoweekday() in business_days and current.date() not in holidays and opens <= current.time() < closes:
            remaining-=1
        current+=timedelta(minutes=1)
    return current.astimezone(start.tzinfo)
