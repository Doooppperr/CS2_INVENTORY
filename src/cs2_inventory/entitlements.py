from __future__ import annotations

import hashlib
import secrets
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update

from .database import db
from .models import (
    ActivationCode,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    User,
    beijing_iso,
    utcnow,
)

BEIJING = ZoneInfo("Asia/Shanghai")
GRACE_LIFETIME = timedelta(days=7)
VALID_PLANS = {"monthly", "annual", "permanent"}


def aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_natural_period(value: datetime, plan: str) -> datetime:
    local = aware_utc(value).astimezone(BEIJING)
    if plan == "monthly":
        year = local.year + (1 if local.month == 12 else 0)
        month = 1 if local.month == 12 else local.month + 1
    elif plan == "annual":
        year, month = local.year + 1, local.month
    else:
        raise ValueError("套餐期限无效")
    day = min(local.day, monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day).astimezone(timezone.utc)


def lock_user(user_id: int) -> None:
    # SQLite serializes writers. This no-op UPDATE closes the COUNT/INSERT and
    # invite redemption races without exposing BEGIN IMMEDIATE to callers that
    # may already have an ORM transaction.
    db.session.execute(
        update(User).where(User.id == user_id).values(last_login_at=User.last_login_at)
    )


def entitlement_state(user: User, *, now: datetime | None = None) -> str:
    now = aware_utc(now or utcnow())
    if user.role == "admin":
        return "admin"
    if user.account_kind == "internal":
        return "internal"
    if user.plan == "permanent":
        return "active_permanent"
    expires = aware_utc(user.activation_expires_at)
    if expires and now < expires:
        return "active"
    if expires and now < expires + GRACE_LIFETIME:
        return "grace"
    return "expired"


def grace_ends_at(user: User) -> datetime | None:
    expires = aware_utc(user.activation_expires_at)
    return expires + GRACE_LIFETIME if expires else None


def can_add_monitor(user: User, *, now: datetime | None = None) -> tuple[bool, str | None]:
    state = entitlement_state(user, now=now)
    if state in {"admin", "internal"}:
        return True, None
    if state in {"active", "active_permanent"}:
        count = Subscription.query.filter_by(user_id=user.id).count()
        limit = user.monitor_limit
        if limit is not None and count >= limit:
            return False, f"已达到监控上限 {count}/{limit}"
        return True, None
    if state == "grace":
        return False, "套餐已到期，当前处于只读宽限期，请使用邀请码重新激活"
    if state == "expired":
        return False, "套餐已过期，请使用邀请码重新激活"
    return False, "当前账号不能添加监控"


def entitlement_public(user: User, *, now: datetime | None = None) -> dict:
    now = aware_utc(now or utcnow())
    state = entitlement_state(user, now=now)
    actual_count = Subscription.query.filter_by(user_id=user.id).count()
    visible_count = 0 if state == "expired" else actual_count
    can_add, reason = can_add_monitor(user, now=now)
    data = {
        "kind": user.account_kind,
        "plan": user.plan,
        "status": state,
        "activated_at": beijing_iso(user.activated_at),
        "expires_at": beijing_iso(user.activation_expires_at),
        "grace_ends_at": beijing_iso(grace_ends_at(user)),
        "monitor_limit": user.monitor_limit,
        "monitor_count": visible_count,
        "can_add_monitor": can_add,
        "add_block_reason": reason,
    }
    return data


def state_allows_monitor_write(user: User, *, now: datetime | None = None) -> bool:
    return entitlement_state(user, now=now) in {
        "admin",
        "internal",
        "active",
        "active_permanent",
    }


def snapshot_query_for_user(user: User, target_id: int):
    query = Snapshot.query.filter_by(target_id=target_id)
    state = entitlement_state(user)
    if state in {"admin", "internal", "active", "active_permanent"}:
        return query
    if state == "grace":
        return query.filter(Snapshot.scanned_at <= user.activation_expires_at)
    return query.filter(Snapshot.id == -1)


def latest_accessible_snapshot(user: User, target_id: int) -> Snapshot | None:
    return snapshot_query_for_user(user, target_id).order_by(
        Snapshot.scanned_at.desc(), Snapshot.id.desc()
    ).first()


def user_can_access_monitors(user: User) -> bool:
    return entitlement_state(user) != "expired"


def daily_eligible_user(user: User, *, now: datetime | None = None) -> bool:
    return entitlement_state(user, now=now) in {
        "admin",
        "internal",
        "active",
        "active_permanent",
    }


def target_daily_eligible(target: SteamTarget, *, now: datetime | None = None) -> bool:
    return any(daily_eligible_user(sub.user, now=now) for sub in target.subscriptions)


