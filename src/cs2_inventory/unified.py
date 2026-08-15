from __future__ import annotations

import collections
import json
from typing import Any, Mapping

INTERNAL_GROUPS = (
    "protected_live",
    "protected_observed",
    "public_missing_live",
    "public_missing_observed",
    "public",
)


def unify_inventory(result: Mapping[str, Any]) -> dict[str, Any]:
    """Merge reliable categories by assetid and retain explicit live protection."""
    assets: dict[str, dict[str, Any]] = {}
    synthetic_index = 0
    for group_name in INTERNAL_GROUPS:
        is_trade_protected = group_name == "protected_live"
        for row in result.get(group_name) or []:
            name = str(row.get("name") or "Unknown item")
            count = max(1, int(row.get("count", 1) or 1))
            assetids = [str(value) for value in (row.get("assetids") or []) if value]
            sources = [str(value) for value in (row.get("sources") or [])]
            for assetid in assetids:
                assets[assetid] = {
                    "asset_key": assetid,
                    "name": name,
                    "amount": 1,
                    "sources": sources,
                    "is_trade_protected": is_trade_protected,
                }
            missing = max(0, count - len(assetids))
            for _ in range(missing):
                synthetic_index += 1
                key = f"synthetic:{name}:{synthetic_index}"
                assets[key] = {
                    "asset_key": key,
                    "name": name,
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
    }


def public_payload(unified: Mapping[str, Any], *, scanned_at: str | None = None) -> dict[str, Any]:
    return {
        "items": list(unified.get("items") or []),
        "total_items": int(unified.get("total_items", 0)),
        "item_types": int(unified.get("item_types", 0)),
        "coverage": str(unified.get("coverage") or "unknown"),
        "scanned_at": scanned_at,
        "elapsed_ms": int(unified.get("elapsed_ms", 0)),
        "errors": list(unified.get("errors") or []),
    }


def evidence_json(asset: Mapping[str, Any]) -> str:
    return json.dumps({"sources": asset.get("sources") or []}, ensure_ascii=False, separators=(",", ":"))
