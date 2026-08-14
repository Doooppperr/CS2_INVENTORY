#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CS2 Steam 库存查询：
1) 优先通过 Steamwebapi 第三方 Inventory API 查询“受交易保护”的 CS2 物品名称。
2) 如果未配置 Steamwebapi key，则退回 Steam 登录 Cookie 历史页 / Steam Web API / 公开库存接口。
3) 用户运行时可以只输入 SteamID64；第三方 key 写入环境变量或本地参数即可。
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as _dt
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Counter, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

APPID_CS2 = 730
CONTEXTID_CS2 = "2"
TRADE_PROTECTION_SECONDS = 7 * 24 * 60 * 60
OBSERVATION_TTL_SECONDS = 10 * 24 * 60 * 60
STEAM_INVENTORY_URL = "https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}"
STEAM_TRADE_HISTORY_URL = "https://api.steampowered.com/IEconService/GetTradeHistory/v1/"
STEAM_INVENTORY_HISTORY_URL = "https://steamcommunity.com/profiles/{steamid}/inventoryhistory/"
STEAMWEBAPI_INVENTORY_URL = "https://www.steamwebapi.com/steam/api/inventory"
DEFAULT_LANGUAGE = "schinese"
DEFAULT_USER_AGENT = "cs2-inventory-query/1.0 (+https://steamcommunity.com/)"
DEFAULT_STEAMWEBAPI_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "steamwebapi_key.txt")
DEFAULT_OBSERVATION_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_observations.json")


class SteamQueryError(RuntimeError):
    """Raised when Steam returns an error or an unexpected response."""


@dataclasses.dataclass(frozen=True)
class InventoryItem:
    assetid: str
    classid: str
    instanceid: str
    name: str
    amount: int = 1


@dataclasses.dataclass(frozen=True)
class ProtectedItem:
    name: str
    assetid: str
    tradeid: str
    received_at: int
    protected_until: int
    amount: int = 1


@dataclasses.dataclass(frozen=True)
class ThirdPartyInventoryResult:
    protected_items: List[ProtectedItem]
    visible_items: List[InventoryItem]
    raw_items_count: int


@dataclasses.dataclass(frozen=True)
class SteamwebapiFetchResult:
    payload: List[Mapping[str, Any]]
    realtime_verified: bool
    upstream_item_counts: List[int]
    source: str


@dataclasses.dataclass(frozen=True)
class RawInventoryPage:
    """One raw Steamwebapi inventory response page with provenance and diagnostics."""

    source: str
    payload: Mapping[str, Any]
    item_count: int
    total_inventory_count: Optional[int]
    last_assetid: str
    duration_ms: int
    error: str = ""


@dataclasses.dataclass(frozen=True)
class SteamwebapiRawFetchResult:
    pages: List[RawInventoryPage]
    upstream_item_counts: List[int]
    sources: List[str]
    total_inventory_counts: List[Optional[int]]
    errors: List[str]


@dataclasses.dataclass(frozen=True)
class AssetRecord:
    """A reliable CS2 inventory asset identified by assetid (never by name alone)."""

    assetid: str
    classid: str
    instanceid: str
    name: str
    amount: int = 1
    contextid: str = CONTEXTID_CS2
    appid: str = str(APPID_CS2)
    tradeprotected: bool = False
    tradelocked: bool = False
    protected_until: int = 0
    sources: Tuple[str, ...] = ()
    protection_state: str = ""


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _amount(value: Any) -> int:
    amount = _int(value, 1)
    return max(amount, 1)


def _timestamp_to_local_text(timestamp: int) -> str:
    if not timestamp:
        return "未知时间"
    return _dt.datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _name_from_description(description: Mapping[str, Any] | None) -> str:
    if not description:
        return "未知物品"
    for key in ("market_hash_name", "market_name", "name"):
        value = description.get(key)
        if value:
            return str(value)
    return "未知物品"


def _description_keys(obj: Mapping[str, Any], default_contextid: str = "") -> List[Tuple[str, str, str, str]]:
    appid = _string(obj.get("appid"))
    contextid = _string(obj.get("contextid"), default_contextid)
    classid = _string(obj.get("classid"))
    instanceid = _string(obj.get("instanceid"), "0") or "0"
    keys: List[Tuple[str, str, str, str]] = []
    if appid and contextid and classid:
        keys.append((appid, contextid, classid, instanceid))
    if appid and classid:
        keys.append((appid, "", classid, instanceid))
    if classid:
        keys.append(("", "", classid, instanceid))
    return keys


def build_description_map(descriptions: Iterable[Mapping[str, Any]], default_contextid: str = "") -> Dict[Tuple[str, str, str, str], Mapping[str, Any]]:
    result: Dict[Tuple[str, str, str, str], Mapping[str, Any]] = {}
    for description in descriptions:
        for key in _description_keys(description, default_contextid=default_contextid):
            result.setdefault(key, description)
    return result


