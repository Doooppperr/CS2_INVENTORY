from __future__ import annotations

from datetime import datetime, timezone

from .models import BEIJING_TIMEZONE, utcnow

# 早晚沿用旧槽键：已有批次不重复，早间 R2 同时提供日报基线。
SCHEDULED_ROUNDS = ((7, "R2", "daily"), (14, "R3", "daily_light"), (21, "R5", "daily_light"))


def scheduled_slot_key(now: datetime | None = None) -> str | None:
    """返回北京时间当日最近已到的槽位；首轮前不跨日补扫。"""
    value = now or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(BEIJING_TIMEZONE)
    for hour, suffix, _ in reversed(SCHEDULED_ROUNDS):
        if local.hour >= hour:
            return f"{local.date().isoformat()}-{suffix}"
    return None
