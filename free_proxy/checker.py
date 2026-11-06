"""健康检查：验证某个上游代理是否可用并测出延迟。

支持 HTTP 与 SOCKS5 两种上游协议：
- HTTP 上游：走 aiohttp 的 proxy 参数发出真实请求。
- SOCKS5 上游：手工 SOCKS5 CONNECT 到检测目标（能连上即视为存活）。
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import Tuple

import aiohttp

from .logger import get_logger
from .model import Proxy
from .tunnel import open_socks5_tunnel

log = get_logger("checker")


async def check_proxy(session: aiohttp.ClientSession, proxy: Proxy,
                      url: str, timeout: float) -> Tuple[bool, float]:
    """通过该代理访问检测目标，返回 (是否存活, 延迟毫秒)。"""
    if proxy.scheme == "socks5":
        return await _check_socks5(proxy, url, timeout)
    return await _check_http(session, proxy, url, timeout)


async def _check_http(session: aiohttp.ClientSession, proxy: Proxy,
                      url: str, timeout: float) -> Tuple[bool, float]:
    start = time.monotonic()
    try:
        auth = aiohttp.BasicAuth(proxy.user, proxy.password) if proxy.user else None
        async with session.get(
            url,
            proxy=proxy.url,
            proxy_auth=auth,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False,
        ) as resp:
            await resp.read()
            latency = (time.monotonic() - start) * 1000.0
            ok = 200 <= resp.status < 400
            return ok, latency
    except Exception as e:  # 连接失败、超时、DNS、TLS、代理拒绝等
        log.debug("检查 %s 失败: %s", proxy.key, type(e).__name__)
        return False, 0.0


async def _check_socks5(proxy: Proxy, url: str, timeout: float) -> Tuple[bool, float]:
    """SOCKS5 上游：CONNECT 到检测目标的主机:端口，能建立隧道即存活。"""
    u = urllib.parse.urlsplit(url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "https" else 80)
    if not host:
        return False, 0.0
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            open_socks5_tunnel(proxy, host, port, timeout), timeout=timeout
        )
        writer.close()
        latency = (time.monotonic() - start) * 1000.0
        return True, latency
    except Exception as e:  # noqa: BLE001
        log.debug("SOCKS5 上游检查 %s 失败: %s", proxy.key, type(e).__name__)
        return False, 0.0