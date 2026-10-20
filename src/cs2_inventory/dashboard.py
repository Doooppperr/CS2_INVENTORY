"""八天日报：独立基线、权限内聚合、无外部 API 请求。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta, timezone

from .auth import can_inventory_diagnostics
from .database import db
from .entitlements import entitlement_state
from .models import (
    BEIJING_TIMEZONE,
    DailyObservation,
    ScanBatch,
    ScanJob,
    SteamTarget,
    Subscription,
    TargetAssetHistory,
    beijing_iso,
    utcnow,
)
from .observation import (
    evidence_observation,
    quality_messages,
    reliable_id,
    row_evidence,
    signal_kind,
    snapshot_quality,
)


def stamp(value):
    return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()


def snapshot_assets(snapshot):
    rows = []
    history = {r.asset_key: r.first_seen_at for r in TargetAssetHistory.query.filter_by(target_id=snapshot.target_id)}
    for item in snapshot.items:
        evidence = row_evidence(item)
        rows.append({"id": item.asset_key, "name": item.name,
                     "market": evidence.get("market_name") or "",
                     "kind": evidence.get("item_type") or "",
                     "classid": item.classid, "instanceid": item.instanceid,
                     "amount": item.amount, "observation": evidence_observation(evidence),
                     "first_seen": stamp(history.get(item.asset_key) or item.first_seen_at or snapshot.scanned_at)})
    return rows


def calculate_report(current, previous):
    report = {"baseline_at": beijing_iso(previous.scanned_at) if previous else None,
              "scanned_at": beijing_iso(current.scanned_at), "comparable": False,
              "baseline_only": previous is None, "changes": [],
              "baseline_status": previous.status if previous else None,
              "quality": json.loads(current.report_json or "{}").get("quality", {}),
              "baseline_quality": json.loads(previous.report_json or "{}").get("quality", {}) if previous else {}}
    if not previous or current.status in {"private", "failed"} or previous.status in {"private", "failed"}:
        return report
    current_policy = report["quality"].get("source_policy", "legacy")
    previous_policy = report["baseline_quality"].get("source_policy", "legacy")
    if current_policy != previous_policy:
        return report
    before = json.loads(previous.assets_json)
    after = json.loads(current.assets_json)
    old_by_id = {a["id"]: a for a in before}
    new_by_id = {a["id"]: a for a in after}
    # 同一 assetid 的可信市场名称可补齐另一端，不以翻译后的显示名作为主键。
    for key in old_by_id.keys() & new_by_id.keys():
        left, right = old_by_id[key], new_by_id[key]
        market = right.get("market") or left.get("market")
        kind = right.get("kind") or left.get("kind")
        left["market"] = right["market"] = market
        left["kind"] = right["kind"] = kind
    groups = defaultdict(lambda: {"before": [], "after": []})
    for side, assets in (("before", before), ("after", after)):
        for asset in assets:
            key = asset.get("market") or (f"unknown:{asset.get('classid')}:{asset.get('instanceid')}" if asset.get("classid") else f"unidentified:{asset['id']}")
            groups[key][side].append(asset)
    comparable = current.status == previous.status == "ready"
    report["comparable"] = comparable
    for key, sides in sorted(groups.items()):
        old, new = sides["before"], sides["after"]
        all_assets = old + new
        representative = (new or old)[0]
        kind = signal_kind(representative.get("market"), representative.get("kind"))
        if kind == "excluded":
            continue
        b, a = sum(x["amount"] for x in old), sum(x["amount"] for x in new)
        arriving = [x for x in new if x["id"] not in old_by_id]
        first = sum(x["amount"] for x in arriving if reliable_id(x["id"])
                    and x["observation"] == "live" and x["first_seen"] > stamp(previous.scanned_at))
        returned = sum(x["amount"] for x in arriving if reliable_id(x["id"])
                       and x["first_seen"] <= stamp(previous.scanned_at))
        verified = comparable and kind == "eligible" and all(
            reliable_id(x["id"]) and x["observation"] == "live" for x in all_assets)
        increase = min(first, max(0, a - b - returned)) if verified else 0
        decrease = max(0, b - a) if verified else 0
        unknown = sum(x["amount"] for x in arriving if x["observation"] != "live" or not reliable_id(x["id"]))
        if a == b and not arriving:
            continue
        reasons = []
        if current.status != "ready":
            reasons.extend(report["quality"].get("reason_codes") or ["legacy_evidence"])
        if previous.status != "ready":
            reasons.append("baseline_incomplete")
        if any(x["observation"] != "live" for x in all_assets):
            reasons.append("freshness_unknown")
        if any(not reliable_id(x["id"]) for x in all_assets):
            reasons.append("identity_unknown")
        report["changes"].append({"key": key, "name": representative["name"], "before": b, "after": a,
            "delta": a - b, "first_seen": first, "reappeared": returned, "unverified_arrivals": unknown,
            "increase": increase, "decrease": decrease, "verified": verified,
            "reason": "" if verified else "；".join(quality_messages(reasons)) or "饰品分类尚未核实"})
    return report


def save_daily(target, *, day, scanned_at, status, snapshot=None):
    row = db.session.get(DailyObservation, (target.id, day))
    if row and row.status == "ready":
        return row  # 已确认的日基线不被重复任务/失败重试覆盖。
    if row is None:
        row = DailyObservation(target_id=target.id, day=day)
        db.session.add(row)
    row.scanned_at, row.status = scanned_at, status
    row.report_json = json.dumps({"quality": snapshot_quality(snapshot) if snapshot else
                                  {"comparable": False, "reason_codes": [status]}}, ensure_ascii=False)
    row.assets_json = json.dumps(snapshot_assets(snapshot) if snapshot else [], ensure_ascii=False)
    prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    previous = db.session.get(DailyObservation, (target.id, prev_day))
    row.report_json = json.dumps(calculate_report(row, previous), ensure_ascii=False)
    # 补回某日基线后，只重算紧邻下一日，不跨日期回退。
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    following = db.session.get(DailyObservation, (target.id, next_day))
    if following:
        following.report_json = json.dumps(calculate_report(following, row), ensure_ascii=False)
    return row


def record_daily_job(job, target, snapshot=None):
    if job.kind != "daily" or not job.batch_id:
        return
    batch = db.session.get(ScanBatch, job.batch_id)
    slot = batch.slot_key if batch else ""
    if not slot or not slot.endswith("-R2"):
        return
    save_daily(target, day=slot[:10], scanned_at=target.last_scan_at or utcnow(),
               status=target.scan_status, snapshot=snapshot)


def dashboard_data(user, args):
    if args.get("section") == "pending" and not can_inventory_diagnostics(user):
        raise PermissionError("页面不存在")
    today = utcnow().astimezone(BEIJING_TIMEZONE).date()
    try:
        day = date.fromisoformat(args.get("date") or today.isoformat())
    except ValueError:
        raise ValueError("日期格式应为 YYYY-MM-DD") from None
    if not today - timedelta(days=7) <= day <= today:
        raise ValueError("仅支持今天及之前七天的日报")
    state = entitlement_state(user)
    if state == "expired":
        raise PermissionError("账号已过期")
    scope = args.get("scope", "mine")
    if scope not in {"mine", "all"}:
        raise ValueError("未知监控范围")
    if scope == "all" and user.role != "admin":
        raise PermissionError("仅管理员可查看全部目标")
    subscriptions = {s.target_id: s for s in Subscription.query.filter_by(user_id=user.id)}
    targets = SteamTarget.query.all() if scope == "all" else [s.target for s in subscriptions.values()]
    ids = [t.id for t in targets]
    query = DailyObservation.query.filter(DailyObservation.target_id.in_(ids), DailyObservation.day == day.isoformat())
    cutoff = user.activation_expires_at if state == "grace" else None
    if cutoff:
        query = query.filter(DailyObservation.scanned_at <= cutoff)
    observations = {r.target_id: r for r in query}
    # 进度也先做目标与到期时间过滤，不能泄漏其他用户或冻结后的作业。
    jobs = ScanJob.query.join(ScanBatch, ScanJob.batch_id == ScanBatch.id).filter(
        ScanJob.target_id.in_(ids), ScanBatch.slot_key == f"{day.isoformat()}-R2")
    if cutoff:
        jobs = jobs.filter(ScanJob.finished_at <= cutoff)
    job_rows = jobs.all()
    pending_ids = {j.target_id for j in job_rows if j.status in {"queued", "running"}}
    summary = {"targets": len(ids), "observed": len(observations), "comparable": 0,
               "private": 0, "failed": 0, "partial": 0, "baseline_only": 0,
               "pending": len(pending_ids), "missing": len(set(ids) - observations.keys() - pending_ids)}
    account_statuses = []
    quality_counts = defaultdict(int)
    aggregates = {"signals": {}, "reappeared": {}, "pending": {}}
    for target in targets:
        row = observations.get(target.id)
        sub = subscriptions.get(target.id)
        label = " - ".join(v for v in (sub.remark if sub else None, target.persona_name, target.steamid) if v)
        report = json.loads(row.report_json) if row else {}
        codes = []
        if target.id in pending_ids:
            codes = ["pending"]
        elif not row:
            codes = ["missing"]
        elif row.status in {"private", "failed"}:
            codes = [row.status]
        else:
            if row.status != "ready":
                codes.extend(report.get("quality", {}).get("reason_codes") or ["legacy_evidence"])
            if report.get("baseline_only"):
                codes.append("baseline_missing")
            elif not report.get("comparable") and report.get("baseline_status") != "ready":
                # 旧日报未保留基线证据，不猜测是哪一端完整。
                codes.append("baseline_incomplete")
        for code in set(codes):
            quality_counts[code] += 1
        account_statuses.append({"target_id": target.id, "label": label,
            "status": "pending" if target.id in pending_ids else row.status if row else "missing",
            "scanned_at": report.get("scanned_at"), "baseline_at": report.get("baseline_at"),
            "comparable": report.get("comparable", False), "reasons": quality_messages(codes),
            "reason_codes": list(dict.fromkeys(codes))})
        if not row:
            continue
        if row.status in {"private", "failed", "partial"}:
            summary[row.status] += 1
        summary["comparable"] += int(report.get("comparable", False))
        summary["baseline_only"] += int(report.get("baseline_only", False))
        for change in report.get("changes", []):
            if not can_inventory_diagnostics(user) and not change.get("verified"):
                continue
            sections = []
            if change["verified"] and (change["increase"] or change["decrease"]):
                sections.append("signals")
            if change["reappeared"]:
                sections.append("reappeared")
            if not change["verified"]:
                sections.append("pending")
            for section in sections:
                group = aggregates[section].setdefault(change["key"], {
                    "name": change["name"], "key": change["key"], "accounts": [],
                    "increase": 0, "decrease": 0, "net": 0, "account_count": 0, "max_increase": 0})
                # key 仅内部排序，不向外暴露分类或资产证据。
                detail = {k: v for k, v in change.items() if k != "key"}
                detail.update(target_id=target.id, label=label, scanned_at=report["scanned_at"], baseline_at=report["baseline_at"])
                group["accounts"].append(detail)
                group["increase"] += change["increase"]
                group["decrease"] += change["decrease"]
                group["net"] += change["delta"]
                group["account_count"] += int(change["increase"] > 0)
                group["max_increase"] = max(group["max_increase"], change["increase"])

    def integer(name, default, upper=1000000):
        try:
            value = int(args.get(name, default))
        except (ValueError, TypeError):
            raise ValueError(f"{name} 应为正整数") from None
        if not 1 <= value <= upper:
            raise ValueError(f"{name} 超出范围")
        return value

    peers, per_account, single = integer("min_accounts", 2), integer("min_per_account", 1), integer("min_single", 2)
    section = args.get("section", "signals")
    mode, sort = args.get("match", "any"), args.get("sort", "accounts")
    direction = args.get("direction", "all")
    if section not in aggregates or mode not in {"any", "all"} or sort not in {"accounts", "increase", "max", "decrease"} or direction not in {"all", "increase", "decrease"}:
        raise ValueError("筛选参数无效")
    enabled = [args.get("peers", "0"), args.get("single", "0")]
    if any(v not in {"0", "1"} for v in enabled):
        raise ValueError("快捷筛选应为 0 或 1")
    rows = []
    search = args.get("q", "").strip().casefold()
    for row in aggregates[section].values():
        if search and search not in row["name"].casefold() and search not in row["key"].casefold():
            continue
        if section == "signals":
            checks = []
            if enabled[0] == "1":
                checks.append(sum(a["increase"] >= per_account for a in row["accounts"]) >= peers)
            if enabled[1] == "1":
                checks.append(row["max_increase"] >= single)
            if checks and not (all(checks) if mode == "all" else any(checks)):
                continue
            if direction != "all" and row[direction] == 0:
                continue
        rows.append(row)
    primary = {"accounts": "account_count", "increase": "increase", "max": "max_increase", "decrease": "decrease"}[sort]
    rows.sort(key=lambda r: (-r[primary], -r["increase"], r["key"]))
    page, size = integer("page", 1), integer("page_size", 20, 100)
    total = len(rows)
    rows = rows[(page - 1) * size:page * size]
    for row in rows:
        row.pop("key", None)
    status = "generating" if pending_ids else "complete" if summary["comparable"] == len(ids) and ids else "partial" if summary["comparable"] else "no_comparable"
    return {"date": day.isoformat(), "baseline_date": (day - timedelta(days=1)).isoformat(),
            "status": status, "summary": summary, "section": section, "items": rows,
            "account_statuses": account_statuses,
            "quality_summary": [{"code": c, "message": quality_messages([c])[0], "accounts": n}
                                for c, n in sorted(quality_counts.items()) if quality_messages([c])],
            "page": page, "pages": max(1, (total + size - 1) // size), "total": total,
            "section_counts": {k: len(v) for k, v in aggregates.items()},
            "note": "总数按跨来源物品去重后的观测并集，Steam 公开计数不作为上限。首次发现不等于买入；再次出现不等于确定从组件取出。仅比较相邻日期早间深度扫描，未核实数据不进正式排行。历史识别从平台保留记录开始。"}
