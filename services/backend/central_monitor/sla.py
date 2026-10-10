from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def add_sla_minutes(start: datetime, minutes: int, policy: dict, timezone: str, holidays: set) -> datetime:
    if policy.get("coverage") != "business_hours":
        return start + timedelta(minutes=minutes)
    zone=ZoneInfo(timezone)
    current=start.astimezone(zone).replace(second=0,microsecond=0)
    remaining=minutes
    business_days=set(policy.get("business_days") or [1,2,3,4,5])
    opens=policy["business_start"]
    closes=policy["business_end"]
    while remaining>0:
        if current.isoweekday() in business_days and current.date() not in holidays and opens <= current.time() < closes:
            remaining-=1
        current+=timedelta(minutes=1)
    return current.astimezone(start.tzinfo)
