"""健康检查：验证某个上游代理是否可用并测出延迟。"""
from __future__ import annotations

import time
from typing import Tuple

import aiohttp

from .logger import get_logger
from .model import Proxy

log = get_logger("checker")


async def check_proxy(session: aiohttp.ClientSession, proxy: Proxy,
                      url: str, timeout: float) -> Tuple[bool, float]:
    """通过该代理访问检测目标，返回 (是否存活, 延迟毫秒)。"""
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