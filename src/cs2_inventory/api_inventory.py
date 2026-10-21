"""生产库存仅采信同轮、完整分页的实时 API 分区；历史只参与对比。"""
from __future__ import annotations

import collections
import dataclasses
import json
import re
import time
from datetime import datetime, timezone
from typing import Mapping

from . import inventory_engine as engine
from .steam_visible import fetch_visible_inventory

SOURCE_POLICY = "api_preferred_v2"
API_POLICIES = {"api_only_v1", SOURCE_POLICY}
SECTIONS = ("protected", "other")


def _explicit_empty(errors, section):
    if len(errors) != 1:
        return False
    match = re.search(r'HTTP\s+(410|411):\s*(\{.*\})\s*$', errors[0])
    if not match:
        return False
    try:
        code = json.loads(match[2]).get("error")
    except (ValueError, AttributeError):
        return False
    # 普通分区 NO_TRADEABLE/411 不证明包括不可交易物品的整库为空。
    return code == "NO_ITEMS" and (match[1] == "410" or section == "protected")


def _page_records(page, section):
    payload = page.payload
    if isinstance(payload, Mapping) and "success" in payload and payload["success"] in (False, 0, "0", "false"):
        raise ValueError("上游未确认本页成功")
    if section == "protected":
        if not isinstance(payload, list) and not (isinstance(payload, Mapping) and any(
            isinstance(payload.get(key), list) for key in ("items", "inventory", "data", "assets")
        )):
            raise ValueError("保护库存响应结构异常")
        raw = engine._iter_steamwebapi_items(payload)
        records = engine.asset_records_from_parsed_payload(payload, source=page.source)
        if len(raw) != len(records) or any(not isinstance(item, Mapping) for item in (payload if isinstance(payload, list) else raw)):
            raise ValueError("保护库存资产结构异常")
        # 严格 context16 本身是保护证据，不能靠 tradable=true 过滤它。
        records = [dataclasses.replace(row, tradeprotected=True) for row in records]
    else:
        if not isinstance(payload, Mapping) or not isinstance(payload.get("assets"), list):
            raise ValueError("普通库存响应结构异常")
        raw = payload["assets"]
        records = engine.asset_records_from_raw_payload(payload, source=page.source)
        # 元数据缺失不得悄悄丢资产、再将不完整结果标成成功。
        by_id = {row.assetid: row for row in records}
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError("普通库存资产结构异常")
            assetid = str(item.get("assetid") or item.get("id") or "")
            if str(item.get("appid", "730")) != "730" or str(item.get("contextid", "2")) != "2":
                raise ValueError("普通库存返回非 CS2 资产")
            if assetid not in by_id:
                records.append(engine.AssetRecord(assetid=assetid, classid=str(item.get("classid") or ""),
                    instanceid=str(item.get("instanceid") or "0"), name="未知物品", amount=engine._amount(item.get("amount")),
                    observation=engine._freshness(page.source), sources=(page.source,)))
        if len(raw) != len(records):
            raise ValueError("普通库存资产数量异常")
    if any(not row.assetid or row.assetid.startswith("synthetic:") for row in records):
        raise ValueError("库存缺少可靠资产标识")
    if any(row.observation != "live" for row in records) or "freshness=live" not in page.source:
        raise ValueError("接口返回缓存或缺少实时响应证据")
    return records


def _asset(row, sections):
    return {"asset_key": row.assetid, "name": row.name, "raw_name": row.raw_name or row.name,
            "classid": row.classid, "instanceid": row.instanceid, "amount": row.amount,
            "market_name": row.market_name, "item_type": row.item_type,
            "name_localized": bool(re.search(r"[\u4e00-\u9fff]", row.name)),
            "is_trade_protected": bool(row.tradeprotected), "observation": "live",
            "sources": list(row.sources), "api_contexts": sorted(sections)}


