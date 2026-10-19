"""扫描证据、身份与质量判定；不把缺失等同于移除。"""
from __future__ import annotations

import json
import re

QUALITY_MESSAGES = {
    "steam_network": "Steam 网络连接失败，本轮未完成公开库存校验",
    "steam_rate_limit": "Steam 请求限流，本轮未完成公开库存校验",
    "steam_unavailable": "Steam 公开库存校验未成功",
    "provider_no_items": "供应商未返回物品，不等同于确认空库存",
    "provider_error": "供应商部分请求失败",
    "pagination": "库存分页未完成",
    "total_unknown": "尚未取得可核对的库存总数",
    "count_mismatch": "已观测物品与官方总数未对齐",
    "count_shortfall": "已观测物品仍少于上游声明数量，部分物品尚未取回",
    "cached_items": "包含历史缓存物品，本轮是否仍在库存尚待确认",
    "freshness_unknown": "部分物品的实时性尚未确认",
    "identity_unknown": "部分物品缺少可靠资产标识",
    "unverified_items": "部分保护物品证据尚未核实",
    "legacy_evidence": "历史记录缺少完整性证据，等待后续正常扫描建立可信基线",
    "baseline_missing": "缺少前一天早间深度基线，本日仅建立基线",
    "baseline_incomplete": "前一天的早间基线尚未通过完整性校验",
    "private": "已私密，保留历史结果，不产生新的增减结论",
    "failed": "本轮扫描失败，保留上次有效库存",
    "pending": "早间扫描尚未结束",
    "missing": "该日没有早间深度扫描记录",
}


def error_reason_codes(errors):
    codes = []
    for error in errors:
        text = str(error)
        if "分页" in text:
            code = "pagination"
        elif "官方公开库存" in text:
            if "429" in text:
                code = "steam_rate_limit"
            elif any(word in text.lower() for word in ("network", "timed out", "网络", "代理", "超时")):
                code = "steam_network"
            else:
                code = "steam_unavailable"
        elif "NO_ITEMS" in text:
            code = "provider_no_items"
        elif private_errors([text]):
            code = "private"
        else:
            code = "provider_error"
        if code not in codes:
            codes.append(code)
    return codes


def quality_messages(codes):
    return [QUALITY_MESSAGES[c] for c in dict.fromkeys(codes) if c in QUALITY_MESSAGES]


def snapshot_quality(snapshot):
    """读取已保存的校验证据；旧快照只诊断，不事后升级为完整。"""
    import gzip

    try:
        payload = json.loads(gzip.decompress(snapshot.payload_gzip))
        quality = payload.get("quality")
        if isinstance(quality, dict) and "comparable" in quality:
            return quality
    except (OSError, ValueError, TypeError):
        pass
    codes = error_reason_codes(json.loads(snapshot.errors_json or "[]"))
    complete = snapshot.coverage in {"ok", "complete"} and not codes
    if not complete:
        codes.append("legacy_evidence")
    return {"comparable": complete, "reason_codes": list(dict.fromkeys(codes)), "historical": not complete}


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
    # 旧内部夹具的完整标记仍兼容；实际引擎提供 quality 证据。
    quality = result.get("quality")
    if quality is None:
        if coverage == "failed":
            return "failed"
        return "ready" if coverage in {"ok", "complete"} and not errors else "partial"
    supplemental_empty = set(error_reason_codes(errors)) == {"provider_no_items"} and quality.get("warnings") == ["provider_no_items"]
    if quality.get("comparable") and (not errors or supplemental_empty):
        return "ready"
    return "failed" if coverage == "failed" else "partial"


def signal_kind(market_name, item_type):
    """使用上游 Type 分类；未知分类不猜测为交易品。"""
    kind = str(item_type or "").lower()
    excluded = ("collectible", "medal", "badge", "coin", "收藏品", "勋章", "徽章", "硬币")
    if any(word in kind for word in excluded):
        return "excluded"
    # 保护物品可能没有 Steam tags；使用供应商明确的武器类型，而非物品显示名猜测。
    weapons = {"ak-47", "aug", "awp", "bizon", "pp-bizon", "cz75-auto", "desert eagle",
               "dual berettas", "famas", "five-seven", "g3sg1", "galil ar", "glock-18",
               "m249", "m4a1-s", "m4a4", "mac-10", "mag-7", "mp5-sd", "mp7", "mp9",
               "negev", "nova", "p2000", "p250", "p90", "r8 revolver", "sawed-off",
               "scar-20", "sg 553", "ssg 08", "tec-9", "ump-45", "usp-s", "xm1014"}
    if market_name and kind in weapons:
        return "eligible"
    included = ("rifle", "pistol", "sniper", "smg", "shotgun", "machinegun", "knife", "gloves",
                "sticker", "container", "weaponcase", "hands", "handwear", "spray", "customplayer",
                "key", "graffiti", "agent", "music", "patch", "charm",
                "步枪", "手枪", "狙击", "冲锋", "霰弹", "机枪", "匕首", "手套", "印花",
                "武器箱", "容器", "钥匙", "涂鸦", "探员", "音乐", "布章", "挂件")
    if market_name and any(word in kind for word in included):
        return "eligible"
    return "unknown"