def scan_job_eligible(job: ScanJob, *, now: datetime | None = None) -> bool:
    if job.kind in {"manual", "instant"}:
        return True
    target = db.session.get(SteamTarget, job.target_id) if job.target_id else None
    if not target:
        return False
    return target_daily_eligible(target, now=now)


def code_digest(raw_code: str) -> str:
    return hashlib.sha256(raw_code.strip().upper().encode("utf-8")).hexdigest()


def activation_code_public(row: ActivationCode) -> dict:
    status = "revoked" if row.revoked_at else "redeemed" if row.redeemed_at else "unused"
    return {
        "id": row.id,
        "prefix": row.code_prefix,
        "plan": row.plan,
        "monitor_limit": row.monitor_limit,
        "status": status,
        "created_at": beijing_iso(row.created_at),
        "redeemed_at": beijing_iso(row.redeemed_at),
        "redeemed_by_id": row.redeemed_by_id,
    }


def create_activation_code(admin: User, plan: str, monitor_limit: int) -> tuple[ActivationCode, str]:
    if plan not in VALID_PLANS:
        raise ValueError("套餐必须为月度、年度或永久")
    if monitor_limit < 1 or monitor_limit > 10000:
        raise ValueError("邀请码监控限额必须为 1-10000")
    token = secrets.token_hex(16).upper()
    raw = "CS2-" + "-".join(token[index:index + 4] for index in range(0, len(token), 4))
    row = ActivationCode(
        code_digest=code_digest(raw),
        code_prefix=raw[:13],
        plan=plan,
        monitor_limit=monitor_limit,
        created_by_id=admin.id,
    )
    db.session.add(row)
    db.session.commit()
    return row, raw


def _purge_user_monitor_data(user: User) -> int:
    targets = list({sub.target for sub in user.subscriptions})
    removed = len(user.subscriptions)
    for sub in list(user.subscriptions):
        db.session.delete(sub)
    db.session.flush()
    for target in targets:
        if Subscription.query.filter_by(target_id=target.id).count() == 0:
            db.session.delete(target)
    return removed


def redeem_activation_code(user: User, raw_code: str) -> ActivationCode:
    raw_code = str(raw_code or "").strip()
    if not raw_code:
        raise ValueError("请输入邀请码")
    lock_user(user.id)
    db.session.refresh(user)
    state = entitlement_state(user)
    if state in {"admin", "internal"}:
        raise ValueError("内部账号无需使用邀请码")
    if user.account_kind == "customer" and user.plan == "permanent":
        raise ValueError("当前账号已经永久激活")
    row = ActivationCode.query.filter_by(code_digest=code_digest(raw_code)).first()
    if not row:
        raise LookupError("邀请码无效")
    if row.revoked_at:
        raise LookupError("邀请码已撤销")
    if row.redeemed_at:
        raise LookupError("邀请码已使用")
    now = aware_utc(utcnow())
    if user.account_kind == "customer" and state == "expired":
        _purge_user_monitor_data(user)
    old_expiry = aware_utc(user.activation_expires_at)
    base = old_expiry if old_expiry and old_expiry > now else now
    user.account_kind = "customer"
    user.plan = row.plan
    user.activated_at = now
    user.monitor_limit = row.monitor_limit
    user.activation_expires_at = None if row.plan == "permanent" else add_natural_period(base, row.plan)
    row.redeemed_by_id = user.id
    row.redeemed_at = now
    db.session.commit()
    return row


def revoke_activation_code(row: ActivationCode) -> None:
    if row.redeemed_at:
        raise ValueError("已兑换的邀请码不能撤销")
    if not row.revoked_at:
        row.revoked_at = utcnow()
        db.session.commit()


def protected_snapshot_ids(*, now: datetime | None = None) -> set[int]:
    now = aware_utc(now or utcnow())
    result: set[int] = set()
    for user in User.query.filter_by(account_kind="customer").all():
        if entitlement_state(user, now=now) != "grace":
            continue
        for sub in user.subscriptions:
            row = Snapshot.query.filter(
                Snapshot.target_id == sub.target_id,
                Snapshot.scanned_at <= user.activation_expires_at,
            ).order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc()).first()
            if row:
                result.add(row.id)
    return {value for value in result if value is not None}


def cleanup_lifecycle(*, now: datetime | None = None) -> dict:
    now = aware_utc(now or utcnow())
    purged_customers = 0
    removed_subscriptions = 0
    for user in User.query.filter_by(account_kind="customer").all():
        if entitlement_state(user, now=now) == "expired" and user.subscriptions:
            removed_subscriptions += _purge_user_monitor_data(user)
            purged_customers += 1
    db.session.commit()
    return {
        "purged_customers": purged_customers,
        "removed_subscriptions": removed_subscriptions,
    }
