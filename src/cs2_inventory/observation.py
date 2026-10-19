"""扫描证据、身份与质量判定；不把缺失等同于移除。"""
from __future__ import annotations

import json
import re


def reliable_id(value):
    return bool(value) and not str(value).startswith("synthetic:")


def private_errors(errors):
    # 仅识别供应商错误位置的精确错误码，不匹配物品名称/普通 403。
    for error in errors:
        if not str(error).startswith("steamwebapi:"):
            continue
        if re.search(r':\s*PRIVATE\s*$', error):
            return True
        match = re.search(r'HTTP\s+\d+:\s*(\{.*\})\s*$', error)
        if match:
            try:
                if json.loads(match[1]).get("error") == "PRIVATE":
                    return True
            except (ValueError, AttributeError):
                pass
    return False


def asset_evidence(asset):
    return {key: asset.get(key) for key in (
        "sources", "observation", "market_name", "item_type",
    ) if asset.get(key) is not None}


def row_evidence(row):
    try:
        return json.loads(row.evidence_json or "{}")
    except ValueError:
        return {}


def evidence_observation(evidence):
    if evidence.get("observation"):
        return evidence["observation"]
    sources = evidence.get("sources") or []
    if any(str(s).startswith("steam_public_contextid2") for s in sources):
        return "live"
    if any("cache" in str(s).lower() or "observed" in str(s).lower() for s in sources):
        return "cached"
    return "unknown"


def scan_quality(result):
    errors = result.get("errors") or []
    if private_errors(errors):
        return "private"
    coverage = (result.get("coverage") or {}).get("status", "unknown")
    if coverage == "failed":
        return "failed"
    # 旧内部夹具的完整标记仍兼容；实际引擎提供 quality 证据。
    quality = result.get("quality")
    if quality is None:
        return "ready" if coverage in {"ok", "complete"} and not errors else "partial"
    return "ready" if quality.get("comparable") and not errors else "partial"


def signal_kind(market_name, item_type):
    """使用上游 Type 分类；未知分类不猜测为交易品。"""
    kind = str(item_type or "").lower()
    excluded = ("collectible", "medal", "badge", "coin", "收藏品", "勋章", "徽章", "硬币")
    if any(word in kind for word in excluded):
        return "excluded"
    included = ("rifle", "pistol", "sniper", "smg", "shotgun", "machinegun", "knife", "gloves",
                "sticker", "container", "weaponcase", "hands", "handwear", "spray", "customplayer",
                "key", "graffiti", "agent", "music", "patch", "charm",
                "步枪", "手枪", "狙击", "冲锋", "霰弹", "机枪", "匕首", "手套", "印花",
                "武器箱", "容器", "钥匙", "涂鸦", "探员", "音乐", "布章", "挂件")
    if market_name and any(word in kind for word in included):
        return "eligible"
    return "unknown"
