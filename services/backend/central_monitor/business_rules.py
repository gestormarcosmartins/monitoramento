"""Pure business-rule functions; no database or HTTP side effects.

Input values are Decimals. None/stale data must never become healthy.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

Health = Literal['normal', 'atencao', 'critico', 'sem_dados', 'suspenso']


def validate_thresholds(direction: str, warning: Decimal | None, critical: Decimal | None) -> None:
    if direction not in ('above', 'below'):
        raise ValueError('direction must be above or below')
    if (warning is None) != (critical is None):
        raise ValueError('warning and critical must both be set or both omitted')
    if warning is not None:
        if direction == 'above' and warning >= critical:
            raise ValueError('critical must be greater than warning for above')
        if direction == 'below' and warning <= critical:
            raise ValueError('critical must be less than warning for below')


def evaluate(value: Decimal | None, collected_at: datetime | None, *, now: datetime,
             stale_after_seconds: int, direction: str, warning: Decimal | None,
             critical: Decimal | None, suspended: bool = False) -> Health:
    validate_thresholds(direction, warning, critical)
    if suspended:
        return 'suspenso'
    if value is None or collected_at is None:
        return 'sem_dados'
    if now.tzinfo is None or collected_at.tzinfo is None:
        raise ValueError('timestamps require timezone')
    if collected_at > now + timedelta(minutes=5):
        return 'sem_dados'
    if now - collected_at > timedelta(seconds=stale_after_seconds):
        return 'sem_dados'
    if warning is None:
        return 'normal'
    if direction == 'above':
        return 'critico' if value >= critical else 'atencao' if value >= warning else 'normal'
    return 'critico' if value <= critical else 'atencao' if value <= warning else 'normal'
