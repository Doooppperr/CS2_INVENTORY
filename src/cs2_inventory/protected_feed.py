"""各监控账号最近一轮新增保护物品；只比较不同扫描，不访问库存服务。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from itertools import islice

from sqlalchemy import and_, case, func
from sqlalchemy.orm import aliased

from .api_inventory import API_POLICIES
from .dashboard_access import dashboard_scope
from .database import db
from .entitlements import entitlement_state
from .models import (
    BEIJING_TIMEZONE,
    ScanBatch,
    ScanJob,
    Snapshot,
    SnapshotItem,
    SteamTarget,
    Subscription,
    beijing_iso,
    utcnow,
)
from .observation import new_protected_units, snapshot_quality
from .scheduling import SCHEDULED_ROUNDS


def _protected_round(quality):
    return quality.get("source_policy") in API_POLICIES and quality.get(
        "api_contexts", {}
    ).get("protected", {}).get("complete")


def _pair(rows):
    """同一轮的进度和结束快照共享 scan_round。库存口径变化时重新建基线。"""
    current = previous = None
    for row in rows:
        quality = snapshot_quality(row)
        if not _protected_round(quality):
            continue
        if current is None:
            current = row
            current_round = quality.get("scan_round")
            policy = quality["source_policy"]
            continue
        if quality["source_policy"] != policy:
            break
        if current_round and quality.get("scan_round") == current_round:
            continue
        # 普通分区不完整时，资产也许此前已持有，不将缺失视作新增。
        if quality["comparable"]:
            previous = row
        break
    return current, previous


def _reports(pairs):
    """两侧按 asset_key 对齐；卖出不抵消新增，改名、重新出现和保护状态切换不算新增。"""
    reports = {
        cur.target_id: {
            "scanned_at": beijing_iso(cur.scanned_at),
            "baseline_at": beijing_iso(prev.scanned_at) if prev else None,
            "items": [],
        }
        for cur, prev in pairs
    }
    previous_ids = {cur.id: prev.id for cur, prev in pairs if prev is not None}
    if not previous_ids:
        return reports
    owner = {
        cur.id: (cur.target_id, prev.scanned_at)
        for cur, prev in pairs
        if prev is not None
    }
    item, old = SnapshotItem, aliased(SnapshotItem)
    rows = (
        db.session.query(
            item.snapshot_id,
            item.asset_key,
            item.name,
            item.amount,
            item.first_seen_at,
            item.evidence_json,
            old.amount.label("old_amount"),
        )
        .outerjoin(
            old,
            and_(
                old.snapshot_id == case(previous_ids, value=item.snapshot_id),
                old.asset_key == item.asset_key,
            ),
        )
        .filter(item.snapshot_id.in_(previous_ids), item.is_trade_protected.is_(True))
    )
    items = defaultdict(dict)
    for row in rows:
        target_id, baseline_at = owner[row.snapshot_id]
        count = new_protected_units(
            row.asset_key, row.amount, row.first_seen_at, row.old_amount, baseline_at
        )
        if count <= 0:
            continue
        evidence = json.loads(row.evidence_json)
        if "protected" not in evidence.get("api_contexts", []):
            continue
        key = evidence.get("market_name") or row.name
        entry = items[target_id].setdefault(
            key, {"key": key, "name": row.name, "count": 0}
        )
        entry["count"] += count
    for target_id, groups in items.items():
        reports[target_id]["items"] = list(groups.values())
    return reports


def scan_signal(snapshot, quality):
    """在任务完成时写入该轮摘要，使旧快照按日精简后仍能按轮回看。"""
    if snapshot is None or not _protected_round(quality):
        return None
    history = (
        Snapshot.query.filter_by(target_id=snapshot.target_id)
        .filter(Snapshot.id <= snapshot.id)
        .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
        .yield_per(8)
    )
    current, previous = _pair(history)
    return _reports([(current, previous)])[current.target_id]


def _dates(args):
    today = utcnow().astimezone(BEIJING_TIMEZONE).date()
    slot = args.get("slot", "latest")
    suffixes = {f"{hour:02d}": suffix for hour, suffix, _ in SCHEDULED_ROUNDS}
    if slot not in {"latest", *suffixes}:
        raise ValueError("扫描轮次无效")
    try:
        day = (
            date.fromisoformat(args["date"])
            if args.get("date")
            else today
            if slot != "latest"
            else None
        )
    except ValueError:
        raise ValueError("日期格式应为 YYYY-MM-DD") from None
    if day and not today - timedelta(days=7) <= day <= today:
        raise ValueError("可查看今天及之前七天的轮次")
    return (
        day,
        slot,
        f"{day.isoformat()}-{suffixes[slot]}" if slot != "latest" else None,
    )


def _legacy_reports(target_ids, start, end):
    if not target_ids:
        return {}
    # 发布前没有轮次摘要。原始快照在 48 小时后按日精简，不用精简后的两张推断单轮新增。
    compare_after = (utcnow() - timedelta(hours=48)).replace(tzinfo=None)
    candidates = defaultdict(list)
    for row in (
        db.session.query(Snapshot.id, Snapshot.target_id, Snapshot.scanned_at)
        .filter(Snapshot.target_id.in_(target_ids), Snapshot.scanned_at < end)
        .order_by(Snapshot.target_id, Snapshot.scanned_at.desc(), Snapshot.id.desc())
    ):
        candidates[row.target_id].append(row)
    # 旧数据只取定位扫描所需的少数库存包，跨账号批量读取而不是逐账号查询。
    remaining = {
        target_id: iter(values)
        for target_id, values in candidates.items()
        if start is None or values[0].scanned_at >= start
    }
    current = {}
    pairs = []
    while remaining:
        chunks = {tid: list(islice(iterator, 4)) for tid, iterator in remaining.items()}
        ids = [r.id for chunk in chunks.values() for r in chunk]
        snapshots = {r.id: r for r in Snapshot.query.filter(Snapshot.id.in_(ids))}
        for tid, chunk in chunks.items():
            done = len(chunk) < 4
            for row in chunk:
                prior = current.get(tid)
                if (prior and row.scanned_at < compare_after) or (
                    not prior and start and row.scanned_at < start
                ):
                    done = True
                    break
                snapshot = snapshots[row.id]
                quality = snapshot_quality(snapshot)
                if not _protected_round(quality):
                    continue
                if not prior:
                    current[tid] = snapshot, quality
                    continue
                cur, cur_quality = prior
                if quality["source_policy"] != cur_quality["source_policy"]:
                    done = True
                    break
                if (
                    cur_quality.get("scan_round")
                    and quality.get("scan_round") == cur_quality["scan_round"]
                ):
                    continue
                pairs.append((cur, snapshot if quality["comparable"] else None))
                current.pop(tid)
                done = True
                break
            if done:
                del remaining[tid]
                if tid in current:
                    pairs.append((current.pop(tid)[0], None))
    return _reports(pairs)


def protected_feed(user, args):
    scope = dashboard_scope(user, args)
    state = entitlement_state(user)
    if state == "expired":
        raise PermissionError("账号已过期")
    day, slot, slot_key = _dates(args)
    subs = {
        sub.target_id: sub.remark
        for sub in Subscription.query.filter_by(user_id=user.id)
    }
    targets = db.session.query(
        SteamTarget.id, SteamTarget.persona_name, SteamTarget.steamid
    )
    if scope == "mine":
        targets = targets.filter(SteamTarget.id.in_(subs))
    labels = {t.id: subs.get(t.id) or t.persona_name or t.steamid for t in targets}
    now = utcnow()
    start_day = day or (now.astimezone(BEIJING_TIMEZONE).date() - timedelta(days=7))
    start = (
        datetime.combine(start_day, time.min, BEIJING_TIMEZONE)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    end = (
        start + timedelta(days=1)
        if day
        else now.replace(tzinfo=None) + timedelta(microseconds=1)
    )
    cutoff = (
        user.activation_expires_at.replace(tzinfo=None) if state == "grace" else None
    )
    if cutoff:
        end = min(end, cutoff + timedelta(microseconds=1))

    # 作业摘要很小。只查当前范围与保留窗口，不为每个目标反复解压整份历史库存。
    signal_path = "$.protected_signal"
    latest = func.row_number().over(
        partition_by=ScanJob.target_id,
        order_by=(ScanJob.finished_at.desc(), ScanJob.id.desc()),
    )
    query = db.session.query(ScanJob.id.label("job_id"), latest.label("rank")).filter(
        ScanJob.target_id.in_(labels),
        ScanJob.status == "completed",
        ScanJob.finished_at.is_not(None),
        func.json_type(ScanJob.result_json, signal_path) == "object",
    )
    if slot_key:
        query = query.join(ScanBatch, ScanJob.batch_id == ScanBatch.id).filter(
            ScanBatch.slot_key == slot_key
        )
    else:
        query = query.filter(
            ScanJob.finished_at >= start, ScanJob.finished_at < end + timedelta(days=1)
        )
        if day:
            observed_day = func.substr(
                func.json_extract(ScanJob.result_json, signal_path + ".scanned_at"),
                1,
                10,
            )
            query = query.filter(observed_day == day.isoformat())
    if cutoff:
        query = query.filter(ScanJob.finished_at <= cutoff)
    selected = query.subquery()
    recent = (
        db.session.query(
            ScanJob.target_id, func.json_extract(ScanJob.result_json, signal_path)
        )
        .join(selected, ScanJob.id == selected.c.job_id)
        .filter(selected.c.rank == 1)
    )
    records = {target_id: json.loads(raw) for target_id, raw in recent}
    if not slot_key:
        records.update(_legacy_reports(labels.keys() - records.keys(), start, end))

    items = {}
    changed_accounts = 0
    compared_accounts = 0
    for target_id, record in records.items():
        compared_accounts += int(record["baseline_at"] is not None)
        changed_accounts += bool(record["items"])
        for detail in record["items"]:
            item = items.setdefault(
                detail["key"], {"name": detail["name"], "count": 0, "accounts": []}
            )
            item["count"] += detail["count"]
            item["accounts"].append(
                {
                    "target_id": target_id,
                    "label": labels[target_id],
                    "count": detail["count"],
                    "scanned_at": record["scanned_at"],
                    "baseline_at": record["baseline_at"],
                }
            )
    for item in items.values():
        item["account_count"] = len(item["accounts"])
        item["accounts"].sort(key=lambda entry: (-entry["count"], entry["target_id"]))
    return {
        "scope": scope,
        "slot": slot,
        "date": day.isoformat() if day else None,
        "updated_at": max((r["scanned_at"] for r in records.values()), default=None),
        "summary": {
            "total_accounts": len(labels),
            "observed_accounts": len(records),
            "compared_accounts": compared_accounts,
            "baseline_accounts": len(records) - compared_accounts,
            "changed_accounts": changed_accounts,
            "item_types": len(items),
            "added_units": sum(i["count"] for i in items.values())
            if compared_accounts
            else None,
        },
        "items": sorted(
            items.values(), key=lambda i: (-i["count"], -i["account_count"], i["name"])
        ),
    }


def prune_scan_signals(oldest_day):
    """沿用八个北京时间自然日的保留期，仅清除过期摘要，不改变历史任务。"""
    cutoff = datetime.combine(
        date.fromisoformat(oldest_day), time.min, BEIJING_TIMEZONE
    ).astimezone(timezone.utc)
    ScanJob.query.filter(
        ScanJob.finished_at < cutoff,
        func.json_type(ScanJob.result_json, "$.protected_signal").is_not(None),
    ).update(
        {
            ScanJob.result_json: func.json_remove(
                ScanJob.result_json, "$.protected_signal"
            )
        },
        synchronize_session=False,
    )
