"""库存业务视图与诊断权限分离；跨轮分区不冒充完整库存。"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from .api_inventory import API_POLICIES, SECTIONS
from .auth import can_inventory_diagnostics
from .entitlements import snapshot_query_for_user
from .models import ScanJob, Snapshot, beijing_iso
from .observation import new_protected_units, row_evidence, snapshot_quality


@dataclass
class InventorySources:
    """同一目标的当前分区、完整轮次和按北京自然日选定的加仓对照。"""
    sections: dict
    full: Snapshot | None
    baselines: dict
    quality: dict
    last_quality: dict

    def item_ids(self):
        return {row.id for row in (*self.sections.values(), *self.baselines.values())}

def inventory_sources(rows):
    # 先按时间降序选来源；只在入选的分区及 1/7 日基线中读取饰品。
    api_rows = []
    qualities = {}
    for row in rows:
        quality = snapshot_quality(row)
        if quality.get("source_policy") in API_POLICIES:
            api_rows.append(row)
            qualities[row.id] = quality
    newest = qualities[api_rows[0].id] if api_rows else {"source_policy": "legacy"}
    sections = {}
    full = None
    for row in api_rows:
        q = qualities[row.id]
        if full is None and q["source_policy"] == newest["source_policy"] and row.coverage == "complete" and q["comparable"]:
            full = row
        for section in SECTIONS:
            if section not in sections and displayable(q.get("api_contexts", {}).get(section, {})):
                sections[section] = row
        if full is not None and len(sections) == len(SECTIONS):
            break
    baselines = {}
    current = sections.get("protected")
    if current and qualities[current.id]["api_contexts"]["protected"].get("complete"):
        current_date = date.fromisoformat(beijing_iso(current.scanned_at)[:10])
        dates = {(current_date - timedelta(days=n)).isoformat(): str(n) for n in (7, 1)}
        for row in api_rows:
            day = beijing_iso(row.scanned_at)[:10]
            if day not in dates:
                continue
            q = qualities[row.id]
            if q["source_policy"] != qualities[current.id]["source_policy"] or not q.get("api_contexts", {}).get("protected", {}).get("complete"):
                continue
            period = dates.pop(day)
            if q["comparable"]:
                baselines[period] = row
            if not dates:
                break
    return InventorySources(sections, full, baselines, qualities, newest)


def _protected_changes(current, baseline):
    """逐资产的正增量；列表数字与单目标明细同用这一份口径。"""
    before = {item.asset_key: item.amount for item in baseline.items}
    for item in current.items:
        if not item.is_trade_protected:
            continue
        evidence = row_evidence(item)
        if "protected" not in evidence.get("api_contexts", []):
            continue
        delta = new_protected_units(item.asset_key, item.amount, item.first_seen_at,
                                    before.get(item.asset_key), baseline.scanned_at)
        if delta:
            yield item.name, evidence.get("market_name") or item.name, delta


def protected_additions(sources):
    result = {"7": None, "1": None}
    for period, baseline in sources.baselines.items():
        result[period] = sum(count for _, _, count in _protected_changes(sources.sections["protected"], baseline))
    return result


def target_additions(target_id, user, days):
    rows = snapshot_query_for_user(user, target_id).order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc()).all()
    sources = inventory_sources(rows)
    current = sources.sections.get("protected")
    baseline = sources.baselines.get(str(days))
    report = {"target_id": target_id, "days": days, "state": "no_observation", "items": [], "total": None,
              "observed_at": beijing_iso(current.scanned_at) if current else None, "baseline_at": None}
    if current is None or not sources.quality[current.id]["api_contexts"]["protected"].get("complete"):
        return report
    if baseline is None:
        report["state"] = "no_baseline"
        return report
    groups = {}
    for name, market, count in _protected_changes(current, baseline):
        item = groups.setdefault(market, {"name": name, "market_name": market, "count": 0})
        item["count"] += count
    items = sorted(groups.values(), key=lambda item: (-item["count"], item["name"], item["market_name"]))
    report.update(items=items, total=sum(item["count"] for item in items),
                  state="ready", baseline_at=beijing_iso(baseline.scanned_at))
    return report


def displayable(context):
    return context.get("displayable", context.get("complete", False))


def is_api_snapshot(snapshot):
    return snapshot_quality(snapshot).get("source_policy") in API_POLICIES


def complete_snapshot(rows):
    """新口径开始后，不向旧混合快照或不完整新快照回退。"""
    rows = list(rows)
    api_rows = [row for row in rows if is_api_snapshot(row)]
    if api_rows:
        policy = snapshot_quality(api_rows[0]).get("source_policy")
        return next((row for row in api_rows if snapshot_quality(row).get("source_policy") == policy and row.coverage == "complete" and snapshot_quality(row).get("comparable")), None)
    return rows[0] if rows else None  # 旧历史页面兼容；不进入新分区展示。


def section_snapshot_ids(rows):
    selected = {}
    for row in rows:
        quality = snapshot_quality(row)
        if quality.get("source_policy") not in API_POLICIES:
            continue
        for section in SECTIONS:
            if section not in selected and quality.get("api_contexts", {}).get(section, {}).get("displayable", quality.get("api_contexts", {}).get(section, {}).get("complete")):
                selected[section] = row.id
        if len(selected) == len(SECTIONS):
            break
    return set(selected.values())


def inventory_view(target_id, user, *, rows=None, include_items=True, sources=None):
    if sources is None:
        if rows is None:
            rows = snapshot_query_for_user(user, target_id).order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc()).all()
        sources = inventory_sources(rows)
    full = sources.full
    selected = sources.sections
    sections = {}
    for section in SECTIONS:
        row = selected.get(section)
        if not row:
            sections[section] = {"items": [], "total_items": None, "updated_at": None}
            continue
        context = sources.quality[row.id]["api_contexts"][section]
        counts = Counter()
        for item in row.items:
            if section not in row_evidence(item).get("api_contexts", []):
                continue
            if section == "other" and item.is_trade_protected:
                continue  # 同轮去重后的保护资产只在保护区展示。
            counts[(item.name, bool(item.is_trade_protected))] += item.amount
        sections[section] = {"total_items": sum(counts.values()), "updated_at": context["scanned_at"],
            "items": [{"name": name, "count": count, "is_trade_protected": protected}
                      for (name, protected), count in sorted(counts.items())] if include_items else []}
    result = {"inventory_sections": sections,
              "protected_additions": protected_additions(sources),
              "last_complete_snapshot_at": beijing_iso(full.scanned_at) if full else None,
              "last_complete_snapshot_id": full.id if full else None,
              "total_items": full.total_items if full else None,
              "item_types": full.item_types if full else None,
              "sections_same_complete_round": bool(full and len(selected) == 2 and all(
                  row.id == full.id for row in selected.values()))}
    if can_inventory_diagnostics(user):
        result["diagnostics"] = sources.last_quality
        job = ScanJob.query.filter_by(target_id=target_id).order_by(ScanJob.id.desc()).first()
        if job and job.result_json:
            result["diagnostics"] = {**result["diagnostics"], "last_job": json.loads(job.result_json).get("diagnostics", {})}
    return result


DIAGNOSTIC_KEYS = {
    "quality", "errors", "elapsed_ms", "coverage", "last_error", "scan_status", "scan_label", "scan_detail",
    "comparison_note", "removal_comparable", "comparable", "reason_codes", "reasons", "reason", "note",
    "unconfirmed", "pending", "historical_closed", "quality_summary", "account_statuses", "baseline_status",
    "baseline_quality", "diagnostics", "source_policy", "api_contexts", "scan_round", "official_quota", "quota", "localization",
    "origin", "coverage_scope", "page_complete", "inventory_complete", "provider_requests", "api_failure",
    "verified", "baseline_only", "pending_jobs", "last_scan_at",
}


def business_payload(value):
    """从后端裁剪所有诊断，包含嵌套任务结果、历史快照和日报明细。"""
    if isinstance(value, list):
        return [business_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = dict(value)
    if "inventory_sections" in value and "latest" in value:
        value["latest"] = ({"id": value["last_complete_snapshot_id"], "total_items": value["total_items"],
                            "scanned_at": value["last_complete_snapshot_at"]} if value.get("last_complete_snapshot_id") else None)
    quality = value.get("quality") or {}
    if quality.get("source_policy") in API_POLICIES and not quality.get("comparable"):
        value["total_items"] = None
        value["item_types"] = None
    if "from_snapshot_id" in value and not value.get("comparable"):
        for field in ("added", "removed", "changed", "reappeared"):
            value[field] = []
    return {key: business_payload(item) for key, item in value.items() if key not in DIAGNOSTIC_KEYS}
