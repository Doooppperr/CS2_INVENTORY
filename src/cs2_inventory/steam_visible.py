"""当轮公开 context 2 适配器：只展示可见资产，不建立完整比较基线。"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from . import inventory_engine as engine


def fetch_visible_inventory(steamid, *, language="schinese", timeout=20, max_pages=20):
    context = {"origin": "steam_public", "coverage_scope": "visible_only", "complete": False,
               "displayable": False, "page_complete": False, "inventory_complete": False,
               "scanned_at": None, "provider_requests": 0, "pages": 0, "errors": [],
               "private": False, "returned_assets": 0, "declared_total": None}
    rows, cursors, declared = [], set(), set()
    cursor = None
    try:
        for _ in range(max_pages):
            params = {"l": language, "count": 2000}
            if cursor:
                params["start_assetid"] = cursor
            payload = engine.http_get_json(engine.STEAM_INVENTORY_URL.format(
                steamid=steamid, appid=730, contextid=2), params, timeout=timeout)
            context["pages"] += 1
            if not isinstance(payload, dict) or payload.get("success") not in (True, 1, "1"):
                raise ValueError("Steam 未确认本页成功")
            assets = payload.get("assets")
            if not isinstance(assets, list):
                # Steam 成功的零库存可能省略 assets；必须有显式零总数。
                if assets is None and payload.get("total_inventory_count") == 0:
                    assets = []
                else:
                    raise ValueError("Steam 资产结构异常")
            if payload.get("total_inventory_count") is not None:
                count = int(payload["total_inventory_count"])
                if count < 0:
                    raise ValueError("Steam 数量异常")
                declared.add(count)
            parsed = {row.assetid: row for row in engine.asset_records_from_raw_payload(
                payload, source="steam:context2:freshness=live")}
            for asset in assets:
                if not isinstance(asset, dict):
                    raise ValueError("Steam 资产结构异常")
                assetid = str(asset.get("assetid") or "")
                if not assetid.isdigit() or str(asset.get("appid", 730)) != "730" or str(asset.get("contextid", 2)) != "2":
                    raise ValueError("Steam 资产标识异常")
                amount = int(asset.get("amount", 1))
                if amount < 1:
                    raise ValueError("Steam 资产数量异常")
                row = parsed.get(assetid) or engine.AssetRecord(assetid=assetid,
                    classid=str(asset.get("classid") or ""), instanceid=str(asset.get("instanceid") or "0"),
                    name="未知物品", amount=amount)
                rows.append(dataclasses.replace(row, tradeprotected=False, observation="live",
                    sources=("steam:context2:freshness=live",)))
            more = payload.get("more_items", False)
            if more not in (True, False, 0, 1, "0", "1"):
                raise ValueError("Steam 分页标记异常")
            if more in (False, 0, "0"):
                context["page_complete"] = True
                break
            cursor = str(payload.get("last_assetid") or "")
            if not cursor.isdigit() or cursor in cursors:
                raise ValueError("Steam 分页游标缺失或重复")
            cursors.add(cursor)
        if not context["page_complete"]:
            raise ValueError("Steam 分页未完成")
        rows = engine.merge_asset_records([rows])
        total = sum(row.amount for row in rows)
        expected = max(declared) if declared else None
        context.update(returned_assets=total, declared_total=expected)
        if not total and (expected != 0 or len(declared) != 1):
            raise ValueError("Steam 空结果缺少零库存证据")
        if expected is not None and total > expected:
            raise ValueError("Steam 返回数量超过声明数量")
        context.update(displayable=True, inventory_complete=(len(declared) == 1 and total == expected),
                       scanned_at=datetime.now(timezone.utc).isoformat())
        return rows, context
    except (engine.SteamQueryError, ValueError, TypeError, KeyError, OSError) as exc:
        context["errors"] = [f"steam:context2: {exc}"]
        context["private"] = "HTTP 403" in str(exc)
        return [], context
