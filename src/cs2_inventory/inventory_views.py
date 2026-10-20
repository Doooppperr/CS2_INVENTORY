"""库存业务视图与诊断权限分离；跨轮分区不冒充完整库存。"""
from __future__ import annotations

import json
from collections import Counter

from .api_inventory import SECTIONS, SOURCE_POLICY
from .auth import can_inventory_diagnostics
from .entitlements import snapshot_query_for_user
from .models import ScanJob, Snapshot, beijing_iso
from .observation import row_evidence, snapshot_quality


def is_api_snapshot(snapshot):
    return snapshot_quality(snapshot).get("source_policy") == SOURCE_POLICY


def complete_snapshot(rows):
    """新口径开始后，不向旧混合快照或不完整新快照回退。"""
    rows = list(rows)
    api_rows = [row for row in rows if is_api_snapshot(row)]
    if api_rows:
        return next((row for row in api_rows if row.coverage == "complete" and snapshot_quality(row).get("comparable")), None)
    return rows[0] if rows else None  # 旧历史页面兼容；不进入新分区展示。


def section_snapshot_ids(rows):
    selected = {}
    for row in rows:
        quality = snapshot_quality(row)
        if quality.get("source_policy") != SOURCE_POLICY:
            continue
        for section in SECTIONS:
            if section not in selected and quality.get("api_contexts", {}).get(section, {}).get("complete"):
                selected[section] = row.id
        if len(selected) == len(SECTIONS):
            break
    return set(selected.values())


def inventory_view(target_id, user, *, rows=None, include_items=True):
    if rows is None:
        rows = snapshot_query_for_user(user, target_id).order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc()).all()
    api_rows = [row for row in rows if is_api_snapshot(row)]
    full = complete_snapshot(api_rows)
    sections = {}
    for section in SECTIONS:
        row = next((candidate for candidate in api_rows
                    if snapshot_quality(candidate).get("api_contexts", {}).get(section, {}).get("complete")), None)
        if not row:
            sections[section] = {"items": [], "total_items": None, "updated_at": None}
            continue
        context = snapshot_quality(row)["api_contexts"][section]
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
              "last_complete_snapshot_at": beijing_iso(full.scanned_at) if full else None,
              "last_complete_snapshot_id": full.id if full else None,
              "total_items": full.total_items if full else None}
    if can_inventory_diagnostics(user):
        result["diagnostics"] = snapshot_quality(api_rows[0]) if api_rows else {"source_policy": "legacy"}
        job = ScanJob.query.filter_by(target_id=target_id).order_by(ScanJob.id.desc()).first()
        if job and job.result_json:
            result["diagnostics"] = {**result["diagnostics"], "last_job": json.loads(job.result_json).get("diagnostics", {})}
    return result


DIAGNOSTIC_KEYS = {
    "quality", "errors", "elapsed_ms", "coverage", "last_error", "scan_status", "scan_label", "scan_detail",
    "comparison_note", "removal_comparable", "comparable", "reason_codes", "reasons", "reason", "note",
    "unconfirmed", "pending", "historical_closed", "quality_summary", "account_statuses", "baseline_status",
    "baseline_quality", "diagnostics", "source_policy", "api_contexts", "scan_round", "official_quota", "quota", "localization",
    "verified", "baseline_only", "pending_jobs", "last_scan_at",
}


def business_payload(value):
    """从后端裁剪所有诊断，包含嵌套任务结果、历史快照和日报明细。"""
    if isinstance(value, list):
        return [business_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = dict(value)
    if "from_snapshot_id" in value and not value.get("comparable"):
        for field in ("added", "removed", "changed", "reappeared"):
            value[field] = []
    return {key: business_payload(item) for key, item in value.items() if key not in DIAGNOSTIC_KEYS}
