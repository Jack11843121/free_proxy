"""调度器：周期性从所有供应商拉取代理 + 周期性健康检查与淘汰。"""
from __future__ import annotations

import asyncio
import time
from typing import List

import aiohttp

from .checker import check_proxy
from .config import Config
from .logger import get_logger
from .model import Proxy
from .providers import FetchError, create_provider

log = get_logger("scheduler")


class Scheduler:
    def __init__(self, cfg: Config, pool, session: aiohttp.ClientSession):
        self.cfg = cfg
        self.pool = pool
        self.session = session
        self.providers = [create_provider(p) for p in cfg.enabled_providers()]
        self.provider_names = [p.name for p in self.providers]
        self.last_refresh: float = 0.0
        self.last_check: float = 0.0
        self._refresh_lock = asyncio.Lock()
        self._check_lock = asyncio.Lock()

    async def refresh_once(self) -> None:
        if self._refresh_lock.locked():
            return
        async with self._refresh_lock:
            for provider in self.providers:
                try:
                    result = await provider.fetch(self.session)
                except FetchError as e:
                    log.warning("供应商 [%s] 拉取失败: %s", provider.name, e)
                    continue
                except Exception as e:  # noqa: BLE001
                    log.warning("供应商 [%s] 异常: %s", provider.name, e)
                    continue

                added = 0
                for raw in result.proxies:
                    expire = raw.expire_at()
                    if expire <= 0:
                        expire = time.time() + self.cfg.pool.ttl
                    self.pool.add(Proxy(
                        ip=raw.ip, port=raw.port, provider=provider.name,
                        scheme=raw.scheme,
                        user=raw.user, password=raw.password, region=raw.region,
                        expire_at=expire,
                    ))
                    added += 1
                log.info("供应商 [%s] 返回 %d 条，入池 %d", provider.name, len(result.proxies), added)
            self.last_refresh = time.time()

    async def check_once(self) -> None:
        if self._check_lock.locked():
            return
        async with self._check_lock:
            snap: List[Proxy] = self.pool.snapshot()
            if not snap:
                self.last_check = time.time()
                return
            now = time.time()
            healthy: List[Proxy] = []
            alive_cnt = 0
            for p in snap:
                alive, lat = await check_proxy(
                    self.session, p, self.cfg.pool.check_url, self.cfg.pool.check_timeout
                )
                p.last_check = now
                if alive:
                    p.alive = True
                    p.latency_ms = lat
                    p.success += 1
                    p.fail = 0
                    alive_cnt += 1
                else:
                    p.alive = False
                    p.fail += 1

                expired = p.is_expired(now)
                if not expired and (p.alive or p.fail < self.cfg.pool.max_fail):
                    healthy.append(p)
                else:
                    reason = "过期" if expired else f"连续失败 {p.fail} 次"
                    log.info("淘汰 %s（%s）", p.key, reason)

            self.pool.rebuild(healthy)
            self.last_check = now
            log.info("健康检查完成：存活 %d / %d，池内 %d", alive_cnt, len(snap), len(healthy))

    async def run(self) -> None:
        log.info(
            "调度器启动：refresh=%.0fs check=%.0fs 供应商=%d",
            self.cfg.pool.refresh_interval, self.cfg.pool.check_interval, len(self.providers),
        )
        await self._safe(self.refresh_once)
        await self._safe(self.check_once)

        async def refresh_loop() -> None:
            while True:
                await asyncio.sleep(self.cfg.pool.refresh_interval)
                await self._safe(self.refresh_once)

        async def check_loop() -> None:
            while True:
                await asyncio.sleep(self.cfg.pool.check_interval)
                await self._safe(self.check_once)

        await asyncio.gather(refresh_loop(), check_loop())

    async def _safe(self, coro_func) -> None:
        try:
            await coro_func()
        except Exception as e:  # noqa: BLE001
            log.exception("调度任务出错: %s", e)