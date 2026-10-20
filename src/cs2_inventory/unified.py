from __future__ import annotations

import collections
import json
from typing import Any, Mapping

from .observation import asset_evidence, scan_quality

INTERNAL_GROUPS = (
    "protected_live",
    "protected_observed",
    "public_missing_live",
    "public_missing_observed",
    "public",
)


def unify_inventory(result: Mapping[str, Any]) -> dict[str, Any]:
    """Merge reliable categories by assetid and retain explicit live protection."""
    if "_api_assets" in result:
        assets = list(result["_api_assets"])
        counts = collections.Counter()
        for asset in assets:
            counts[asset["name"]] += int(asset["amount"])
        return {"steamid": str(result.get("steamid") or ""),
                "items": [{"name": name, "count": count} for name, count in sorted(counts.items())],
                "total_items": sum(counts.values()), "item_types": len(counts),
                "coverage": result["coverage"]["status"], "elapsed_ms": result.get("elapsed_ms", 0),
                "errors": list(result.get("errors") or []), "_assets": assets,
                "_scan_status": scan_quality(result), "_quality": dict(result["quality"])}
    assets: dict[str, dict[str, Any]] = {}
    synthetic_index = 0
    for group_name in INTERNAL_GROUPS:
        is_trade_protected = group_name == "protected_live"
        for row in result.get(group_name) or []:
            name = str(row.get("name") or "Unknown item")
            count = max(1, int(row.get("count", 1) or 1))
            metadata = [value for value in (row.get("assets") or []) if isinstance(value, Mapping)]
            assetids = [str(value) for value in (row.get("assetids") or []) if value]
            sources = [str(value) for value in (row.get("sources") or [])]
            described_ids: set[str] = set()
            for entry in metadata:
                assetid = str(entry.get("assetid") or "")
                if not assetid:
                    continue
                described_ids.add(assetid)
                assets[assetid] = {
                    "asset_key": assetid,
                    "name": str(entry.get("name") or name),
                    "raw_name": str(entry.get("raw_name") or entry.get("name") or name),
                    "market_name": entry.get("market_name", ""),
                    "item_type": entry.get("item_type", ""),
                    "observation": ("cached" if group_name.endswith("_observed") else entry.get("observation", "unknown")),
                    "classid": str(entry.get("classid") or ""),
                    "instanceid": str(entry.get("instanceid") or "0"),
                    "name_localized": bool(entry.get("name_localized", False)),
                    "amount": max(1, int(entry.get("amount", 1) or 1)),
                    "sources": sources,
                    "is_trade_protected": is_trade_protected,
                }
            for assetid in assetids:
                if assetid in described_ids:
                    continue
                assets[assetid] = {
                    "asset_key": assetid,
                    "name": name,
                    "raw_name": name,
                    "observation": "cached" if group_name.endswith("_observed") else "unknown",
                    "classid": "",
                    "instanceid": "0",
                    "name_localized": False,
                    "amount": 1,
                    "sources": sources,
                    "is_trade_protected": is_trade_protected,
                }
            recorded = sum(int(value["amount"]) for key, value in assets.items() if key in described_ids or key in assetids)
            missing = max(0, count - recorded)
            for _ in range(missing):
                synthetic_index += 1
                key = f"synthetic:{name}:{synthetic_index}"
                assets[key] = {
                    "asset_key": key,
                    "name": name,
                    "raw_name": name,
                    "observation": "cached" if group_name.endswith("_observed") else "unknown",
                    "classid": "",
                    "instanceid": "0",
                    "name_localized": False,
                    "amount": 1,
                    "sources": sources,
                    "is_trade_protected": is_trade_protected,
                }

    counts: collections.Counter[str] = collections.Counter()
    for asset in assets.values():
        counts[asset["name"]] += int(asset["amount"])
    items = [{"name": name, "count": count} for name, count in sorted(counts.items())]
    coverage = result.get("coverage") or {}
    return {
        "steamid": str(result.get("steamid") or ""),
        "items": items,
        "total_items": sum(counts.values()),
        "item_types": len(items),
        "coverage": str(coverage.get("status") or "unknown"),
        "elapsed_ms": int(result.get("elapsed_ms", 0) or 0),
        "errors": [str(value) for value in (result.get("errors") or [])],
        "_assets": list(assets.values()),
        "_scan_status": scan_quality(result),
        "_quality": dict(result.get("quality") or {}),
    }


def public_payload(unified: Mapping[str, Any], *, scanned_at: str | None = None) -> dict[str, Any]:
    items = list(unified.get("items") or [])
    if (unified.get("_quality") or {}).get("source_policy") == "api_only_v1":
        counts = collections.Counter()
        for asset in unified.get("_assets") or []:
            counts[(asset["name"], bool(asset.get("is_trade_protected")))] += int(asset["amount"])
        items = [{"name": name, "count": count, "is_trade_protected": protected}
                 for (name, protected), count in sorted(counts.items(), key=lambda item: (-int(item[0][1]), item[0][0]))]
    data = {
        "items": items,
        "total_items": int(unified.get("total_items", 0)),
        "item_types": int(unified.get("item_types", 0)),
        "coverage": str(unified.get("coverage") or "unknown"),
        "scanned_at": scanned_at,
        "elapsed_ms": int(unified.get("elapsed_ms", 0)),
        "errors": list(unified.get("errors") or []),
        "quality": dict(unified.get("_quality") or {}),
    }
    if data["quality"].get("source_policy") == "api_only_v1":
        sections = {}
        for section in ("protected", "other"):
            context = data["quality"].get("api_contexts", {}).get(section, {})
            counts = collections.Counter()
            for asset in unified.get("_assets") or []:
                if section in asset.get("api_contexts", []) and not (section == "other" and asset.get("is_trade_protected")):
                    counts[(asset["name"], bool(asset.get("is_trade_protected")))] += int(asset["amount"])
            sections[section] = {"items": [{"name": name, "count": count, "is_trade_protected": flag}
                for (name, flag), count in sorted(counts.items())],
                "total_items": sum(counts.values()) if context.get("complete") else None,
                "updated_at": context.get("scanned_at") if context.get("complete") else None}
        data["inventory_sections"] = sections
        if not data["quality"].get("comparable"):
            data["total_items"] = None
            data["item_types"] = None
    return data


def evidence_json(asset: Mapping[str, Any]) -> str:
    return json.dumps(asset_evidence(asset), ensure_ascii=False, separators=(",", ":"))
