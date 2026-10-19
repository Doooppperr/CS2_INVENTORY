"""仅 Steam 域名使用专用 HTTP CONNECT 代理，不改全局路由。"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request

STEAM_HOSTS = {"steamcommunity.com", "www.steamcommunity.com", "api.steampowered.com"}


def steam_urlopen(request, *, timeout):
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    proxy = os.environ.get("CS2_STEAM_PROXY_URL", "").strip()
    if urllib.parse.urlsplit(url).hostname not in STEAM_HOSTS or not proxy:
        return urllib.request.urlopen(request, timeout=timeout)
    parsed = urllib.parse.urlsplit(proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OSError("Steam 专用代理地址格式无效，应使用 HTTP CONNECT 出口")
    handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
    opener = urllib.request.build_opener(handler)
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError:
        # 保留 Steam 的 403/429/5xx 与 Retry-After，供原有有界重试处理。
        raise
    except OSError:
        # 代理 URI 可能带凭据，禁止原始异常进入用户界面和任务日志。
        raise OSError("Steam 专用代理请求失败，请检查代理服务及出口连通性") from None