def lookup_description(asset: Mapping[str, Any], description_map: Mapping[Tuple[str, str, str, str], Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for key in _description_keys(asset, default_contextid=CONTEXTID_CS2):
        if key in description_map:
            return description_map[key]
    return None


def _http_error_body(exc: urllib.error.HTTPError) -> str:
    """Read an HTTP error body once so it can drive both retries and reporting."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _retry_delay_seconds(retry_after: Any, body: str, attempt: int) -> float:
    """Prefer the API's JSON retryAfterSeconds, then the header, then backoff."""
    body_delay = 0
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, Mapping):
                body_delay = _int(payload.get("retryAfterSeconds"), 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    delay = body_delay or _int(retry_after, 0) or min(2 ** attempt, 8)
    return float(max(0, min(delay, 300)))


def http_get_json_with_headers(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    timeout: float = 20.0,
    retries: int = 3,
    user_agent: str = DEFAULT_USER_AGENT,
    cookie: str | None = None,
) -> Tuple[Any, Dict[str, str]]:
    """GET JSON with retry handling and return (payload, response headers).

    The headers carry pagination (`last_assetid`), cache and rate-limit metadata
    that the plain payload-only helper discards.
    """
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json,text/javascript,*/*;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(full_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SteamQueryError(f"Steam 返回的不是 JSON：{raw[:200]}") from exc
                return payload, {str(key).lower(): str(value) for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            body = _http_error_body(exc)
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise SteamQueryError(f"HTTP {exc.code}: {body[:300] or exc.reason}") from exc
            delay = _retry_delay_seconds(retry_after, body, attempt)
            time.sleep(delay)
        except OSError as exc:
            last_error = exc
            if attempt >= retries:
                raise SteamQueryError(f"网络请求失败：{getattr(exc, 'reason', None) or exc}") from exc
            time.sleep(min(2 ** attempt, 8))
    raise SteamQueryError(f"请求失败：{last_error}")


def http_get_json(url: str, params: Mapping[str, Any] | None = None, *, timeout: float = 20.0, retries: int = 3, user_agent: str = DEFAULT_USER_AGENT, cookie: str | None = None) -> Any:
    """GET JSON with small retry handling for transient Steam 429/5xx responses."""
    payload, _headers = http_get_json_with_headers(url, params, timeout=timeout, retries=retries, user_agent=user_agent, cookie=cookie)
    return payload

def http_get_text(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    cookie: str | None = None,
    timeout: float = 20.0,
    retries: int = 3,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Tuple[str, str]:
    """GET HTML/text and return (body, final_url). Cookie is optional for logged-in Steam pages."""
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full_url = f"{url}?{query}" if query else url
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(full_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace"), response.geturl()
        except urllib.error.HTTPError as exc:
            last_error = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            body = _http_error_body(exc)
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                raise SteamQueryError(f"HTTP {exc.code}: {body[:300] or exc.reason}") from exc
            delay = _retry_delay_seconds(retry_after, body, attempt)
            time.sleep(delay)
        except OSError as exc:
            last_error = exc
            if attempt >= retries:
                raise SteamQueryError(f"网络请求失败：{getattr(exc, 'reason', None) or exc}") from exc
            time.sleep(min(2 ** attempt, 8))
    raise SteamQueryError(f"请求失败：{last_error}")


def merge_inventory_pages(pages: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    assets: List[Mapping[str, Any]] = []
    descriptions: List[Mapping[str, Any]] = []
    seen_descriptions: set[Tuple[str, str, str, str]] = set()
    total_inventory_count = None
    for page in pages:
        assets.extend(page.get("assets") or [])
        for description in page.get("descriptions") or []:
            keys = _description_keys(description, default_contextid=CONTEXTID_CS2)
            primary = keys[0] if keys else ("", "", _string(description.get("classid")), _string(description.get("instanceid"), "0"))
            if primary in seen_descriptions:
                continue
            seen_descriptions.add(primary)
            descriptions.append(description)
        if page.get("total_inventory_count") is not None:
            total_inventory_count = page.get("total_inventory_count")
    return {"assets": assets, "descriptions": descriptions, "total_inventory_count": total_inventory_count}


def fetch_public_inventory(
    steamid: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    count: int = 2000,
    max_pages: int = 20,
    timeout: float = 20.0,
    cookie: str | None = None,
) -> Dict[str, Any]:
    """Fetch currently visible CS2 inventory items from Steam Community inventory endpoint."""
    pages: List[Mapping[str, Any]] = []
    start_assetid: str | None = None
    url = STEAM_INVENTORY_URL.format(steamid=steamid, appid=APPID_CS2, contextid=CONTEXTID_CS2)
    for _ in range(max_pages):
        params: Dict[str, Any] = {"l": language, "count": count}
        if start_assetid:
            params["start_assetid"] = start_assetid
        data = http_get_json(url, params, timeout=timeout, cookie=cookie)
        if data is None:
            raise SteamQueryError("Steam 返回 null；常见原因是请求过于频繁、库存不公开或临时限流。")
        if not isinstance(data, Mapping):
            raise SteamQueryError(f"Steam 库存响应格式异常：{type(data).__name__}")
        success = data.get("success")
        if success in (False, 0, "0"):
            raise SteamQueryError(f"Steam 库存查询未成功：{data}")
        pages.append(data)
        start_assetid = _string(data.get("last_assetid")) or None
        if not data.get("more_items") or not start_assetid:
            break
    return merge_inventory_pages(pages)


def inventory_items_from_payload(payload: Mapping[str, Any]) -> List[InventoryItem]:
    description_map = build_description_map(payload.get("descriptions") or [], default_contextid=CONTEXTID_CS2)
    items: List[InventoryItem] = []
    for asset in payload.get("assets") or []:
        if _string(asset.get("appid")) not in ("", str(APPID_CS2)):
            continue
        if _string(asset.get("contextid"), CONTEXTID_CS2) != CONTEXTID_CS2:
            continue
        description = lookup_description(asset, description_map)
        name = _name_from_description(description)
        items.append(
            InventoryItem(
                assetid=_string(asset.get("assetid") or asset.get("id")),
                classid=_string(asset.get("classid")),
                instanceid=_string(asset.get("instanceid"), "0") or "0",
                name=name,
                amount=_amount(asset.get("amount")),
            )
        )
    return items


def _iter_steamwebapi_items(payload: Any) -> List[Mapping[str, Any]]:
    """Normalize Steamwebapi inventory response variants into a flat item list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    candidate_keys = (
        "items",
        "inventory",
        "data",
        "result",
        "assets",
        "response",
    )
    for key in candidate_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = _iter_steamwebapi_items(value)
            if nested:
                return nested
    # Some grouped responses are dictionaries keyed by item name.
    if payload and all(isinstance(value, Mapping) for value in payload.values()):
        return [value for value in payload.values() if isinstance(value, Mapping)]
    return []


def _steamwebapi_item_name(item: Mapping[str, Any]) -> str:
    for key in ("markethashname", "market_hash_name", "marketHashName", "marketname", "market_name", "name"):
        value = item.get(key)
        if value:
            return str(value)
    return "未知物品"


def _steamwebapi_bool(item: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = item.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            if value.strip().lower() in {"1", "true", "yes", "y"}:
                return True
            if value.strip().lower() in {"0", "false", "no", "n", ""}:
                continue
    return False


def _steamwebapi_amount(item: Mapping[str, Any]) -> int:
    for key in ("count", "amount", "quantity", "qty"):
        if key in item:
            return _amount(item.get(key))
    return 1


def _steamwebapi_assetid(item: Mapping[str, Any]) -> str:
    for key in ("assetid", "asset_id", "id", "newassetid", "new_assetid"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _steamwebapi_protected_until(item: Mapping[str, Any]) -> int:
    for key in ("tradeprotecteduntiltimestamp", "tradeprotected_until_timestamp", "tradeblockuntil", "tradable_after_timestamp"):
        value = _int(item.get(key), 0)
        if value > 0:
            return value
    return 0


def steamwebapi_items_from_payload(payload: Any, *, now: int | None = None) -> ThirdPartyInventoryResult:
    """Split Steamwebapi Inventory API response into protected and non-protected item lists."""
    if now is None:
        now = int(time.time())
    protected_items: List[ProtectedItem] = []
    visible_items: List[InventoryItem] = []
    raw_items = _iter_steamwebapi_items(payload)
    for item in raw_items:
        name = _steamwebapi_item_name(item)
        amount = _steamwebapi_amount(item)
        assetid = _steamwebapi_assetid(item)
        is_protected = _steamwebapi_bool(item, "tradeprotected", "trade_protected")
        protected_until = _steamwebapi_protected_until(item)
        if protected_until > now:
            is_protected = True
        if is_protected:
            protected_items.append(
                ProtectedItem(
                    name=name,
                    assetid=assetid,
                    tradeid=_string(item.get("tradeid")),
                    received_at=0,
                    protected_until=protected_until,
                    amount=amount,
                )
            )
        else:
            visible_items.append(
                InventoryItem(
                    assetid=assetid,
                    classid=_string(item.get("classid") or item.get("class_id")),
                    instanceid=_string(item.get("instanceid") or item.get("instance_id"), "0") or "0",
                    name=name,
                    amount=amount,
                )
            )
    return ThirdPartyInventoryResult(protected_items=protected_items, visible_items=visible_items, raw_items_count=len(raw_items))


def _steamwebapi_error_text(payload: Any) -> str:
    """Return a non-empty message when a Steamwebapi response is an error envelope."""
    if not isinstance(payload, Mapping):
        return ""
    status = payload.get("status")
    try:
        numeric_status = int(status) if status is not None else None
    except (TypeError, ValueError):
        numeric_status = None
    if numeric_status is not None and numeric_status >= 400:
        return _string(payload.get("message") or payload.get("error") or f"HTTP {numeric_status}")
    if payload.get("error"):
        return _string(payload.get("error"))
    return ""


def _steamwebapi_raw_item_count(payload: Any, *, parse: str) -> int:
    """Item count of one raw Steamwebapi response, depending on parse mode."""
    if parse == "0":
        if isinstance(payload, Mapping):
            return len(payload.get("assets") or [])
        return 0
    return len(_iter_steamwebapi_items(payload))


def _raw_asset_contextid(asset: Mapping[str, Any]) -> str:
    contextid = _string(asset.get("contextid"), CONTEXTID_CS2)
    return contextid or CONTEXTID_CS2


def _raw_asset_appid(asset: Mapping[str, Any]) -> str:
    appid = _string(asset.get("appid"), str(APPID_CS2))
    return appid or str(APPID_CS2)


def asset_records_from_raw_payload(payload: Any, *, source: str) -> List[AssetRecord]:
    """Convert a parse=0 Steamwebapi response into strict CS2 asset records.

    Only appid=730/contextid=2 assets with a resolvable classid/instanceid and a
    description are accepted. Attachment/sticker metadata is never an asset here.
    """
    if not isinstance(payload, Mapping):
        return []
    assets = payload.get("assets") or []
    descriptions = payload.get("descriptions") or []
    description_map = build_description_map(descriptions, default_contextid=CONTEXTID_CS2)
    records: List[AssetRecord] = []
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        if _raw_asset_appid(asset) != str(APPID_CS2):
            continue
        if _raw_asset_contextid(asset) != CONTEXTID_CS2:
            continue
        description = lookup_description(asset, description_map)
        name = _name_from_description(description)
        assetid = _string(asset.get("assetid") or asset.get("id"))
        classid = _string(asset.get("classid"))
        instanceid = _string(asset.get("instanceid"), "0") or "0"
        if not assetid or not classid or name == "未知物品":
            continue
        records.append(
            AssetRecord(
                assetid=assetid,
                classid=classid,
                instanceid=instanceid,
                name=name,
                amount=_amount(asset.get("amount")),
                contextid=CONTEXTID_CS2,
                appid=str(APPID_CS2),
                sources=(source,),
            )
        )
    return records


def asset_records_from_parsed_payload(payload: Any, *, source: str) -> List[AssetRecord]:
    """Convert a parse=1 Steamwebapi item list into AssetRecord rows.

    parse=1 is untrusted for asset identity: sticker/attachment metadata can appear
    as independent rows, so callers must cross-check against the official public
    inventory before treating any row as protected.
    """
    records: List[AssetRecord] = []
    for item in _iter_steamwebapi_items(payload):
        assetid = _steamwebapi_assetid(item)
        classid = _string(item.get("classid") or item.get("class_id"))
        instanceid = _string(item.get("instanceid") or item.get("instance_id"), "0") or "0"
        records.append(
            AssetRecord(
                assetid=assetid,
                classid=classid,
                instanceid=instanceid,
                name=_steamwebapi_item_name(item),
                amount=_steamwebapi_amount(item),
                contextid=CONTEXTID_CS2,
                appid=str(APPID_CS2),
                tradeprotected=_steamwebapi_bool(item, "tradeprotected", "trade_protected"),
                tradelocked=_steamwebapi_bool(item, "tradelocked", "trade_locked"),
                protected_until=_steamwebapi_protected_until(item),
                sources=(source,),
            )
        )
    return records


def asset_records_from_public_payload(payload: Mapping[str, Any], *, source: str) -> List[AssetRecord]:
    """Convert the official Steam public inventory payload into AssetRecords."""
    description_map = build_description_map(payload.get("descriptions") or [], default_contextid=CONTEXTID_CS2)
    records: List[AssetRecord] = []
    for asset in payload.get("assets") or []:
        if not isinstance(asset, Mapping):
            continue
        if _string(asset.get("appid")) not in ("", str(APPID_CS2)):
            continue
        if _string(asset.get("contextid"), CONTEXTID_CS2) != CONTEXTID_CS2:
            continue
        description = lookup_description(asset, description_map)
        assetid = _string(asset.get("assetid") or asset.get("id"))
        classid = _string(asset.get("classid"))
        instanceid = _string(asset.get("instanceid"), "0") or "0"
        if not assetid:
            continue
        records.append(
            AssetRecord(
                assetid=assetid,
                classid=classid,
                instanceid=instanceid,
                name=_name_from_description(description),
                amount=_amount(asset.get("amount")),
                contextid=CONTEXTID_CS2,
                appid=str(APPID_CS2),
                sources=(source,),
            )
        )
    return records


def merge_asset_records(record_lists: Iterable[Iterable[AssetRecord]]) -> List[AssetRecord]:
    """Union snapshots strictly by assetid; later rows refresh metadata and sources.

    This intentionally never merges by name: two distinct assetids remain distinct
    until the final display step groups them for the user.
    """
    by_assetid: Dict[str, AssetRecord] = {}
    for records in record_lists:
        for record in records:
            if not record.assetid:
                continue
            previous = by_assetid.get(record.assetid)
            if previous is None:
                by_assetid[record.assetid] = record
                continue
            merged_sources = tuple(sorted(set(previous.sources) | set(record.sources)))
            by_assetid[record.assetid] = AssetRecord(
                assetid=record.assetid,
                classid=record.classid or previous.classid,
                instanceid=record.instanceid or previous.instanceid,
                name=record.name or previous.name,
                amount=max(record.amount, previous.amount),
                contextid=record.contextid or previous.contextid,
                appid=record.appid or previous.appid,
                tradeprotected=previous.tradeprotected or record.tradeprotected,
                tradelocked=previous.tradelocked or record.tradelocked,
                protected_until=max(previous.protected_until, record.protected_until),
                sources=merged_sources,
            )
    return sorted(by_assetid.values(), key=lambda record: (record.name, record.assetid))


def _last_assetid_from(payload: Any, headers: Mapping[str, str]) -> str:
    if isinstance(payload, Mapping):
        body_value = _string(payload.get("last_assetid"))
        if body_value:
            return body_value
    for key in ("last_assetid", "last-assetid"):
        value = headers.get(key)
        if value:
            return str(value)
    return ""


def _rate_limit_sleep(headers: Mapping[str, str], *, max_sleep: float = 65.0) -> None:
    """Respect the Steamwebapi per-endpoint rate window before the next request."""
    try:
        remaining = int(headers.get("x-ratelimit-remaining", "999"))
    except ValueError:
        remaining = 999
    if remaining > 2:
        return
    retry_after = _int(headers.get("x-ratelimit-retry-after"), 0)
    if retry_after > 0:
        delay = max(0.0, min(retry_after - time.time(), max_sleep))
        if delay > 0:
            time.sleep(delay)


def fetch_steamwebapi_raw_inventory(
    steamid: str,
    *,
    key: str,
    mode: str = "2",
    parse: str = "0",
    language: str = DEFAULT_LANGUAGE,
    state: str = "active",
    no_cache: str = "1",
    limit: int = 2000,
    samples: int = 1,
    timeout: float = 60.0,
    trade_url: str | None = None,
    steam_login_secure: str | None = None,
    trade_locked: str = "0",
    rate_gap: float = 1.2,
    max_pages_per_sample: int = 10,
    label: str | None = None,
) -> SteamwebapiRawFetchResult:
    """Fetch raw Steamwebapi inventory samples with pagination and provenance.

    parse=0 returns Steam's assets/descriptions response; parse=1 returns the
    enriched item list. `last_assetid` (body or response header) is followed until
    the full inventory is consumed.
    """
    if not key:
        raise SteamQueryError("缺少 Steamwebapi key。")
    source_label = label or f"steamwebapi:parse={parse}:mode={mode}"
    params: Dict[str, Any] = {
        "key": key,
        "steam_id": steamid,
        "game": "cs2",
        "language": language,
        "parse": parse,
        "state": state,
        "limit": limit,
        "no_cache": no_cache,
        "group": "0",
        "with_no_tradable": "1",
        "offset": "0",
        "production": "0",
        "try_first_seven_days_blocked_items": mode,
    }
    if steam_login_secure:
        params["steam_login_secure"] = steam_login_secure
        params["trade_locked"] = trade_locked
    if trade_url:
        params["trade_url"] = trade_url

    pages: List[RawInventoryPage] = []
    errors: List[str] = []
    for sample_index in range(max(1, samples)):
        start_assetid = ""
        for page_index in range(max_pages_per_sample):
            request_params = dict(params)
            if start_assetid:
                request_params["start_assetid"] = start_assetid
            page_label = f"{source_label}:sample={sample_index + 1}:page={page_index + 1}"
            started = time.monotonic()
            try:
                payload, headers = http_get_json_with_headers(
                    STEAMWEBAPI_INVENTORY_URL, request_params, timeout=timeout, retries=1
                )
            except SteamQueryError as exc:
                errors.append(f"{page_label}: {exc}")
                break
            duration_ms = int((time.monotonic() - started) * 1000)
            error_text = _steamwebapi_error_text(payload)
            if error_text:
                errors.append(f"{page_label}: {error_text}")
                pages.append(
                    RawInventoryPage(
                        source=page_label,
                        payload={},
                        item_count=0,
                        total_inventory_count=None,
                        last_assetid="",
                        duration_ms=duration_ms,
                        error=error_text,
                    )
                )
                break
            if not isinstance(payload, (Mapping, list)):
                errors.append(f"{page_label}: 响应格式异常 {type(payload).__name__}")
                break
            total_inventory_count: Optional[int] = None
            if isinstance(payload, Mapping) and payload.get("total_inventory_count") is not None:
                total_inventory_count = _int(payload.get("total_inventory_count"), 0) or None
            last_assetid = _last_assetid_from(payload, headers)
            pages.append(
                RawInventoryPage(
                    source=page_label,
                    payload=payload,
                    item_count=_steamwebapi_raw_item_count(payload, parse=parse),
                    total_inventory_count=total_inventory_count,
                    last_assetid=last_assetid,
                    duration_ms=duration_ms,
                )
            )
            _rate_limit_sleep(headers)
            if last_assetid and last_assetid != start_assetid:
                start_assetid = last_assetid
                time.sleep(max(0.0, rate_gap))
                continue
            break
        if sample_index + 1 < max(1, samples):
            time.sleep(max(0.0, rate_gap))
    return SteamwebapiRawFetchResult(
        pages=pages,
        upstream_item_counts=[page.item_count for page in pages],
        sources=[page.source for page in pages],
        total_inventory_counts=[page.total_inventory_count for page in pages],
        errors=errors,
    )


def _merge_two_asset_records(first: AssetRecord, second: AssetRecord) -> AssetRecord:
    """Merge two rows for the same assetid, refreshing metadata and OR-ing flags."""
    return AssetRecord(
        assetid=first.assetid,
        classid=first.classid or second.classid,
        instanceid=first.instanceid or second.instanceid,
        name=first.name or second.name,
        amount=max(first.amount, second.amount),
        contextid=first.contextid or second.contextid,
        appid=first.appid or second.appid,
        tradeprotected=first.tradeprotected or second.tradeprotected,
        tradelocked=first.tradelocked or second.tradelocked,
        protected_until=max(first.protected_until, second.protected_until),
        sources=tuple(sorted(set(first.sources) | set(second.sources))),
        protection_state=first.protection_state or second.protection_state,
    )


def classify_hidden_assets(
    trading_records: Sequence[AssetRecord],
    public_records: Sequence[AssetRecord],
    parsed_records: Sequence[AssetRecord] = (),
    *,
    now: int | None = None,
) -> Tuple[List[AssetRecord], List[AssetRecord], List[Dict[str, Any]]]:
    """Split public-missing assets by actual trade-protection evidence.

    Returns (trade_protected, public_missing_but_protection_ended, excluded).

    - trade_protected: tradeprotected/tradelocked/protected_until evidence is true.
    - public_missing: absent from the official public inventory but with no active
      protection evidence; these are still hidden by Steam's ~10-day visibility
      window after a recent trade (protection already ended).
    - excluded: sticker/attachment rows reusing a public assetid, or invalid rows.
    """
    if now is None:
        now = int(time.time())
    public_by_assetid = {record.assetid: record for record in public_records if record.assetid}
    parsed_by_assetid = {record.assetid: record for record in parsed_records if record.assetid}
    protected: Dict[str, AssetRecord] = {}
    public_missing: Dict[str, AssetRecord] = {}
    excluded: List[Dict[str, Any]] = []

    def _exclude(record: AssetRecord, reason: str) -> None:
        excluded.append(
            {
                "assetid": record.assetid,
                "name": record.name,
                "reason": reason,
                "source": record.sources[0] if record.sources else "",
            }
        )

    def _has_protection_evidence(record: AssetRecord) -> bool:
        return bool(
            record.tradeprotected
            or record.tradelocked
            or (record.protected_until > 0 and record.protected_until > now)
        )

    for record in trading_records:
        if not record.assetid:
            _exclude(record, "缺少 assetid")
            continue
        public_match = public_by_assetid.get(record.assetid)
        if public_match is not None:
            same_identity = (
                (not record.classid or not public_match.classid or record.classid == public_match.classid)
                and (not record.instanceid or not public_match.instanceid or record.instanceid == public_match.instanceid)
            )
            if same_identity:
                continue
            _exclude(record, "assetid 出现在官方公开库存但 classid/instanceid 不一致（附着贴纸/描述元数据假阳性）")
            continue
        if not record.classid or not record.instanceid or record.name in ("", "未知物品"):
            _exclude(record, "assetid/classid/instanceid/description 关系无效")
            continue

        parsed = parsed_by_assetid.get(record.assetid)
        merged = _merge_two_asset_records(record, parsed) if parsed is not None else record
        if _has_protection_evidence(merged):
            protected[record.assetid] = AssetRecord(
                assetid=merged.assetid,
                classid=merged.classid,
                instanceid=merged.instanceid,
                name=merged.name,
                amount=merged.amount,
                contextid=merged.contextid,
                appid=merged.appid,
                tradeprotected=merged.tradeprotected,
                tradelocked=merged.tradelocked,
                protected_until=merged.protected_until,
                sources=merged.sources,
                protection_state="active",
            )
        else:
            public_missing[record.assetid] = AssetRecord(
                assetid=merged.assetid,
                classid=merged.classid,
                instanceid=merged.instanceid,
                name=merged.name,
                amount=merged.amount,
                contextid=merged.contextid,
                appid=merged.appid,
                tradeprotected=False,
                tradelocked=False,
                protected_until=0,
                sources=merged.sources,
                protection_state="ended" if parsed is not None else "unknown",
            )

    for record in parsed_records:
        if not _has_protection_evidence(record):
            continue
        if not record.assetid:
            _exclude(record, "缺少 assetid")
            continue
        public_match = public_by_assetid.get(record.assetid)
        if public_match is not None:
            _exclude(record, "parse=1 的受保护标记指向公开库存中已存在的 assetid（附着贴纸假阳性）")
            continue
        if record.assetid in protected:
            protected[record.assetid] = AssetRecord(
                assetid=record.assetid,
                classid=record.classid,
                instanceid=record.instanceid,
                name=record.name,
                amount=record.amount,
                contextid=record.contextid,
                appid=record.appid,
                tradeprotected=record.tradeprotected,
                tradelocked=record.tradelocked,
                protected_until=record.protected_until,
                sources=record.sources,
                protection_state="active",
            )
            continue
        if record.assetid in public_missing:
            # parse=1 upgraded the evidence from ended to active protection.
            moved = public_missing.pop(record.assetid)
            protected[record.assetid] = _merge_two_asset_records(moved, record)
            protected[record.assetid] = AssetRecord(
                assetid=record.assetid,
                classid=record.classid or moved.classid,
                instanceid=record.instanceid or moved.instanceid,
                name=record.name or moved.name,
                amount=max(record.amount, moved.amount),
                contextid=record.contextid,
                appid=record.appid,
                tradeprotected=True,
                tradelocked=record.tradelocked,
                protected_until=record.protected_until,
                sources=tuple(sorted(set(record.sources) | set(moved.sources))),
                protection_state="active",
            )
            continue
        if not record.classid or record.name in ("", "未知物品"):
            _exclude(record, "assetid/classid/description 关系无效")
            continue
        protected[record.assetid] = AssetRecord(
            assetid=record.assetid,
            classid=record.classid,
            instanceid=record.instanceid,
            name=record.name,
            amount=record.amount,
            contextid=record.contextid,
            appid=record.appid,
            tradeprotected=record.tradeprotected,
            tradelocked=record.tradelocked,
            protected_until=record.protected_until,
            sources=record.sources,
            protection_state="active",
        )

    protected_list = sorted(protected.values(), key=lambda record: (record.name, record.assetid))
    public_missing_list = sorted(public_missing.values(), key=lambda record: (record.name, record.assetid))
    return protected_list, public_missing_list, excluded


def classify_protected_assets(
    trading_records: Sequence[AssetRecord],
    public_records: Sequence[AssetRecord],
    parsed_records: Sequence[AssetRecord] = (),
) -> Tuple[List[AssetRecord], List[Dict[str, Any]]]:
    """Backward-compatible wrapper: returns protected + public-missing together.

    New callers should use classify_hidden_assets to keep trade protection separate
    from the ~10-day public-visibility window.
    """
    protected, public_missing, excluded = classify_hidden_assets(
        trading_records, public_records, parsed_records
    )
    return list(protected) + list(public_missing), excluded


def classify_owner_view_hidden(
    owner_records: Sequence[AssetRecord],
    public_records: Sequence[AssetRecord],
    owner_tradable_by_assetid: Mapping[str, bool],
    trading_records: Sequence[AssetRecord] = (),
    parsed_records: Sequence[AssetRecord] = (),
) -> Tuple[List[AssetRecord], List[AssetRecord]]:
    """Authoritative hidden-set split from an owner-authenticated inventory view.

    The owner view returns every CS2 asset; the description's tradable flag decides
    whether a public-missing asset is still trade-locked (active protection) or has
    ended protection and is only hidden by the ~10-day visibility window.
    """
    public_ids = {record.assetid for record in public_records if record.assetid}
    trading_by_assetid = {record.assetid: record for record in trading_records if record.assetid}
    parsed_by_assetid = {record.assetid: record for record in parsed_records if record.assetid}
    protected: List[AssetRecord] = []
    public_missing: List[AssetRecord] = []
    for record in owner_records:
        if record.assetid in public_ids:
            continue
        tradable = bool(owner_tradable_by_assetid.get(record.assetid, True))
        sources = set(record.sources)
        for source_map in (trading_by_assetid, parsed_by_assetid):
            if record.assetid in source_map:
                sources.update(source_map[record.assetid].sources)
        state = "ended" if tradable else "active"
        classified = AssetRecord(
            assetid=record.assetid,
            classid=record.classid,
            instanceid=record.instanceid,
            name=record.name,
            amount=record.amount,
            contextid=record.contextid,
            appid=record.appid,
            tradeprotected=not tradable,
            tradelocked=not tradable,
            protected_until=0,
            sources=tuple(sorted(sources)),
            protection_state=state,
        )
        (protected if state == "active" else public_missing).append(classified)
    protected.sort(key=lambda record: (record.name, record.assetid))
    public_missing.sort(key=lambda record: (record.name, record.assetid))
    return protected, public_missing


def apply_hidden_budget(
    protected_candidates: Sequence[AssetRecord],
    public_missing_candidates: Sequence[AssetRecord],
    *,
    trading_assetids: Set[str] | Sequence[str],
    hidden_budget: int,
) -> Tuple[List[AssetRecord], List[AssetRecord], List[Dict[str, Any]], List[AssetRecord]]:
    """Use Steam's official total_inventory_count as an upper bound, without deleting.

    parse=1 can return trade-protection rows that cannot be reconciled with the
    official count. Rows corroborated by the parse=0 trading inventory are trusted.
    parse=1-only rows that fit inside the remaining hidden budget are accepted;
    rows that do not fit are NOT deleted but returned separately as "unverified"
    so the caller can show them with the conflicting evidence.
    """
    trading_set = set(trading_assetids)
    candidates = list(protected_candidates) + list(public_missing_candidates)
    corroborated = [record for record in candidates if record.assetid in trading_set]
    parse1_only = [record for record in candidates if record.assetid not in trading_set]
    remaining = max(0, hidden_budget - len(corroborated))
    excluded: List[Dict[str, Any]] = []
    unverified: List[AssetRecord] = []
    if len(parse1_only) > remaining:
        protected_out = [record for record in protected_candidates if record.assetid in trading_set]
        public_missing_out = [record for record in public_missing_candidates if record.assetid in trading_set]
        unverified = list(parse1_only)
        return protected_out, public_missing_out, excluded, unverified
    return list(protected_candidates), list(public_missing_candidates), excluded, unverified


def _read_observation_cache(path: Optional[str]) -> Dict[str, Any]:
    cache_path = path or DEFAULT_OBSERVATION_CACHE_FILE
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_observation_cache(path: Optional[str], data: Dict[str, Any]) -> None:
    cache_path = path or DEFAULT_OBSERVATION_CACHE_FILE
    directory = os.path.dirname(os.path.abspath(cache_path)) or "."
    os.makedirs(directory, exist_ok=True)
    import tempfile

    fd, temporary = tempfile.mkstemp(prefix=os.path.basename(cache_path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, cache_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


_OBSERVATION_CACHE_LOCK = threading.RLock()


def _apply_observation_cache_unlocked(
    steamid: str,
    live_protected: Sequence[AssetRecord],
    public_assetids: Set[str] | Sequence[str],
    *,
    cache_path: Optional[str] = None,
    now: int | None = None,
    ttl_seconds: int = OBSERVATION_TTL_SECONDS,
) -> Tuple[List[AssetRecord], List[AssetRecord]]:
    """Persist protected assetids for 10 days and split live vs recent observations.

    A cached assetid is dropped immediately when Steam's public inventory exposes
    it, and otherwise expires after the observation TTL. The returned pair keeps
    "本次实时命中" separate from "近期观测补全".
    """
    if now is None:
        now = int(time.time())
    public_set = set(public_assetids)
    live_by_assetid = {record.assetid: record for record in live_protected if record.assetid}
    cache = _read_observation_cache(cache_path)
    account = cache.get(steamid)
    if not isinstance(account, dict):
        account = {}

    for assetid in list(account):
        row = account.get(assetid)
        if (
            not isinstance(row, dict)
            or assetid in public_set
            or _int(row.get("expires_at"), 0) <= now
        ):
            account.pop(assetid, None)

    for assetid, record in live_by_assetid.items():
        previous = account.get(assetid) if isinstance(account.get(assetid), dict) else {}
        protected_until = int(getattr(record, "protected_until", 0) or 0)
        expires_at = max(now + ttl_seconds, protected_until + 12 * 3600)
        protection_state = getattr(record, "protection_state", "") or (
            "active"
            if (
                bool(getattr(record, "tradeprotected", False))
                or bool(getattr(record, "tradelocked", False))
                or protected_until > now
            )
            else "unknown"
        )
        previous_sources = previous.get("sources")
        previous_sources_list = previous_sources if isinstance(previous_sources, list) else []
        account[assetid] = {
            "name": record.name,
            "amount": record.amount,
            "classid": record.classid,
            "instanceid": record.instanceid,
            "tradeprotected": bool(getattr(record, "tradeprotected", False)),
            "tradelocked": bool(getattr(record, "tradelocked", False)),
            "protected_until": protected_until,
            "protection_state": protection_state,
            "sources": sorted(set(previous_sources_list) | set(record.sources)),
            "first_seen": _int(previous.get("first_seen"), now) or now,
            "last_seen": now,
            "expires_at": expires_at,
        }

    cache[steamid] = account
    _write_observation_cache(cache_path, cache)

    observed: List[AssetRecord] = []
    for assetid, row in sorted(account.items()):
        if assetid in live_by_assetid:
            continue
        row_sources = row.get("sources")
        if isinstance(row_sources, list) and any(str(source).startswith("historical_seed") for source in row_sources):
            continue
        stored_protection_state = _string(row.get("protection_state"))
        if not stored_protection_state:
            stored_protection_state = (
                "active" if (bool(row.get("tradeprotected")) or bool(row.get("tradelocked"))) else "unknown"
            )
        observed.append(
            AssetRecord(
                assetid=assetid,
                classid=_string(row.get("classid")),
                instanceid=_string(row.get("instanceid"), "0") or "0",
                name=_string(row.get("name") or "未知物品"),
                amount=_amount(row.get("amount")),
                contextid=CONTEXTID_CS2,
                appid=str(APPID_CS2),
                tradeprotected=bool(row.get("tradeprotected")),
                tradelocked=bool(row.get("tradelocked")),
                protected_until=_int(row.get("protected_until"), 0),
                protection_state=stored_protection_state,
                sources=tuple(row_sources) if isinstance(row_sources, list) else ("observation_cache",),
            )
        )
    return list(live_by_assetid.values()), observed


def apply_observation_cache(
    steamid: str,
    live_protected: Sequence[AssetRecord],
    public_assetids: Set[str] | Sequence[str],
    *,
    cache_path: Optional[str] = None,
    now: int | None = None,
    ttl_seconds: int = OBSERVATION_TTL_SECONDS,
) -> Tuple[List[AssetRecord], List[AssetRecord]]:
    """Update the shared observation cache without losing concurrent queries."""
    with _OBSERVATION_CACHE_LOCK:
        return _apply_observation_cache_unlocked(
            steamid,
            live_protected,
            public_assetids,
            cache_path=cache_path,
            now=now,
            ttl_seconds=ttl_seconds,
        )


def seed_observation_cache(
    steamid: str,
    records: Sequence[AssetRecord],
    *,
    cache_path: Optional[str] = None,
    now: int | None = None,
    ttl_seconds: int = OBSERVATION_TTL_SECONDS,
) -> int:
    """Seed previously-observed hidden assets so they survive upstream omissions.

    Seeded rows carry protection_state="unknown" and a historical_seed source, so
    they are surfaced as hidden-gap candidates instead of being silently dropped
    when today's live endpoints cannot see them.
    """
    if now is None:
        now = int(time.time())
    cache = _read_observation_cache(cache_path)
    account = cache.get(steamid)
    if not isinstance(account, dict):
        account = {}
    seeded = 0
    for record in records:
        if not record.assetid:
            continue
        existing = account.get(record.assetid)
        if isinstance(existing, dict) and existing.get("sources"):
            continue
        account[record.assetid] = {
            "name": record.name,
            "amount": record.amount,
            "classid": record.classid,
            "instanceid": record.instanceid,
            "tradeprotected": False,
            "tradelocked": False,
            "protected_until": 0,
            "protection_state": "unknown",
            "sources": ["historical_seed"],
            "first_seen": now - 24 * 3600,
            "last_seen": now - 24 * 3600,
            "expires_at": now + ttl_seconds,
        }
        seeded += 1
    cache[steamid] = account
    _write_observation_cache(cache_path, cache)
    return seeded


def remove_public_visible_false_protected(
    protected_items: Sequence[ProtectedItem],
    public_visible_items: Sequence[InventoryItem],
) -> List[ProtectedItem]:
    """
    Steamwebapi can sometimes mark a public, non-tradable item as tradeprotected when
    parsing sticker/attached-item metadata. For this project, task 1 means items that
    the normal public inventory cannot directly return, so any same assetid already
    visible in Steam's public inventory is removed from the protected list.
    """
    public_assetids = {item.assetid for item in public_visible_items if item.assetid}
    if not public_assetids:
        return list(protected_items)
    return [item for item in protected_items if not item.assetid or item.assetid not in public_assetids]


def merge_public_missing_third_party_items(
    protected_items: Sequence[ProtectedItem],
    third_party_visible_items: Sequence[InventoryItem],
    public_visible_items: Sequence[InventoryItem],
) -> List[ProtectedItem]:
    """
    Task 1 is defined as items under trade protection / not returned by the normal
    public Steam inventory query. Steamwebapi may return some public-missing items
    with tradeprotected=false but owner-only/trading-inventory metadata. Include
    these public-missing assetids as task-1 candidates, while excluding false
    positives whose same assetid is already visible in the official public inventory.
    """
    public_assetids = {item.assetid for item in public_visible_items if item.assetid}
    by_assetid: Dict[str, ProtectedItem] = {}
    unnamed: List[ProtectedItem] = []

    for item in protected_items:
        if item.assetid and item.assetid in public_assetids:
            continue
        if item.assetid:
            by_assetid[item.assetid] = item
        else:
            unnamed.append(item)

    for item in third_party_visible_items:
        if not item.assetid or item.assetid in public_assetids or item.assetid in by_assetid:
            continue
        by_assetid[item.assetid] = ProtectedItem(
            name=item.name,
            assetid=item.assetid,
            tradeid="",
            received_at=0,
            protected_until=0,
            amount=item.amount,
        )

    merged = unnamed + list(by_assetid.values())
    merged.sort(key=lambda item: (item.name, item.assetid))
    return merged


def fetch_steamwebapi_inventory(
    steamid: str,
    *,
    key: str,
    language: str = DEFAULT_LANGUAGE,
    timeout: float = 60.0,
    mode: str = "2",
    state: str = "active",
    no_cache: str = "1",
    limit: int = 10000,
    samples: int = 2,
    trade_url: str | None = None,
) -> SteamwebapiFetchResult:
    """Fetch and union fresh CS2 trading-inventory samples from Steamwebapi."""
    if not key:
        raise SteamQueryError("缺少 Steamwebapi key。")
    params = {
        "key": key,
        "steam_id": steamid,
        "game": "cs2",
        "language": language,
        "parse": "1",
        "state": state,
        "limit": limit,
        "no_cache": no_cache,
        "group": "0",
        "with_no_tradable": "1",
        "offset": "0",
        "production": "0",
        "try_first_seven_days_blocked_items": mode,
    }
    if trade_url:
        params["trade_url"] = trade_url
    successful_payloads: List[Any] = []
    upstream_item_counts: List[int] = []
    errors: List[str] = []
    for sample_index in range(max(1, samples)):
        try:
            payload = http_get_json(STEAMWEBAPI_INVENTORY_URL, params, timeout=timeout, retries=1)
            successful_payloads.append(payload)
            upstream_item_counts.append(len(_iter_steamwebapi_items(payload)))
        except SteamQueryError as exc:
            errors.append(str(exc))
        if sample_index + 1 < max(1, samples):
            time.sleep(1.0)
    if not successful_payloads:
        raise SteamQueryError("；".join(errors) or "Steamwebapi 未返回库存。")

    merged: Dict[str, Mapping[str, Any]] = {}
    unnamed_index = 0
    for payload in successful_payloads:
        for item in _iter_steamwebapi_items(payload):
            assetid = _steamwebapi_assetid(item)
            key_name = f"asset:{assetid}" if assetid else f"unnamed:{_steamwebapi_item_name(item)}:{unnamed_index}"
            merged[key_name] = item
            if not assetid:
                unnamed_index += 1
    return SteamwebapiFetchResult(
        payload=list(merged.values()),
        realtime_verified=(state == "active" and no_cache == "1"),
        upstream_item_counts=upstream_item_counts,
        source="authenticated_trade_url" if trade_url else "steamid_trading_inventory",
    )


def read_cookie_file(path: str) -> str:
    """Read either a raw Cookie header file or a Netscape/curl cookie jar."""
    try:
        raw = open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise SteamQueryError(f"无法读取 Cookie 文件 {path}: {exc}") from exc
    stripped = raw.strip()
    if not stripped:
        return ""
    cookie_parts: List[str] = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and "steam" in fields[0].lower():
            name, value = fields[5], fields[6]
            if name and value:
                cookie_parts.append(f"{name}={value}")
    if cookie_parts:
        return "; ".join(cookie_parts)
    return stripped.replace("\r", "").replace("\n", "; ")


def build_cookie_header(
    *,
    steam_cookie: str | None = None,
    steam_cookie_file: str | None = None,
    steam_login_secure: str | None = None,
    sessionid: str | None = None,
) -> str:
    """Build a Cookie header for keyless authenticated Steam Community history pages."""
    parts: List[str] = []
    if steam_cookie_file:
        file_cookie = read_cookie_file(steam_cookie_file)
        if file_cookie:
            parts.append(file_cookie)
    if steam_cookie:
        parts.append(steam_cookie.strip())
    if steam_login_secure:
        parts.append(f"steamLoginSecure={steam_login_secure.strip()}")
    if sessionid:
        parts.append(f"sessionid={sessionid.strip()}")
    return "; ".join(part for part in parts if part)


def _extract_js_assignment_json(body: str, variable_name: str) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(variable_name)}\s*=\s*", body)
    if not match:
        raise SteamQueryError(f"历史页面缺少 {variable_name}。")
    index = match.end()
    while index < len(body) and body[index].isspace():
        index += 1
    if index >= len(body) or body[index] not in "{[":
        raise SteamQueryError(f"历史页面 {variable_name} 起始格式异常。")

    opener = body[index]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    quote = ""
    for pos in range(index, len(body)):
        char = body[pos]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in ("'", '"'):
            in_string = True
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                token = body[index : pos + 1]
                try:
                    return json.loads(token)
                except json.JSONDecodeError as exc:
                    raise SteamQueryError(f"历史页面 {variable_name} JSON 格式异常：{exc}") from exc
    raise SteamQueryError(f"历史页面 {variable_name} 结束格式异常。")


def _parse_history_time(date_text: str, time_text: str, *, default_year: int | None = None) -> int:
    date_text = " ".join(date_text.replace("\xa0", " ").split()).strip()
    time_text = " ".join(time_text.replace("\xa0", " ").split()).strip().lower().replace(" ", "")
    if not date_text or not time_text:
        return 0
    if default_year is None:
        default_year = _dt.datetime.now(_dt.timezone.utc).year
    candidates = [f"{date_text} {time_text}"]
    if not re.search(r"\b\d{4}\b", date_text):
        candidates.append(f"{date_text}, {default_year} {time_text}")
        candidates.append(f"{date_text} {default_year} {time_text}")
    formats = [
        "%d %b, %Y %I:%M%p",
        "%b %d, %Y %I:%M%p",
        "%d %B, %Y %I:%M%p",
        "%B %d, %Y %I:%M%p",
        "%d %b %Y %I:%M%p",
        "%b %d %Y %I:%M%p",
        "%Y-%m-%d %I:%M%p",
        "%Y/%m/%d %I:%M%p",
        "%d %b, %Y %H:%M",
        "%b %d, %Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    ]
    for candidate in candidates:
        for fmt in formats:
            try:
                parsed = _dt.datetime.strptime(candidate, fmt).replace(tzinfo=_dt.timezone.utc)
                return int(parsed.timestamp())
            except ValueError:
                continue
    return 0


def _lookup_history_inventory_item(history_inventory: Mapping[str, Any], appid: str, contextid: str, item_key: str) -> Mapping[str, Any]:
    try:
        item = history_inventory[str(appid)][str(contextid)][str(item_key)]
    except (KeyError, TypeError) as exc:
        raise SteamQueryError(f"历史页面物品索引缺失：appid={appid}, contextid={contextid}, key={item_key}") from exc
    if not isinstance(item, Mapping):
        raise SteamQueryError(f"历史页面物品格式异常：appid={appid}, contextid={contextid}, key={item_key}")
    return item


def parse_history_html_payload(body: str, *, page_kind: str = "tradehistory") -> Dict[str, Any]:
    """Parse a logged-in Steam Community tradehistory/inventoryhistory HTML page into GetTradeHistory-like payload."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as exc:
        raise SteamQueryError("解析 Steam 历史 HTML 需要 beautifulsoup4。") from exc

    history_inventory = _extract_js_assignment_json(body, "g_rgHistoryInventory")
    if not isinstance(history_inventory, Mapping):
        raise SteamQueryError("历史页面 g_rgHistoryInventory 格式异常。")
    soup = BeautifulSoup(body, "html.parser")
    rows = soup.select(".tradehistoryrow")
    if not rows:
        raise SteamQueryError("历史页面没有 tradehistoryrow。")

    hover_map: Dict[str, Tuple[str, str, str, str]] = {}
    hover_re = re.compile(
        r"HistoryPageCreateItemHover\(\s*'([^']+)'\s*,\s*(\d+)\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\);"
    )
    for match in hover_re.finditer(body):
        item_id, appid, contextid, item_key, amount = match.groups()
        hover_map[item_id] = (appid, contextid, item_key, amount)

    trades: List[Dict[str, Any]] = []
    descriptions: List[Mapping[str, Any]] = []
    for row_index, row in enumerate(rows):
        event_description = row.select_one(".tradehistory_event_description")
        event_text = event_description.get_text(" ", strip=True) if event_description else ""
        if page_kind == "inventoryhistory" and not re.search(r"\btrade|traded|交易|交换\b", event_text, re.IGNORECASE):
            continue

        date_node = row.select_one(".tradehistory_date")
        time_node = row.select_one(".tradehistory_timestamp")
        trade_time = _parse_history_time(
            date_node.get_text(" ", strip=True) if date_node else "",
            time_node.get_text(" ", strip=True) if time_node else "",
        )
        row_id = row.get("id") or f"{page_kind}_row_{row_index}"
        tradeid_match = re.search(r"(\d{8,})", str(row_id))
        tradeid = tradeid_match.group(1) if tradeid_match else str(row_id)

        assets_received: List[Dict[str, Any]] = []
        for item_node in row.select(".history_item"):
            item_id = item_node.get("id") or ""
            if "received" not in item_id.lower():
                continue
            hover = hover_map.get(item_id)
            if not hover:
                continue
            appid, contextid, item_key, amount = hover
            if str(appid) != str(APPID_CS2) or str(contextid) != CONTEXTID_CS2:
                continue
            econ_item = dict(_lookup_history_inventory_item(history_inventory, appid, contextid, item_key))
            econ_item.setdefault("appid", str(appid))
            econ_item.setdefault("contextid", str(contextid))
            econ_item.setdefault("classid", _string(econ_item.get("classid") or item_key))
            econ_item.setdefault("instanceid", _string(econ_item.get("instanceid"), "0") or "0")
            econ_item.setdefault("amount", amount)
            assetid = _string(econ_item.get("new_assetid") or econ_item.get("assetid") or econ_item.get("id") or item_key)
            econ_item["assetid"] = assetid
            econ_item["new_assetid"] = assetid
            assets_received.append(econ_item)
            descriptions.append(econ_item)

        if assets_received:
            trades.append(
                {
                    "tradeid": tradeid,
                    "time_init": trade_time,
                    "status": 3,
                    "assets_received": assets_received,
                }
            )
    return {"trades": trades, "descriptions": descriptions}


def _history_next_cursor(body: str) -> Tuple[int, int] | None:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return None
    soup = BeautifulSoup(body, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if "after_time=" not in href or "after_trade=" not in href or "prev=1" in href:
            continue
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        after_time = _int((query.get("after_time") or ["0"])[0], 0)
        after_trade = _int((query.get("after_trade") or ["0"])[0], 0)
        if after_time > 0 and after_trade > 0:
            return after_time, after_trade
    match = re.search(r"after_time=(\d+).*?after_trade=(\d+)", body)
    if match and "prev=1" not in match.group(0):
        return _int(match.group(1), 0), _int(match.group(2), 0)
    return None


def fetch_keyless_history_payloads(
    steamid: str,
    *,
    cookie: str,
    language: str = "english",
    page_kind: str = "auto",
    max_pages: int = 5,
    timeout: float = 20.0,
) -> List[Dict[str, Any]]:
    """Fetch authenticated Steam Community history pages without a Steam Web API key."""
    if not cookie:
        raise SteamQueryError("无 Key 模式需要 Steam 登录 Cookie（steamLoginSecure）。")
    kinds = ["tradehistory", "inventoryhistory"] if page_kind == "auto" else [page_kind]
    last_error: SteamQueryError | None = None
    for kind in kinds:
        payloads: List[Dict[str, Any]] = []
        after_time = 0
        after_trade = 0
        seen: set[Tuple[int, int]] = set()
        url = f"https://steamcommunity.com/profiles/{steamid}/{kind}/"
        try:
            for _ in range(max_pages):
                token = (after_time, after_trade)
                if token in seen:
                    break
                seen.add(token)
                params: Dict[str, Any] = {"l": language}
                if after_time:
                    params["after_time"] = after_time
                    params["after_trade"] = after_trade
                body, final_url = http_get_text(url, params, cookie=cookie, timeout=timeout)
                if "/login/" in final_url or "<title>Sign In</title>" in body:
                    raise SteamQueryError("Steam 登录 Cookie 未生效，历史页返回登录页面。")
                payload = parse_history_html_payload(body, page_kind=kind)
                payloads.append(payload)
                cursor = _history_next_cursor(body)
                if not cursor:
                    break
                after_time, after_trade = cursor
            if payloads:
                return payloads
        except SteamQueryError as exc:
            last_error = exc
            continue
    raise last_error or SteamQueryError("无 Key 历史页没有返回可解析数据。")


def fetch_trade_history(api_key: str, *, language: str = DEFAULT_LANGUAGE, max_trades: int = 500, max_pages: int = 5, timeout: float = 20.0) -> List[Dict[str, Any]]:
    """Fetch trade history pages for the Steam account owning api_key."""
    max_trades = max(1, min(max_trades, 500))
    pages: List[Dict[str, Any]] = []
    start_after_time = 0
    start_after_tradeid = 0
    seen_page_tokens: set[Tuple[int, int]] = set()
    for _ in range(max_pages):
        token = (start_after_time, start_after_tradeid)
        if token in seen_page_tokens:
            break
        seen_page_tokens.add(token)
        params = {
            "key": api_key,
            "max_trades": max_trades,
            "start_after_time": start_after_time,
            "start_after_tradeid": start_after_tradeid,
            "navigating_back": 0,
            "get_descriptions": 1,
            "language": language,
            "include_failed": 0,
            "include_total": 0,
        }
        data = http_get_json(STEAM_TRADE_HISTORY_URL, params, timeout=timeout)
        if not isinstance(data, Mapping):
            raise SteamQueryError(f"Steam 交易历史响应格式异常：{type(data).__name__}")
        response = data.get("response", data)
        if not isinstance(response, Mapping):
            raise SteamQueryError(f"Steam 交易历史 response 格式异常：{type(response).__name__}")
        page = dict(response)
        pages.append(page)
        trades = page.get("trades") or []
        if not trades or len(trades) < max_trades:
            break
        last_trade = trades[-1]
        if not isinstance(last_trade, Mapping):
            break
        next_time = _int(last_trade.get("time_init") or last_trade.get("time_completed") or last_trade.get("time"), 0)
        next_tradeid = _int(last_trade.get("tradeid"), 0)
        if next_time <= 0 or next_tradeid <= 0:
            break
        start_after_time = next_time
        start_after_tradeid = next_tradeid
    return pages


def _trade_is_successful(trade: Mapping[str, Any]) -> bool:
    status = str(trade.get("status", "")).lower()
    if status in {"failed", "cancelled", "canceled", "declined", "invalid", "rolledback", "rollback"}:
        return False
    if trade.get("failed") is True:
        return False
    return True


def protected_items_from_trade_payloads(payloads: Sequence[Mapping[str, Any]], *, now: int | None = None, protection_seconds: int = TRADE_PROTECTION_SECONDS) -> List[ProtectedItem]:
    """Return CS2 assets received in successful trades whose 7-day protection has not expired."""
    if now is None:
        now = int(time.time())
    cutoff = now - protection_seconds
    descriptions: List[Mapping[str, Any]] = []
    for payload in payloads:
        descriptions.extend(payload.get("descriptions") or [])
    description_map = build_description_map(descriptions, default_contextid=CONTEXTID_CS2)

    protected: List[ProtectedItem] = []
    for payload in payloads:
        for trade in payload.get("trades") or []:
            if not isinstance(trade, Mapping) or not _trade_is_successful(trade):
                continue
            trade_time = _int(trade.get("time_init") or trade.get("time_completed") or trade.get("time"), 0)
            if trade_time <= 0 or trade_time < cutoff:
                continue
            tradeid = _string(trade.get("tradeid"))
            for asset in trade.get("assets_received") or trade.get("received_assets") or []:
                if not isinstance(asset, Mapping):
                    continue
                if _string(asset.get("appid")) != str(APPID_CS2):
                    continue
                if _string(asset.get("contextid"), CONTEXTID_CS2) != CONTEXTID_CS2:
                    continue
                description = lookup_description(asset, description_map)
                name = _name_from_description(description)
                protected.append(
                    ProtectedItem(
                        name=name,
                        assetid=_string(asset.get("new_assetid") or asset.get("assetid") or asset.get("id")),
                        tradeid=tradeid,
                        received_at=trade_time,
                        protected_until=trade_time + protection_seconds,
                        amount=_amount(asset.get("amount")),
                    )
                )
    protected.sort(key=lambda item: (item.protected_until, item.name, item.assetid))
    return protected


def counter_by_name(items: Iterable[InventoryItem | ProtectedItem]) -> Counter[str]:
    counter: Counter[str] = collections.Counter()
    for item in items:
        counter[item.name] += item.amount
    return counter


def _format_counter(counter: Counter[str]) -> List[str]:
    if not counter:
        return ["（空）"]
    lines: List[str] = []
    for name in sorted(counter):
        count = counter[name]
        suffix = f" x{count}" if count > 1 else ""
        lines.append(f"- {name}{suffix}")
    return lines


def _format_protected_details(items: Sequence[ProtectedItem]) -> List[str]:
    if not items:
        return ["（空）"]
    lines: List[str] = []
    for item in items:
        suffix = f" x{item.amount}" if item.amount > 1 else ""
        trade = f"，tradeid={item.tradeid}" if item.tradeid else ""
        asset = f"，assetid={item.assetid}" if item.assetid else ""
        lines.append(
            f"- {item.name}{suffix}（收到：{_timestamp_to_local_text(item.received_at)}；保护至：{_timestamp_to_local_text(item.protected_until)}{trade}{asset}）"
        )
    return lines


def _format_inventory_details(items: Sequence[InventoryItem]) -> List[str]:
    if not items:
        return ["（空）"]
    lines: List[str] = []
    for item in sorted(items, key=lambda value: (value.name, value.assetid)):
        amount = f"，数量={item.amount}" if item.amount != 1 else ""
        asset = f"assetid={item.assetid}" if item.assetid else "assetid=未知"
        lines.append(f"- {item.name}（{asset}{amount}）")
    return lines


def group_records_by_name(records: Iterable[AssetRecord]) -> List[Dict[str, Any]]:
    """Display-time grouping: identical names are summed, assetids remain attached."""
    groups: Dict[str, Dict[str, Any]] = {}
    for record in records:
        group = groups.setdefault(
            record.name,
            {
                "name": record.name,
                "count": 0,
                "assetids": [],
                "sources": [],
                "tradeprotected": False,
                "tradelocked": False,
                "protection_states": set(),
            },
        )
        group["count"] += record.amount
        if record.assetid:
            group["assetids"].append(record.assetid)
        for source in record.sources:
            if source not in group["sources"]:
                group["sources"].append(source)
        group["tradeprotected"] = bool(group["tradeprotected"] or record.tradeprotected)
        group["tradelocked"] = bool(group["tradelocked"] or record.tradelocked)
        if record.protection_state:
            group["protection_states"].add(record.protection_state)
    for group in groups.values():
        states = group["protection_states"]
        if "active" in states:
            group["protection_state"] = "active"
        elif "ended" in states:
            group["protection_state"] = "ended"
        elif "unknown" in states:
            group["protection_state"] = "unknown"
        else:
            group["protection_state"] = ""
        group.pop("protection_states", None)
    return sorted(groups.values(), key=lambda group: group["name"])


def _sources_entry(
    requests: int,
    items_per_request: Sequence[int],
    *,
    error: str = "",
    total_inventory_count: Optional[int] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "requests": requests,
        "items_per_request": list(items_per_request),
        "items_total": sum(items_per_request),
    }
    if total_inventory_count is not None:
        entry["total_inventory_count"] = total_inventory_count
    if error:
        entry["error"] = error
    return entry


def fetch_csfloat_listings(
    steamid: str,
    *,
    api_key: Optional[str] = None,
    timeout: float = 25.0,
) -> Tuple[List[Dict[str, Any]], str]:
    """Best-effort CSFloat public listings for a SteamID (optional supplement only).

    Returns (listing_rows, note). Listings only cover items the account has placed
    for sale; they never prove the full inventory and must be marked low-confidence.
    """
    url = "https://csfloat.com/api/v1/listings"
    params: Dict[str, Any] = {"user_id": steamid, "limit": 100}
    headers: Dict[str, str] = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = api_key
    try:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        rows: List[Dict[str, Any]] = []
        if isinstance(payload, Mapping):
            for listing in payload.get("data") or payload.get("listings") or []:
                if isinstance(listing, Mapping):
                    rows.append(
                        {
                            "assetid": _string(listing.get("item", {}).get("asset_id") if isinstance(listing.get("item"), Mapping) else listing.get("assetid")),
                            "name": _string(listing.get("item", {}).get("market_hash_name") if isinstance(listing.get("item"), Mapping) else listing.get("market_hash_name") or listing.get("name")),
                        }
                    )
        return rows, "csfloat_public_listings"
    except (urllib.error.URLError, urllib.error.HTTPError, SteamQueryError, ValueError, OSError) as exc:
        return [], f"csfloat_public_listings_unavailable:{type(exc).__name__}"


def run_max_coverage_query(
    steamid: str,
    *,
    key: Optional[str] = None,
    language: str = DEFAULT_LANGUAGE,
    timeout: float = 60.0,
    trading_samples: int = 3,
    normal_samples: int = 1,
    include_mode1: bool = True,
    include_parse1: bool = True,
    parse1_samples: int = 2,
    enforce_budget: bool = False,
    include_public: bool = True,
    trade_url: Optional[str] = None,
    steam_cookie: Optional[str] = None,
    steam_cookie_file: Optional[str] = None,
    steam_login_secure: Optional[str] = None,
    sessionid: Optional[str] = None,
    trade_locked: str = "1",
    observation_cache_path: Optional[str] = None,
    seed_payloads: Sequence[Mapping[str, Any]] = (),
    now: Optional[int] = None,
    include_market: bool = False,
    csfloat_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Maximum-coverage query: many independent live samples merged by assetid.

    The result is diagnostic-first: per-source counts, dedupe statistics, excluded
    false positives, the official total_inventory_count gap, coverage status and an
    evidence level. It never claims 100% completeness for a SteamID-only query.
    """
    if now is None:
        now = int(time.time())
    started = time.monotonic()
    if not key:
        key = load_steamwebapi_key(None, None)
    sources: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    official_public_records: List[AssetRecord] = []
    normal_records: List[AssetRecord] = []
    trading_records: List[AssetRecord] = []
    parsed_records: List[AssetRecord] = []
    unverified_records: List[AssetRecord] = []
    official_total: Optional[int] = None
    official_returned = 0
    successful_trading_pages = 0
    owner_cookie = build_cookie_header(
        steam_cookie=steam_cookie,
        steam_cookie_file=steam_cookie_file,
        steam_login_secure=steam_login_secure,
        sessionid=sessionid,
    )

    if seed_payloads:
        seed_records: List[AssetRecord] = []
        for payload in seed_payloads:
            seed_records.extend(
                asset_records_from_raw_payload(payload, source="historical_seed")
            )
        seed_observation_cache(
            steamid,
            seed_records,
            cache_path=observation_cache_path,
            now=now,
        )

    if include_public:
        try:
            public_payload = fetch_public_inventory(
                steamid, language=language, max_pages=20, timeout=min(timeout, 25.0)
            )
            official_total = public_payload.get("total_inventory_count")
            official_public_records = asset_records_from_public_payload(
                public_payload, source="steam_public_contextid2"
            )
            official_returned = len(official_public_records)
            sources["steam_public_contextid2"] = _sources_entry(
                1, [official_returned], total_inventory_count=official_total
            )
        except SteamQueryError as exc:
            errors.append(f"官方公开库存: {exc}")
            sources["steam_public_contextid2"] = _sources_entry(1, [0], error=str(exc))

    owner_records: List[AssetRecord] = []
    owner_tradable_by_assetid: Dict[str, bool] = {}
    owner_total: Optional[int] = None
    if owner_cookie and include_public:
        try:
            owner_payload = fetch_public_inventory(
                steamid,
                language=language,
                max_pages=20,
                timeout=min(timeout, 25.0),
                cookie=owner_cookie,
            )
            owner_total = owner_payload.get("total_inventory_count")
            owner_records = asset_records_from_public_payload(
                owner_payload, source="steam_owner_session_contextid2"
            )
            owner_description_map = build_description_map(
                owner_payload.get("descriptions") or [], default_contextid=CONTEXTID_CS2
            )
            for asset in owner_payload.get("assets") or []:
                description = lookup_description(asset, owner_description_map)
                owner_tradable_by_assetid[_string(asset.get("assetid"))] = (
                    _int(description.get("tradable"), 1) != 0 if description else True
                )
            sources["steam_owner_session_contextid2"] = _sources_entry(
                1, [len(owner_records)], total_inventory_count=owner_total
            )
        except SteamQueryError as exc:
            errors.append(f"owner 会话库存: {exc}")
            sources["steam_owner_session_contextid2"] = _sources_entry(1, [0], error=str(exc))

    if key and normal_samples > 0:
        normal_fetch = fetch_steamwebapi_raw_inventory(
            steamid,
            key=key,
            mode="0",
            parse="0",
            language=language,
            state="active",
            no_cache="1",
            samples=normal_samples,
            timeout=timeout,
            trade_url=trade_url,
            steam_login_secure=steam_login_secure,
            trade_locked=trade_locked,
            label="steamwebapi:parse=0:mode=0",
        )
        for page in normal_fetch.pages:
            normal_records.extend(asset_records_from_raw_payload(page.payload, source=page.source))
        sources["steamwebapi:parse=0:mode=0"] = _sources_entry(
            len(normal_fetch.pages),
            normal_fetch.upstream_item_counts,
            error="；".join(normal_fetch.errors),
        )
        errors.extend(normal_fetch.errors)

    if key and trading_samples > 0:
        trading_fetch = fetch_steamwebapi_raw_inventory(
            steamid,
            key=key,
            mode="2",
            parse="0",
            language=language,
            state="active",
            no_cache="1",
            samples=trading_samples,
            timeout=timeout,
            trade_url=trade_url,
            steam_login_secure=steam_login_secure,
            trade_locked=trade_locked,
            label="steamwebapi:parse=0:mode=2",
        )
        for page in trading_fetch.pages:
            trading_records.extend(asset_records_from_raw_payload(page.payload, source=page.source))
        successful_trading_pages = sum(
            1 for page in trading_fetch.pages if page.item_count > 0 and not page.error
        )
        sources["steamwebapi:parse=0:mode=2"] = _sources_entry(
            len(trading_fetch.pages),
            trading_fetch.upstream_item_counts,
            error="；".join(trading_fetch.errors),
        )
        errors.extend(trading_fetch.errors)

    if key and include_mode1:
        mode1_fetch = fetch_steamwebapi_raw_inventory(
            steamid,
            key=key,
            mode="1",
            parse="0",
            language=language,
            state="active",
            no_cache="1",
            samples=1,
            timeout=timeout,
            trade_url=trade_url,
            steam_login_secure=steam_login_secure,
            trade_locked=trade_locked,
            label="steamwebapi:parse=0:mode=1",
        )
        for page in mode1_fetch.pages:
            trading_records.extend(asset_records_from_raw_payload(page.payload, source=page.source))
        sources["steamwebapi:parse=0:mode=1"] = _sources_entry(
            len(mode1_fetch.pages),
            mode1_fetch.upstream_item_counts,
            error="；".join(mode1_fetch.errors),
        )
        errors.extend(mode1_fetch.errors)

    if key and include_parse1:
        parsed_fetch = fetch_steamwebapi_raw_inventory(
            steamid,
            key=key,
            mode="2",
            parse="1",
            language=language,
            state="active",
            no_cache="1",
            samples=max(1, parse1_samples),
            timeout=timeout,
            trade_url=trade_url,
            steam_login_secure=steam_login_secure,
            trade_locked=trade_locked,
            label="steamwebapi:parse=1:mode=2",
        )
        for page in parsed_fetch.pages:
            parsed_records.extend(asset_records_from_parsed_payload(page.payload, source=page.source))
        sources["steamwebapi:parse=1:mode=2"] = _sources_entry(
            len(parsed_fetch.pages),
            parsed_fetch.upstream_item_counts,
            error="；".join(parsed_fetch.errors),
        )
        errors.extend(parsed_fetch.errors)

    public_records = merge_asset_records([official_public_records, normal_records])
    trading_merged = merge_asset_records([trading_records])
    protected_candidates, public_missing_candidates, excluded = classify_hidden_assets(
        trading_merged, public_records, parsed_records, now=now
    )

    if owner_records:
        # Owner-authenticated view is authoritative for the hidden set: the official
        # inventory endpoint with the owner session returns every CS2 asset including
        # trade-locked items, and its descriptions carry the real tradable flag.
        protected_candidates, public_missing_candidates = classify_owner_view_hidden(
            owner_records,
            public_records,
            owner_tradable_by_assetid,
            trading_records=trading_merged,
            parsed_records=parsed_records,
        )

    if official_total is not None and not owner_records and enforce_budget:
        hidden_budget = max(0, int(official_total) - official_returned)
        protected_candidates, public_missing_candidates, budget_excluded, budget_unverified = apply_hidden_budget(
            protected_candidates,
            public_missing_candidates,
            trading_assetids={record.assetid for record in trading_merged},
            hidden_budget=hidden_budget,
        )
        excluded.extend(budget_excluded)
        unverified_records.extend(budget_unverified)
        kept_ids = {record.assetid for record in protected_candidates} | {
            record.assetid for record in public_missing_candidates
        }
        trading_set = {record.assetid for record in trading_merged}
        cache = _read_observation_cache(observation_cache_path)
        account_rows = cache.get(steamid)
        if isinstance(account_rows, dict):
            remaining = max(0, hidden_budget - len(kept_ids))
            unverified_active = [
                (assetid, row)
                for assetid, row in account_rows.items()
                if isinstance(row, dict)
                and row.get("protection_state") == "active"
                and assetid not in kept_ids
                and assetid not in trading_set
            ]
            if len(unverified_active) > remaining:
                for assetid, row in unverified_active:
                    row_sources = row.get("sources")
                    unverified_records.append(
                        AssetRecord(
                            assetid=assetid,
                            classid=_string(row.get("classid")),
                            instanceid=_string(row.get("instanceid"), "0") or "0",
                            name=_string(row.get("name") or "未知物品"),
                            amount=_amount(row.get("amount")),
                            contextid=CONTEXTID_CS2,
                            appid=str(APPID_CS2),
                            tradeprotected=bool(row.get("tradeprotected")),
                            tradelocked=bool(row.get("tradelocked")),
                            sources=tuple(row_sources) if isinstance(row_sources, list) else (),
                            protection_state="unverified",
                        )
                    )
                    account_rows.pop(assetid, None)
                cache[steamid] = account_rows
                _write_observation_cache(observation_cache_path, cache)

    hidden_candidates = list(protected_candidates) + list(public_missing_candidates)

    live_hidden, observed_hidden = apply_observation_cache(
        steamid,
        hidden_candidates,
        {record.assetid for record in public_records},
        cache_path=observation_cache_path,
        now=now,
    )
    live_protected = [record for record in live_hidden if record.protection_state == "active"]
    live_public_missing = [record for record in live_hidden if record.protection_state != "active"]
    observed_protected = [record for record in observed_hidden if record.protection_state == "active"]
    observed_public_missing = [record for record in observed_hidden if record.protection_state != "active"]

    market_supplements: List[Dict[str, Any]] = []
    if include_market:
        listings, listing_source = fetch_csfloat_listings(steamid, api_key=csfloat_api_key, timeout=min(timeout, 25.0))
        if listing_source.startswith("csfloat_public_listings_unavailable"):
            sources["csfloat_public_listings"] = _sources_entry(1, [0], error=listing_source)
        else:
            known_assetids = {record.assetid for record in public_records}
            protected_assetids = {record.assetid for record in live_hidden} | {record.assetid for record in observed_hidden}
            for listing in listings:
                assetid = listing.get("assetid") or ""
                if not assetid or assetid in known_assetids or assetid in protected_assetids:
                    continue
                market_supplements.append(
                    {
                        "assetid": assetid,
                        "name": listing.get("name") or "未知物品",
                        "source": listing_source,
                        "credibility": "low",
                        "note": "仅证明该 assetid 曾上架 CSFloat，不等于完整库存证据",
                    }
                )
            sources["csfloat_public_listings"] = _sources_entry(1, [len(listings)], error="")

    protected_live_count = sum(record.amount for record in live_protected)
    protected_observed_count = sum(record.amount for record in observed_protected)
    public_missing_live_count = sum(record.amount for record in live_public_missing)
    public_missing_observed_count = sum(record.amount for record in observed_public_missing)
    public_count = sum(record.amount for record in public_records)
    protected_total = protected_live_count + protected_observed_count
    public_missing_total = public_missing_live_count + public_missing_observed_count
    hidden_total = protected_total + public_missing_total
    total_count = hidden_total + public_count

    unverified_groups = group_records_by_name(unverified_records)
    unverified_protected: List[Dict[str, Any]] = []
    for group in unverified_groups:
        sample_numbers = set()
        for source in group.get("sources") or []:
            match = re.search(r"sample=(\d+)", str(source))
            if match:
                sample_numbers.add(int(match.group(1)))
        unverified_protected.append(
            {
                "name": group["name"],
                "count": group["count"],
                "assetids": group["assetids"],
                "sources": group["sources"],
                "samples_seen": len(sample_numbers),
                "note": (
                    f"官方 total_inventory_count={official_total}，公开返回 {official_returned}，"
                    f"隐藏预算 {max(0, int(official_total or 0) - official_returned)}；"
                    "该行仅在 parse=1 出现且未被 parse=0 trading 佐证，无法与官方计数自洽，"
                    "因此未计入 protected/public_missing，是否真实需 owner 会话确认。"
                ),
            }
        )
    unverified_count = sum(int(group["count"]) for group in unverified_protected)

    raw_assetids = [
        record.assetid
        for record in [*official_public_records, *normal_records, *trading_records, *parsed_records]
        if record.assetid
    ]
    dedupe = {
        "observations_before_dedupe": len(raw_assetids),
        "unique_assetids_after_dedupe": len(set(raw_assetids)),
        "unique_public": len(public_records),
        "unique_protected_live": len(live_protected),
        "unique_protected_observed": len(observed_protected),
        "unique_public_missing_live": len(live_public_missing),
        "unique_public_missing_observed": len(observed_public_missing),
    }

    official_gap: Optional[Dict[str, int]] = None
    if official_total is not None:
        official_gap = {
            "total_inventory_count": int(official_total),
            "returned": official_returned,
            "gap": max(0, int(official_total) - official_returned),
        }
        if owner_total is not None:
            official_gap["owner_total_inventory_count"] = int(owner_total)
            official_gap["owner_returned"] = len(owner_records)
            official_gap["owner_gap"] = max(0, int(owner_total) - len(owner_records))

    hidden_gap_candidates: List[Dict[str, Any]] = []
    hidden_gap = 0
    if official_gap is not None:
        observed_hidden_ids = {record.assetid for record in live_hidden} | {record.assetid for record in observed_hidden}
        hidden_gap = max(0, int(official_gap["gap"]) - len(observed_hidden_ids))
        public_ids = {record.assetid for record in public_records}
        account = _read_observation_cache(observation_cache_path).get(steamid) or {}
        for assetid, row in sorted(account.items()):
            if not isinstance(row, dict):
                continue
            if assetid in public_ids or assetid in observed_hidden_ids:
                continue
            if _int(row.get("expires_at"), 0) <= now:
                continue
            hidden_gap_candidates.append(
                {
                    "assetid": assetid,
                    "name": _string(row.get("name") or "未知物品"),
                    "protection_state": _string(row.get("protection_state")) or "unknown",
                    "first_seen": row.get("first_seen"),
                    "last_seen": row.get("last_seen"),
                    "sources": row.get("sources") or [],
                    "note": "SteamID 侧当前所有可访问数据源均未返回该资产，保护状态无法确认；可能仍处于交易保护。",
                }
            )

    evidence_level = "low"
    if official_public_records:
        evidence_level = "medium"
        if successful_trading_pages >= 2 and include_parse1:
            evidence_level = "high"
    coverage_status = "partial"
    if errors and not public_records and not live_hidden:
        coverage_status = "failed"
    elif errors:
        coverage_status = "degraded"

    elapsed_ms = int((time.monotonic() - started) * 1000)
    note = (
        "仅 SteamID 路径：交易保护期约7天，但近期交易/获得的物品仍会被 Steam 对外隐藏约10天，"
        "且第三方接口可能遗漏部分受保护物品；protected 只是有明确保护证据的下限，"
        "hidden_gap 给出官方 total_inventory_count 与已观测隐藏资产的差值。不声称100%完整。"
    )
    return {
        "steamid": steamid,
        "owner_view": bool(owner_cookie),
        "protected_live": group_records_by_name(live_protected),
        "protected_observed": group_records_by_name(observed_protected),
        "public_missing_live": group_records_by_name(live_public_missing),
        "public_missing_observed": group_records_by_name(observed_public_missing),
        "unverified_protected": unverified_protected,
        "public": group_records_by_name(public_records),
        "market_supplements": market_supplements,
        "counts": {
            "protected_live": protected_live_count,
            "protected_observed": protected_observed_count,
            "protected": protected_total,
            "public_missing_live": public_missing_live_count,
            "public_missing_observed": public_missing_observed_count,
            "public_missing": public_missing_total,
            "hidden": hidden_total,
            "hidden_gap": hidden_gap,
            "unverified": unverified_count,
            "public": public_count,
            "total": total_count,
        },
        "sources": sources,
        "dedupe": dedupe,
        "excluded_false_positives": excluded,
        "official_gap": official_gap,
        "hidden_gap_candidates": hidden_gap_candidates,
        "elapsed_ms": elapsed_ms,
        "coverage": {
            "status": coverage_status,
            "evidence_level": evidence_level,
            "note": note,
        },
        "errors": errors,
    }


def _format_grouped_records(groups: Sequence[Dict[str, Any]], *, show_assetids: bool = True, show_sources: bool = True) -> List[str]:
    if not groups:
        return ["（空）"]
    lines: List[str] = []
    for group in groups:
        count = int(group.get("count", 1))
        suffix = f" x{count}" if count > 1 else ""
        detail_parts: List[str] = []
        if show_assetids and group.get("assetids"):
            detail_parts.append("assetid=" + ",".join(str(value) for value in group["assetids"]))
        if show_sources and group.get("sources"):
            detail_parts.append("来源=" + ",".join(str(value) for value in group["sources"]))
        detail = f"（{'；'.join(detail_parts)}）" if detail_parts else ""
        lines.append(f"- {group.get('name', '未知物品')}{suffix}{detail}")
    return lines


def render_maxcoverage_report(result: Mapping[str, Any]) -> str:
    lines: List[str] = [f"CS2 库存查询结果（SteamID64: {result.get('steamid', '')}）- 最大覆盖模式"]
    lines.append("")
    lines.append("一、仍处于交易保护的物品（protected_live，tradeprotected/tradelocked 有实际证据）")
    lines.extend(_format_grouped_records(result.get("protected_live") or []))
    lines.append("")
    lines.append("二、近期观测补全的交易保护物品（protected_observed，来自10天观测缓存）")
    lines.extend(_format_grouped_records(result.get("protected_observed") or []))
    lines.append("")
    lines.append("三、未核实的受保护声明（unverified_protected，超出官方计数预算，未计入统计）")
    unverified = result.get("unverified_protected") or []
    if not unverified:
        lines.append("（无）")
    else:
        for row in unverified:
            lines.append(
                f"- {row.get('name', '未知物品')} x{row.get('count', 1)}（assetid={','.join(str(value) for value in row.get('assetids') or [])}，"
                f"采样出现 {row.get('samples_seen', 0)} 次，来源={','.join(row.get('sources') or [])}）"
            )
            if row.get("note"):
                lines.append(f"  * {row['note']}")
    lines.append("")
    lines.append("四、公开库存缺失、保护状态以客户端为准的物品（public_missing_live，约10天可见性窗口）")
    lines.extend(_format_grouped_records(result.get("public_missing_live") or []))
    lines.append("")
    lines.append("五、近期观测补全的公开缺失物品（保护状态以客户端为准，来自10天观测缓存）")
    lines.extend(_format_grouped_records(result.get("public_missing_observed") or []))
    lines.append("")
    lines.append("六、公开可见物品（public，官方 contextid=2 与 normal inventory 并集）")
    lines.extend(_format_grouped_records(result.get("public") or [], show_assetids=False, show_sources=False))

    market = result.get("market_supplements") or []
    lines.append("")
    lines.append("七、保护状态未知/未观测到的隐藏资产候选（hidden_gap_candidates）")
    gap_candidates = result.get("hidden_gap_candidates") or []
    if not gap_candidates:
        lines.append("（无）")
    else:
        for row in gap_candidates:
            lines.append(
                f"- {row.get('name', '未知物品')}（assetid={row.get('assetid', '未知')}，保护状态={row.get('protection_state', 'unknown')}，来源={','.join(row.get('sources') or [])}）"
            )
            if row.get("note"):
                lines.append(f"  * {row['note']}")

    lines.append("")
    lines.append("八、公开市场补充（仅上架记录，低可信度，不计入主统计）")
    if not market:
        lines.append("（未启用或未返回记录）")
    else:
        for row in market:
            lines.append(
                f"- {row.get('name', '未知物品')}（assetid={row.get('assetid', '未知')}，来源={row.get('source', '')}，可信度={row.get('credibility', '')}）"
            )

    counts = result.get("counts") or {}
    lines.append("")
    lines.append("九、诊断信息")
    lines.append(
        f"- 交易保护中={counts.get('protected', 0)} 件（实时 {counts.get('protected_live', 0)} + 观测补全 {counts.get('protected_observed', 0)}）；"
        f"公开缺失且保护状态待客户端确认={counts.get('public_missing', 0)} 件（实时 {counts.get('public_missing_live', 0)} + 观测补全 {counts.get('public_missing_observed', 0)}）；"
        f"未核实受保护声明={counts.get('unverified', 0)} 件；public={counts.get('public', 0)} 件；"
        f"隐藏缺口={counts.get('hidden_gap', 0)} 件；total={counts.get('total', 0)} 件。"
    )

    sources = result.get("sources") or {}
    for source_name, entry in sorted(sources.items()):
        items = "/".join(str(value) for value in entry.get("items_per_request", []))
        base = f"- 数据源 {source_name}：请求 {entry.get('requests', 0)} 次，每次返回 item 数 [{items}]，合计 {entry.get('items_total', 0)}"
        if entry.get("total_inventory_count") is not None:
            base += f"，total_inventory_count={entry['total_inventory_count']}"
        if entry.get("error"):
            base += f"，错误={entry['error']}"
        lines.append(base)

    dedupe = result.get("dedupe") or {}
    lines.append(
        f"- assetid 去重：去重前观测 {dedupe.get('observations_before_dedupe', 0)} 条；去重后唯一 assetid {dedupe.get('unique_assetids_after_dedupe', 0)} 个（public={dedupe.get('unique_public', 0)}，"
        f"protected_live={dedupe.get('unique_protected_live', 0)}，public_missing_live={dedupe.get('unique_public_missing_live', 0)}，观测补全合计={dedupe.get('unique_protected_observed', 0) + dedupe.get('unique_public_missing_observed', 0)}）。"
    )

    excluded = result.get("excluded_false_positives") or []
    if excluded:
        lines.append(f"- 被排除的假阳性/无效候选 {len(excluded)} 条：")
        for row in excluded:
            lines.append(f"  * {row.get('name', '未知物品')}（assetid={row.get('assetid', '未知')}）：{row.get('reason', '')}")
    else:
        lines.append("- 被排除的假阳性/无效候选：0 条。")

    gap = result.get("official_gap")
    if gap:
        lines.append(
            f"- 官方 total_inventory_count 差值：官方声明 {gap.get('total_inventory_count', 0)}，实际返回 {gap.get('returned', 0)}，差值 {gap.get('gap', 0)}（可能对应近期获得且仍对外隐藏的物品）。"
        )
        if gap.get("owner_total_inventory_count") is not None:
            lines.append(
                f"- owner 会话视图：官方声明 {gap.get('owner_total_inventory_count', 0)}，owner 返回 {gap.get('owner_returned', 0)}，owner 差值 {gap.get('owner_gap', 0)}。"
            )
    else:
        lines.append("- 官方 total_inventory_count：未取得（官方公开库存不可用）。")

    coverage = result.get("coverage") or {}
    lines.append(f"- 覆盖状态={coverage.get('status', 'unknown')}；证据等级={coverage.get('evidence_level', 'unknown')}。")
    lines.append(f"- 认证视图：{'owner 会话已启用（可看到完整库存与交易保护物品）' if result.get('owner_view') else '仅 SteamID 匿名视图（受保护物品只能按证据推断）'}。")
    lines.append(f"- 查询耗时：{result.get('elapsed_ms', 0)} ms。")
    errors = result.get("errors") or []
    if errors:
        lines.append(f"- 上游错误 {len(errors)} 条：")
        for error in errors:
            lines.append(f"  * {error}")
    lines.append("")
    lines.append(f"覆盖说明：{coverage.get('note', '')}")
    return "\n".join(lines)


def render_report(
    steamid: str,
    protected_items: Sequence[ProtectedItem] | None,
    visible_items: Sequence[InventoryItem] | None,
    *,
    protected_status: str = "ok",
    visible_status: str = "ok",
    show_details: bool = False,
    coverage_status: str = "",
) -> str:
    lines: List[str] = [f"CS2 库存查询结果（SteamID64: {steamid}）"]
    lines.append("")
    lines.append("1. 受到交易保护 / 公开库存直接查不到的物品名称（优先，通过 Steamwebapi / 交易历史识别）")
    if protected_status != "ok":
        lines.append(f"- {protected_status}")
    else:
        assert protected_items is not None
        lines.extend(_format_protected_details(protected_items) if show_details else _format_counter(counter_by_name(protected_items)))
    lines.append("")
    lines.append("2. 不处于交易保护的物品名称（公开库存接口当前可见）")
    if visible_status != "ok":
        lines.append(f"- {visible_status}")
    else:
        assert visible_items is not None
        lines.extend(_format_inventory_details(visible_items) if show_details else _format_counter(counter_by_name(visible_items)))
    protected_units = sum(item.amount for item in protected_items or []) if protected_status == "ok" else 0
    visible_units = sum(item.amount for item in visible_items or []) if visible_status == "ok" else 0
    lines.append("")
    lines.append(f"统计：交易保护/公开缺失 {protected_units} 件；公开可见 {visible_units} 件；合计 {protected_units + visible_units} 件。")
    if coverage_status:
        lines.append(f"数据完整性：{coverage_status}")
    return "\n".join(lines)


def load_json_file(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SteamQueryError(f"无法读取 JSON 文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SteamQueryError(f"JSON 文件格式错误 {path}: {exc}") from exc


def build_json_result(steamid: str, protected_items: Sequence[ProtectedItem] | None, visible_items: Sequence[InventoryItem] | None, protected_status: str, visible_status: str) -> Dict[str, Any]:
    return {
        "steamid": steamid,
        "protected_status": protected_status,
        "visible_status": visible_status,
        "trade_protected_items": [dataclasses.asdict(item) for item in protected_items] if protected_items is not None else None,
        "visible_non_protected_items": [dataclasses.asdict(item) for item in visible_items] if visible_items is not None else None,
        "trade_protected_names": dict(counter_by_name(protected_items or [])),
        "visible_non_protected_names": dict(counter_by_name(visible_items or [])),
    }


def load_steamwebapi_key(key_arg: str | None, key_file: str | None) -> str | None:
    if key_arg:
        return key_arg.strip()
    if not key_file:
        key_file = DEFAULT_STEAMWEBAPI_KEY_FILE
        if not os.path.exists(key_file):
            return None
    try:
        return open(key_file, "r", encoding="utf-8").read().strip()
    except OSError as exc:
        raise SteamQueryError(f"无法读取 Steamwebapi key 文件 {key_file}: {exc}") from exc


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="输入 SteamID64，输出 CS2 受交易保护物品名称和当前公开可见物品名称。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("steamid", nargs="?", help="目标 SteamID64。")
    parser.add_argument("--steamwebapi-key", default=os.getenv("STEAMWEBAPI_KEY"), help="推荐：Steamwebapi.com 第三方 API Key。配置后用户运行时只需输入 steamid。也可用环境变量 STEAMWEBAPI_KEY。")
    parser.add_argument("--steamwebapi-key-file", default=os.getenv("STEAMWEBAPI_KEY_FILE"), help="Steamwebapi key 文件路径。也可用环境变量 STEAMWEBAPI_KEY_FILE。")
    parser.add_argument("--steamwebapi-mode", choices=["0", "1", "2"], default=os.getenv("STEAMWEBAPI_TRADE_PROTECTED_MODE", "2"), help="Steamwebapi 交易保护检测：0=关闭，1=交易库存优先并回退普通库存，2=只用交易库存。")
    parser.add_argument("--steamwebapi-state", choices=["active", "fallback", "takedb"], default=os.getenv("STEAMWEBAPI_STATE", "active"), help="Steamwebapi 库存读取模式。")
    parser.add_argument("--steamwebapi-no-cache", choices=["0", "1"], default=os.getenv("STEAMWEBAPI_NO_CACHE", "1"), help="Steamwebapi 是否绕过缓存；完整性优先默认使用 1。")
    parser.add_argument("--steamwebapi-samples", type=int, default=int(os.getenv("STEAMWEBAPI_SAMPLES", "2")), help="新鲜交易库存采样次数；按 assetid 合并，降低上游临时漏项。")
    parser.add_argument("--max-coverage", action="store_true", default=True, help="使用最大覆盖模式：多路 mode/parse 实时样本、分页、按 assetid 并集、观测缓存与完整诊断（默认开启）。")
    parser.add_argument("--legacy", action="store_true", help="回退到旧版单一路径查询（parse=1 + 单一 trading 采样）。")
    parser.add_argument("--trading-samples", type=int, default=int(os.getenv("STEAMWEBAPI_TRADING_SAMPLES", "3")), help="最大覆盖模式下 trading inventory mode=2 的独立实时采样次数。")
    parser.add_argument("--normal-samples", type=int, default=int(os.getenv("STEAMWEBAPI_NORMAL_SAMPLES", "1")), help="最大覆盖模式下 normal inventory mode=0 的独立实时采样次数。")
    parser.add_argument("--no-mode1", action="store_true", help="最大覆盖模式下跳过 mode=1 对照样本。")
    parser.add_argument("--no-parse1", action="store_true", help="最大覆盖模式下跳过 parse=1 对照响应。")
    parser.add_argument("--parse1-samples", type=int, default=int(os.getenv("STEAMWEBAPI_PARSE1_SAMPLES", "2")), help="最大覆盖模式下 parse=1 的独立实时采样次数；默认双采样以降低上游波动造成的漏检。")
    parser.add_argument("--no-budget-check", action="store_true", help="默认行为：不使用官方 total_inventory_count 做核验，按各上游源的最大并集返回（宁多勿少）。")
    parser.add_argument("--budget-check", action="store_true", help="可选：重新启用官方 total_inventory_count 预算核验；超预算的 parse=1 受保护行会转入 unverified_protected。")
    parser.add_argument("--observation-cache", default=os.getenv("INVENTORY_OBSERVATION_CACHE", DEFAULT_OBSERVATION_CACHE_FILE), help="受保护 assetid 的持久化观测缓存路径（保留10天）。")
    parser.add_argument("--seed-observations-json", action="append", default=[], help="历史 parse=0 库存 JSON 样本；其 assetid 作为“保护状态未知”的历史观测种子写入缓存。可重复传入。")
    parser.add_argument("--trade-locked", choices=["0", "1"], default=os.getenv("STEAMWEBAPI_TRADE_LOCKED", "1"), help="配合 --steam-login-secure：是否请求 owner 视角的 trade-locked 物品（1=是）。")
    parser.add_argument("--include-market", action="store_true", help="接入无需目标账号 Cookie 的公开市场补充数据（目前支持 CSFloat 上架记录），并标记来源与可信度。")
    parser.add_argument("--csfloat-api-key", default=os.getenv("CSFLOAT_API_KEY"), help="可选：CSFloat API Key；不传时使用公开列表页。")
    parser.add_argument("--output-file", help="将 JSON 结果写入该文件。")
    parser.add_argument("--trade-url", default=os.getenv("STEAM_TRADE_URL"), help="可选：目标账号的 Steam Trade URL；Steamwebapi 文档指定该路径可读取其 7-10 天交易锁定物品。")
    parser.add_argument("--api-key", default=os.getenv("STEAM_WEB_API_KEY"), help="可选：Steam Web API Key；不传时走无 Key 的 Steam 登录 Cookie 历史页模式。也可用环境变量 STEAM_WEB_API_KEY。")
    parser.add_argument("--cookie", default=os.getenv("STEAM_COOKIE"), help="无 Key 模式：Steam Community 登录 Cookie 原文。也可用环境变量 STEAM_COOKIE。")
    parser.add_argument("--cookie-file", default=os.getenv("STEAM_COOKIE_FILE"), help="无 Key 模式：包含 Steam Cookie 的文本文件或 Netscape/curl cookie jar。也可用环境变量 STEAM_COOKIE_FILE。")
    parser.add_argument("--steam-login-secure", default=os.getenv("STEAM_LOGIN_SECURE"), help="无 Key 模式：steamLoginSecure 的值。也可用环境变量 STEAM_LOGIN_SECURE。")
    parser.add_argument("--sessionid", default=os.getenv("STEAM_SESSIONID"), help="无 Key 模式：sessionid 的值。也可用环境变量 STEAM_SESSIONID。")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="Steam 返回物品名称的语言，如 schinese/english。")
    parser.add_argument("--max-trades", type=int, default=500, help="每页交易历史数量。")
    parser.add_argument("--trade-history-pages", type=int, default=5, help="最多读取的交易历史页数。")
    parser.add_argument("--inventory-pages", type=int, default=20, help="最多读取的公开库存页数。")
    parser.add_argument("--timeout", type=float, default=20.0, help="单次 HTTP 超时秒数。")
    parser.add_argument("--details", action="store_true", help="受保护物品同时显示收到时间、保护到期时间、tradeid、assetid。")
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    parser.add_argument("--skip-visible", action="store_true", help="只查受交易保护物品，跳过公开库存。")
    parser.add_argument("--history-kind", choices=["auto", "tradehistory", "inventoryhistory"], default="auto", help="无 Key 模式读取的 Steam Community 历史页。")
    parser.add_argument("--inventory-json", help="从本地 Steam 库存 JSON 文件读取，用于测试/离线解析。")
    parser.add_argument("--trade-history-json", action="append", help="从本地交易历史 JSON 文件读取；可重复传入多页，用于测试/离线解析。")
    parser.add_argument("--steamwebapi-json", help="从本地 Steamwebapi inventory JSON 文件读取；用于测试/离线解析。")
    parser.add_argument("--history-html", action="append", help="从本地 Steam tradehistory/inventoryhistory HTML 文件读取；用于无 Key 模式离线解析测试。")
    parser.add_argument("--now", type=int, help="用于离线测试的当前 Unix 时间；不传则使用系统当前时间。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    steamid = args.steamid or input("SteamID64: ").strip()
    if not steamid:
        print("SteamID64 为空。", file=sys.stderr)
        return 2

    offline_requested = any(
        [args.steamwebapi_json, args.trade_history_json, args.history_html, args.inventory_json]
    )
    if args.max_coverage and not args.legacy and not offline_requested:
        try:
            steamwebapi_key = load_steamwebapi_key(args.steamwebapi_key, args.steamwebapi_key_file)
        except SteamQueryError as exc:
            steamwebapi_key = None
            max_coverage_result: Dict[str, Any] = {
                "steamid": steamid,
                "counts": {"protected_live": 0, "protected_observed": 0, "protected": 0, "public": 0, "total": 0},
                "sources": {},
                "dedupe": {},
                "excluded_false_positives": [],
                "official_gap": None,
                "elapsed_ms": 0,
                "coverage": {"status": "failed", "evidence_level": "low", "note": str(exc)},
                "errors": [f"读取 Steamwebapi key 失败：{exc}"],
            }
        else:
            try:
                max_coverage_result = run_max_coverage_query(
                    steamid,
                    key=steamwebapi_key,
                    language=args.language,
                    timeout=args.timeout,
                    trading_samples=max(1, args.trading_samples),
                    normal_samples=0 if args.skip_visible else max(0, args.normal_samples),
                    include_mode1=not args.no_mode1,
                    include_parse1=not args.no_parse1,
                    parse1_samples=max(1, args.parse1_samples),
                    enforce_budget=(args.budget_check and not args.no_budget_check),
                    include_public=not args.skip_visible,
                    trade_url=args.trade_url,
                    steam_cookie=args.cookie,
                    steam_cookie_file=args.cookie_file,
                    steam_login_secure=args.steam_login_secure,
                    sessionid=args.sessionid,
                    trade_locked=args.trade_locked,
                    observation_cache_path=args.observation_cache,
                    seed_payloads=tuple(
                        payload
                        for payload in (load_json_file(path) for path in args.seed_observations_json)
                        if isinstance(payload, Mapping)
                    ),
                    now=args.now,
                    include_market=args.include_market,
                    csfloat_api_key=args.csfloat_api_key,
                )
            except Exception as exc:
                max_coverage_result = {
                    "steamid": steamid,
                    "counts": {"protected_live": 0, "protected_observed": 0, "protected": 0, "public": 0, "total": 0},
                    "sources": {},
                    "dedupe": {},
                    "excluded_false_positives": [],
                    "official_gap": None,
                    "elapsed_ms": 0,
                    "coverage": {"status": "failed", "evidence_level": "low", "note": str(exc)},
                    "errors": [str(exc)],
                }
        if args.output_file:
            with open(args.output_file, "w", encoding="utf-8") as handle:
                json.dump(max_coverage_result, handle, ensure_ascii=False, indent=2)
        if args.json:
            print(json.dumps(max_coverage_result, ensure_ascii=False, indent=2))
        else:
            print(render_maxcoverage_report(max_coverage_result))
        return 0

    try:
        steamwebapi_key = load_steamwebapi_key(args.steamwebapi_key, args.steamwebapi_key_file)
    except SteamQueryError as exc:
        steamwebapi_key = None
        protected_status = f"查询失败：{exc}"
        visible_status = f"查询失败：{exc}"

    api_key = args.api_key
    cookie_header = build_cookie_header(
        steam_cookie=args.cookie,
        steam_cookie_file=args.cookie_file,
        steam_login_secure=args.steam_login_secure,
        sessionid=args.sessionid,
    )

    protected_items: List[ProtectedItem] | None = None
    visible_items: List[InventoryItem] | None = None
    third_party_visible_items: List[InventoryItem] | None = None
    used_third_party_inventory = False
    coverage_status = ""
    protected_status = locals().get("protected_status", "ok")
    visible_status = locals().get("visible_status", "ok")

    try:
        if args.steamwebapi_json:
            third_party_payload = load_json_file(args.steamwebapi_json)
            third_party_result = steamwebapi_items_from_payload(third_party_payload, now=args.now)
            protected_items = third_party_result.protected_items
            third_party_visible_items = third_party_result.visible_items
            visible_items = third_party_visible_items
            used_third_party_inventory = True
        elif steamwebapi_key:
            fetch_result = fetch_steamwebapi_inventory(
                steamid,
                key=steamwebapi_key,
                language=args.language,
                timeout=args.timeout,
                mode=args.steamwebapi_mode,
                state=args.steamwebapi_state,
                no_cache=args.steamwebapi_no_cache,
                samples=args.steamwebapi_samples,
                trade_url=args.trade_url,
            )
            third_party_payload = fetch_result.payload
            counts = "/".join(str(value) for value in fetch_result.upstream_item_counts)
            if args.trade_url:
                coverage_status = f"实时请求已验证（active + no_cache=1）；已使用 trade_url 认证交易路径；上游样本数={counts}。"
            else:
                coverage_status = (
                    f"实时请求已验证（active + no_cache=1）；仅 SteamID 交易库存路径；上游样本数={counts}。"
                    "该路径返回的是 Steam 当前向此查询方展示的集合，不声明覆盖账号内全部受保护物品。"
                )
            third_party_result = steamwebapi_items_from_payload(third_party_payload, now=args.now)
            protected_items = third_party_result.protected_items
            third_party_visible_items = third_party_result.visible_items
            visible_items = third_party_visible_items
            used_third_party_inventory = True
        elif args.trade_history_json:
            trade_payloads = []
            for path in args.trade_history_json:
                payload = load_json_file(path)
                if isinstance(payload, Mapping) and isinstance(payload.get("response"), Mapping):
                    payload = payload["response"]
                if not isinstance(payload, Mapping):
                    raise SteamQueryError(f"交易历史文件格式异常：{path}")
                trade_payloads.append(payload)
            protected_items = protected_items_from_trade_payloads(trade_payloads, now=args.now)
        elif args.history_html:
            trade_payloads = []
            for path in args.history_html:
                try:
                    body = open(path, "r", encoding="utf-8").read()
                except OSError as exc:
                    raise SteamQueryError(f"无法读取历史 HTML 文件 {path}: {exc}") from exc
                trade_payloads.append(parse_history_html_payload(body, page_kind=args.history_kind if args.history_kind != "auto" else "tradehistory"))
            protected_items = protected_items_from_trade_payloads(trade_payloads, now=args.now)
        elif api_key:
            trade_payloads = fetch_trade_history(api_key, language=args.language, max_trades=args.max_trades, max_pages=args.trade_history_pages, timeout=args.timeout)
            protected_items = protected_items_from_trade_payloads(trade_payloads, now=args.now)
        elif cookie_header:
            trade_payloads = fetch_keyless_history_payloads(
                steamid,
                cookie=cookie_header,
                language=args.language,
                page_kind=args.history_kind,
                max_pages=args.trade_history_pages,
                timeout=args.timeout,
            )
            protected_items = protected_items_from_trade_payloads(trade_payloads, now=args.now)
        else:
            protected_status = "未配置 Steamwebapi key；请设置环境变量 STEAMWEBAPI_KEY，之后运行时只需要输入 steamid。"
    except SteamQueryError as exc:
        protected_status = f"查询失败：{exc}"

    if not args.skip_visible:
        try:
            if args.inventory_json:
                inventory_payload = load_json_file(args.inventory_json)
                if not isinstance(inventory_payload, Mapping):
                    raise SteamQueryError(f"库存文件格式异常：{args.inventory_json}")
                public_visible_items = inventory_items_from_payload(inventory_payload)
            else:
                inventory_payload = fetch_public_inventory(steamid, language=args.language, max_pages=args.inventory_pages, timeout=args.timeout)
                public_visible_items = inventory_items_from_payload(inventory_payload)
            if protected_items is not None:
                if used_third_party_inventory and third_party_visible_items is not None:
                    protected_items = merge_public_missing_third_party_items(protected_items, third_party_visible_items, public_visible_items)
                else:
                    protected_items = remove_public_visible_false_protected(protected_items, public_visible_items)
            visible_items = public_visible_items
        except SteamQueryError as exc:
            if visible_items is None:
                visible_status = f"查询失败：{exc}"
            elif used_third_party_inventory:
                visible_status = "ok"
    else:
        if used_third_party_inventory and protected_items is not None:
            try:
                public_payload = fetch_public_inventory(steamid, language=args.language, max_pages=args.inventory_pages, timeout=args.timeout)
                public_visible_items = inventory_items_from_payload(public_payload)
                if third_party_visible_items is not None:
                    protected_items = merge_public_missing_third_party_items(protected_items, third_party_visible_items, public_visible_items)
                else:
                    protected_items = remove_public_visible_false_protected(protected_items, public_visible_items)
            except SteamQueryError:
                pass
        visible_status = "已按 --skip-visible 跳过。"

    if args.json:
        print(json.dumps(build_json_result(steamid, protected_items, visible_items, protected_status, visible_status), ensure_ascii=False, indent=2))
    else:
        print(render_report(steamid, protected_items, visible_items, protected_status=protected_status, visible_status=visible_status, show_details=args.details, coverage_status=coverage_status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
