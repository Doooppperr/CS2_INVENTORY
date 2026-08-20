from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from .database import db
from .localization import (
    canonicalize_unified,
    has_han,
    localization_map,
    queue_localization_job,
)
from .models import (
    QuotaUsage,
    ScanJob,
    Snapshot,
    SnapshotItem,
    SteamTarget,
    Subscription,
    SystemState,
    User,
    beijing_iso,
    utcnow,
)
from .unified import evidence_json, public_payload

STEAMID_RE = re.compile(r"^7656119\d{10}$")


def state_get(key: str, default: str = "") -> str:
    row = db.session.get(SystemState, key)
    return row.value if row else default


def state_set(key: str, value: str) -> None:
    row = db.session.get(SystemState, key)
    if row:
        row.value = value
    else:
        db.session.add(SystemState(key=key, value=value))


def maintenance_active() -> bool:
    return state_get("maintenance", "0") == "1"


def ensure_mutation_allowed(user: User | None) -> tuple[dict, int] | None:
    if maintenance_active() and (not user or user.role != "admin"):
        return {"error": "每日库存维护进行中，当前为只读模式", "maintenance": True}, 423
    return None


def target_label(target: SteamTarget) -> str:
    return f"{target.persona_name} ({target.steamid})" if target.persona_name else target.steamid


def latest_snapshot(target_id: int) -> Snapshot | None:
    return (
        Snapshot.query.filter_by(target_id=target_id)
        .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
        .first()
    )


def snapshot_public(snapshot: Snapshot, *, include_items: bool = True) -> dict:
    data = {
        "id": snapshot.id,
        "total_items": snapshot.total_items,
        "item_types": snapshot.item_types,
        "coverage": snapshot.coverage,
        "scanned_at": beijing_iso(snapshot.scanned_at),
        "elapsed_ms": snapshot.elapsed_ms,
        "errors": json.loads(snapshot.errors_json or "[]"),
    }
    if include_items:
        rows = Counter()
        newest_discovery: dict[tuple[str, bool], float] = {}
        for item in snapshot.items:
            key = (item.name, bool(item.is_trade_protected))
            rows[key] += item.amount
            seen = item.first_seen_at or snapshot.scanned_at
            seen_key = _datetime_sort_key(seen)
            current = newest_discovery.get(key)
            if current is None or seen_key > current:
                newest_discovery[key] = seen_key
        ordered_groups = sorted(
            rows,
            key=lambda key: (
                -int(key[1]),
                -newest_discovery[key],
                key[0].casefold(),
            ),
        )
        data["items"] = [
            {
                "name": name,
                "count": rows[(name, is_trade_protected)],
                "is_trade_protected": is_trade_protected,
            }
            for name, is_trade_protected in ordered_groups
        ]
    return data


