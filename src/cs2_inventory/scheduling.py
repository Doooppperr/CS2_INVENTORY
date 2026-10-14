from __future__ import annotations

from datetime import datetime, timezone

from .models import BEIJING_TIMEZONE, utcnow

# 保留旧 R2/R5 槽键，使发布当天和 timer 补触发都不会重复建立同一轮。
SCHEDULED_ROUNDS = ((8, "R2", "daily"), (20, "R5", "daily_light"))


def scheduled_slot_key(now: datetime | None = None) -> str | None:
    """每天北京时间 08/20 点两轮；08 点前不跨日补扫。"""
    value = now or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(BEIJING_TIMEZONE)
    for hour, suffix, _ in reversed(SCHEDULED_ROUNDS):
        if local.hour >= hour:
            return f"{local.date().isoformat()}-{suffix}"
    return None
