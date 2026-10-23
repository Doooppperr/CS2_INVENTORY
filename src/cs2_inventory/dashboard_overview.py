"""只读库存趋势与双向排行，复用已校验的相邻自然日日报。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import tuple_
from sqlalchemy.orm import load_only

from .entitlements import entitlement_state
from .models import (
    BEIJING_TIMEZONE,
    DailyObservation,
    SteamTarget,
    Subscription,
    beijing_iso,
    utcnow,
)


def asset_group(asset):
    return asset.get("market") or (f"unknown:{asset.get('classid')}:{asset.get('instanceid')}"
                                   if asset.get("classid") else f"unidentified:{asset['id']}")


def overview_data(user, args):
    today = utcnow().astimezone(BEIJING_TIMEZONE).date()
    try:
        end = date.fromisoformat(args.get("date") or today.isoformat())
        days = int(args.get("days", 7))
    except (ValueError, TypeError):
        raise ValueError("日期或周期格式无效") from None
    if days not in {3, 7} or not today - timedelta(days=7) <= end <= today:
        raise ValueError("支持近 3 日或近 7 日，结束日期须在保留范围内")
    scope = args.get("scope", "mine")
    if scope not in {"mine", "all"}:
        raise ValueError("未知监控范围")
    if scope == "all" and user.role != "admin":
        raise PermissionError("仅管理员可查看全部目标")
    state = entitlement_state(user)
    if state == "expired":
        raise PermissionError("账号已过期")
    subscriptions = {sub.target_id: sub for sub in Subscription.query.filter_by(user_id=user.id)}
    targets = SteamTarget.query
    if scope == "mine":
        targets = targets.filter(SteamTarget.id.in_(subscriptions))
    targets = {target.id: target for target in targets}
    start = end - timedelta(days=days - 1)
    query = DailyObservation.query.filter(
        DailyObservation.target_id.in_(targets),
        DailyObservation.day >= start.isoformat(), DailyObservation.day <= end.isoformat())
    if state == "grace":
        query = query.filter(DailyObservation.scanned_at <= user.activation_expires_at)
    observations = query.options(load_only(
        DailyObservation.target_id, DailyObservation.day, DailyObservation.scanned_at,
        DailyObservation.status, DailyObservation.report_json,
    )).order_by(DailyObservation.day, DailyObservation.target_id).all()
    points = { (start + timedelta(days=i)).isoformat(): {
        "date": (start + timedelta(days=i)).isoformat(), "increase": None, "decrease": None,
        "observed_accounts": 0, "compared_accounts": 0, "total_accounts": len(targets)
    } for i in range(days)}
    groups = {}
    latest = {}
    updated_at = None
    changed_accounts = set()
    for row in observations:
        latest[row.target_id] = row
        timestamp = beijing_iso(row.scanned_at)
        updated_at = max(updated_at or timestamp, timestamp)
        point = points[row.day]
        point["observed_accounts"] += 1
        report = json.loads(row.report_json or "{}")
        if row.status != "ready" or not report.get("comparable"):
            continue
        point["compared_accounts"] += 1
        point["increase"] = point["increase"] or 0
        point["decrease"] = point["decrease"] or 0
        for change in report.get("changes", []):
            if not change.get("verified"):
                continue
            increase, decrease = change.get("increase", 0), change.get("decrease", 0)
            if not (increase or decrease):
                continue
            point["increase"] += increase
            point["decrease"] += decrease
            changed_accounts.add(row.target_id)
            group = groups.setdefault(change["key"], {"name": change["name"], "accounts": {}})
            group["name"] = change["name"]
            account = group["accounts"].setdefault(row.target_id, {"increase": 0, "decrease": 0})
            account["increase"] += increase
            account["decrease"] += decrease

    # 持有量来自周期内该账号最后一份日报，既不跨账号相加，也不跨日期累加。
    # 仅批量读取末次资产清单，不加载七天全部资产，也不逐账号产生额外查询。
    latest_keys = [(target_id, row.day) for target_id, row in latest.items() if row.status == "ready"]
    latest_assets = dict(DailyObservation.query.with_entities(
        DailyObservation.target_id, DailyObservation.assets_json,
    ).filter(tuple_(DailyObservation.target_id, DailyObservation.day).in_(latest_keys)).all())
    holdings = {}
    for target_id, row in latest.items():
        if row.status != "ready":
            holdings[target_id] = None
            continue
        counts = Counter()
        for asset in json.loads(latest_assets[target_id]):
            counts[asset_group(asset)] += asset["amount"]
        holdings[target_id] = counts
    rankings = defaultdict(list)
    for key, group in groups.items():
        for direction in ("increase", "decrease"):
            accounts = []
            for target_id, counts in group["accounts"].items():
                if not counts[direction]:
                    continue
                target, sub = targets[target_id], subscriptions.get(target_id)
                held = holdings.get(target_id)
                accounts.append({"target_id": target_id,
                    "label": (sub.remark if sub else None) or target.persona_name or target.steamid,
                    "count": counts[direction], "holding": held[key] if held is not None else None,
                    "holding_at": beijing_iso(latest[target_id].scanned_at)})
            if accounts:
                accounts.sort(key=lambda item: (-item["count"], item["target_id"]))
                rankings[direction].append({"name": group["name"], "count": sum(a["count"] for a in accounts),
                    "account_count": len(accounts), "accounts": accounts, "_key": key})
    for direction in ("increase", "decrease"):
        rankings[direction].sort(key=lambda item: (-item["count"], -item["account_count"], item["_key"]))
        rankings[direction] = rankings[direction][:5]
        for item in rankings[direction]:
            item.pop("_key")
    available = [point for point in points.values() if point["compared_accounts"]]
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "days": days, "scope": scope,
            "updated_at": updated_at, "trend": list(points.values()),
            "summary": {"increase": sum(p["increase"] for p in available) if available else None,
                        "decrease": sum(p["decrease"] for p in available) if available else None,
                        "changed_accounts": len(changed_accounts), "total_accounts": len(targets),
                        "available_days": len(available)},
            "rankings": dict(rankings)}