def run_api_inventory_query(steamid, *, key, language="schinese", timeout=120, round_id=None, on_progress=None):
    """保护分区成功立即回调；只有本次两分区都成功才得到完整库存。"""
    started = time.monotonic()
    contexts, results = {}, {}

    def build(final=False):
        all_rows = engine.merge_asset_records(results.values())
        membership = collections.defaultdict(set)
        for section, rows in results.items():
            for row in rows:
                membership[row.assetid].add(section)
        complete = all(contexts.get(section, {}).get("complete") and contexts[section].get("origin") == "steamwebapi" for section in SECTIONS)
        errors = [error for context in contexts.values() for error in context.get("errors", [])]
        private = bool(len(contexts) == 2 and all(context.get("private") for context in contexts.values()))
        status = "complete" if complete else "partial" if results else "private" if private else "failed"
        quality = {"source_policy": SOURCE_POLICY, "scan_round": str(round_id or ""), "api_contexts": {key: dict(value) for key, value in contexts.items()},
                   "comparable": complete, "reason_codes": [] if complete else ["api_refresh_incomplete"],
                   "live_count": sum(row.amount for row in all_rows), "cached_count": 0, "unknown_count": 0,
                   "final": final}
        return {"steamid": str(steamid), "_api_assets": [_asset(row, membership[row.assetid]) for row in all_rows],
                "quality": quality, "coverage": {"status": status}, "errors": errors,
                "sources": {section: {"provider_requests": context["provider_requests"]} for section, context in contexts.items()},
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    for section in SECTIONS:
        fetch = engine.fetch_steamwebapi_raw_inventory(steamid, key=key, mode="2",
            parse="1" if section == "protected" else "0", language=language,
            state="active", no_cache="1", trade_locked="1" if section == "protected" else "0",
            samples=1, timeout=timeout, label=f"steamwebapi:api_only:{section}")
        errors = list(fetch.errors)
        records = []
        declared = [count for count in fetch.total_inventory_counts if count is not None]
        terminal_empty = bool(section == "other" and fetch.pages and len(errors) == 1
                              and "HTTP 411:" in errors[0] and _explicit_empty(errors, "protected"))
        empty = _explicit_empty(errors, section) and not fetch.pages
        if empty:
            errors = []
        elif not errors or terminal_empty:
            try:
                if not fetch.pages:
                    raise ValueError("接口未返回库存页面")
                for page in fetch.pages:
                    records.extend(_page_records(page, section))
                records = engine.merge_asset_records([records])
                if declared and sum(row.amount for row in records) < max(declared):
                    raise ValueError("本分区资产少于 API 页面声明数量")
                if terminal_empty:
                    if not declared or sum(row.amount for row in records) != max(declared):
                        raise ValueError("末页空响应缺少本分区数量完成证据")
                    errors = []
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(f"steamwebapi:api_only:{section}: {exc}")
        successful = not errors
        contexts[section] = {"complete": successful, "scanned_at": datetime.now(timezone.utc).isoformat(),
                             "provider_requests": fetch.request_attempts, "errors": errors,
                             "pages": len(fetch.pages), "returned_assets": sum(row.amount for row in records),
                             "declared_total": max(declared) if declared else None,
                             "terminal_empty_verified": bool(terminal_empty and successful),
                             "empty": successful and not records,
                             "origin": "steamwebapi", "coverage_scope": section,
                             "page_complete": successful, "inventory_complete": successful,
                             "displayable": successful, "scan_round": str(round_id or ""),
                             "private": any(re.search(r'\"error\"\s*:\s*\"PRIVATE\"', error) or error.endswith(": PRIVATE") for error in errors)}
        if successful:
            results[section] = records
        if section == "protected" and successful and on_progress:
            on_progress(build())
    if not contexts["other"]["complete"]:
        if on_progress:
            on_progress(build())  # 公开兜底前先提交已发生的付费请求账目。
        rows, fallback = fetch_visible_inventory(steamid, language=language, timeout=min(timeout, 20))
        fallback["terminal_empty_verified"] = False
        fallback["scan_round"] = str(round_id or "")
        fallback["api_failure"] = dict(contexts["other"])
        # API 请求与错误仍保留在分区账本中；公开请求不计 API credits。
        fallback["provider_requests"] = contexts["other"]["provider_requests"]
        fallback["errors"] = contexts["other"]["errors"] + fallback["errors"]
        contexts["other"] = fallback
        if fallback["displayable"]:
            results["other"] = rows
    return build(final=True)