def _datetime_sort_key(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def target_public(target: SteamTarget, *, include_latest: bool = True) -> dict:
    snapshot = latest_snapshot(target.id) if include_latest else None
    return {
        "id": target.id,
        "steamid": target.steamid,
        "persona_name": target.persona_name,
        "label": target_label(target),
        "scan_status": target.scan_status,
        "last_scan_at": beijing_iso(target.last_scan_at),
        "last_success_at": beijing_iso(target.last_success_at),
        "last_error": target.last_error,
        "latest": snapshot_public(snapshot, include_items=False) if snapshot else None,
        "subscriber_count": len(target.subscriptions),
    }


def queue_job(target: SteamTarget, *, kind: str, requested_by: int | None = None, batch_id: int | None = None) -> ScanJob:
    existing = ScanJob.query.filter(
        ScanJob.target_id == target.id,
        ScanJob.status.in_(["queued", "running"]),
        ScanJob.kind != "instant",
    ).first()
    if existing:
        return existing
    job = ScanJob(
        target_id=target.id,
        steamid=target.steamid,
        requested_by=requested_by,
        batch_id=batch_id,
        kind=kind,
    )
    db.session.add(job)
    target.scan_status = "queued"
    return job


def add_monitor(user: User, steamid: str) -> tuple[SteamTarget, ScanJob | None, bool]:
    steamid = steamid.strip()
    if not STEAMID_RE.fullmatch(steamid):
        raise ValueError("请输入有效的 SteamID64")
    user_id = user.id
    target = SteamTarget.query.filter_by(steamid=steamid).first()
    created = False
    if target is None:
        target = SteamTarget(steamid=steamid)
        db.session.add(target)
        try:
            db.session.flush()
            created = True
        except IntegrityError:
            db.session.rollback()
            target = SteamTarget.query.filter_by(steamid=steamid).first()
            if target is None:
                raise
    if Subscription.query.filter_by(user_id=user_id, target_id=target.id).first():
        return target, None, False
    db.session.add(Subscription(user_id=user_id, target_id=target.id))
    job = queue_job(target, kind="initial", requested_by=user_id) if created or not latest_snapshot(target.id) else None
    db.session.commit()
    return target, job, created


def delete_monitor(user: User, target: SteamTarget) -> bool:
    subscription = Subscription.query.filter_by(user_id=user.id, target_id=target.id).first()
    if not subscription and user.role != "admin":
        raise LookupError("监控不存在")
    if subscription:
        db.session.delete(subscription)
        db.session.flush()
    remaining = Subscription.query.filter_by(target_id=target.id).count()
    deleted_target = remaining == 0
    if deleted_target:
        db.session.delete(target)
    db.session.commit()
    return deleted_target


def delete_user_account(user: User) -> int:
    """Permanently delete a user and targets that only they subscribed to."""
    targets = list({subscription.target for subscription in user.subscriptions})
    db.session.delete(user)
    db.session.flush()
    deleted_targets = 0
    for target in targets:
        if Subscription.query.filter_by(target_id=target.id).count() == 0:
            db.session.delete(target)
            deleted_targets += 1
    db.session.commit()
    return deleted_targets


def store_snapshot(target: SteamTarget, unified: dict) -> Snapshot:
    scanned_at = utcnow()
    previous = latest_snapshot(target.id)
    previous_seen = {
        item.asset_key: (item.first_seen_at or previous.scanned_at)
        for item in previous.items
    } if previous else {}
    language = current_app.config.get("ITEM_LANGUAGE", "schinese")
    unresolved_count = canonicalize_unified(target, unified, language=language)
    public = public_payload(unified, scanned_at=beijing_iso(scanned_at))
    blob = gzip.compress(json.dumps(public, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    snapshot = Snapshot(
        target_id=target.id,
        total_items=unified["total_items"],
        item_types=unified["item_types"],
        coverage=unified["coverage"],
        elapsed_ms=unified["elapsed_ms"],
        errors_json=json.dumps(unified.get("errors") or [], ensure_ascii=False),
        payload_gzip=blob,
        scanned_at=scanned_at,
    )
    db.session.add(snapshot)
    db.session.flush()
    for asset in unified.get("_assets") or []:
        db.session.add(SnapshotItem(
            snapshot_id=snapshot.id,
            asset_key=asset["asset_key"],
            name=asset["name"],
            raw_name=asset.get("raw_name") or asset["name"],
            classid=str(asset.get("classid") or ""),
            instanceid=str(asset.get("instanceid") or "0"),
            name_localized=bool(asset.get("name_localized", False)),
            amount=int(asset.get("amount", 1)),
            evidence_json=evidence_json(asset),
            first_seen_at=previous_seen.get(asset["asset_key"], scanned_at),
            is_trade_protected=bool(asset.get("is_trade_protected", False)),
        ))
    queue_localization_job(snapshot, target, unresolved_count, language=language)
    target.last_success_at = snapshot.scanned_at
    target.last_scan_at = snapshot.scanned_at
    target.scan_status = "ready"
    target.last_error = None
    return snapshot


def snapshot_diff(current: Snapshot, previous: Snapshot | None) -> dict:
    current_rows = {row.asset_key: row for row in current.items}
    previous_rows = {row.asset_key: row for row in previous.items} if previous else {}
    aliases = localization_map(current_app.config.get("ITEM_LANGUAGE", "schinese"))
    for asset_key in set(current_rows) & set(previous_rows):
        current_row = current_rows[asset_key]
        previous_row = previous_rows[asset_key]
        if current_row.name == previous_row.name:
            continue
        if current_row.name_localized or has_han(current_row.name):
            aliases[previous_row.raw_name or previous_row.name] = current_row.name
            aliases[previous_row.name] = current_row.name
        elif previous_row.name_localized or has_han(previous_row.name):
            aliases[current_row.raw_name or current_row.name] = previous_row.name
            aliases[current_row.name] = previous_row.name

    def stable_name(row: SnapshotItem) -> str:
        return aliases.get(row.raw_name or row.name) or aliases.get(row.name) or row.name

    current_counts = Counter()
    previous_counts = Counter()
    for row in current_rows.values():
        current_counts[stable_name(row)] += row.amount
    for row in previous_rows.values():
        previous_counts[stable_name(row)] += row.amount
    names = sorted(set(current_counts) | set(previous_counts))
    added, removed, changed = [], [], []
    for name in names:
        before, after = previous_counts[name], current_counts[name]
        if before == 0 and after:
            added.append({"name": name, "count": after})
        elif after == 0 and before:
            removed.append({"name": name, "count": before})
        elif before != after:
            changed.append({"name": name, "before": before, "after": after, "delta": after - before})
    return {"from_snapshot_id": previous.id if previous else None, "to_snapshot_id": current.id, "added": added, "removed": removed, "changed": changed}


def prune_expired() -> dict:
    cutoff = utcnow() - timedelta(days=current_app.config["SNAPSHOT_RETENTION_DAYS"])
    snapshots = Snapshot.query.filter(Snapshot.scanned_at < cutoff).all()
    expired_jobs = ScanJob.query.filter(ScanJob.expires_at.is_not(None), ScanJob.expires_at < utcnow()).all()
    for row in snapshots + expired_jobs:
        db.session.delete(row)
    db.session.commit()
    return {"snapshots": len(snapshots), "jobs": len(expired_jobs)}


def billing_period_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    year, month = now.year, now.month
    if now.day < 25:
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return datetime(year, month, 25, tzinfo=timezone.utc)


def quota_status() -> dict:
    now = utcnow()
    daily_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    monthly = db.session.query(func.coalesce(func.sum(QuotaUsage.credits), 0)).filter(
        QuotaUsage.endpoint == "inventory", QuotaUsage.used_at >= billing_period_start(now)
    ).scalar()
    daily = db.session.query(func.coalesce(func.sum(QuotaUsage.credits), 0)).filter(
        QuotaUsage.endpoint == "inventory", QuotaUsage.used_at >= daily_start
    ).scalar()
    return {
        "daily_used": int(daily or 0),
        "daily_budget": current_app.config["INVENTORY_DAILY_BUDGET"],
        "daily_budget_enforced": False,
        "billing_used": int(monthly or 0),
        "billing_budget": current_app.config["INVENTORY_MONTHLY_BUDGET"],
        "billing_budget_enforced": True,
        "reserve": current_app.config["INVENTORY_RESERVE"],
    }


def quota_allows_scan(*, admin: bool = False) -> bool:
    quota = quota_status()
    credits = current_app.config["REQUESTS_PER_SCAN"]
    if quota["billing_used"] + credits > quota["billing_budget"]:
        return False
    return True
