"""代理池：内存版（默认）与 Redis 共享版。"""
from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional

from .config import PoolConfig, StoreConfig
from .logger import get_logger
from .model import Proxy

log = get_logger("pool")


def _dump(p: Proxy) -> Dict[str, Any]:
    d = p.to_dict()
    d["password"] = p.password  # 仅内部共享用，控制面 API 不会暴露
    d["fetched_at"] = p.fetched_at
    return d


def _load(d: Dict[str, Any]) -> Proxy:
    return Proxy.from_dict(d)


class MemoryPool:
    """单进程内存池。默认策略见 pool.strategy 配置。"""

    def __init__(self, cfg: PoolConfig):
        self.cfg = cfg
        self._pool: Dict[str, Proxy] = {}
        self._rr = 0

    # -- 写入 --
    def add(self, p: Proxy) -> None:
        existing = self._pool.get(p.key)
        if existing is not None:
            # 重新拉取：刷新来源/过期时间，但保留健康统计（除非之前被判死）
            existing.provider = p.provider
            existing.expire_at = p.expire_at
            existing.fetched_at = p.fetched_at
            existing.user = p.user or existing.user
            existing.password = p.password or existing.password
        else:
            self._pool[p.key] = p
        self._trim()

    def remove(self, key: str) -> None:
        self._pool.pop(key, None)

    def clear(self) -> None:
        self._pool.clear()

    def rebuild(self, proxies: List[Proxy]) -> None:
        self._pool = {p.key: p for p in proxies}
        self._trim()

    # -- 读取 --
    def get(self, key: str) -> Optional[Proxy]:
        return self._pool.get(key)

    def snapshot(self) -> List[Proxy]:
        return list(self._pool.values())

    def count(self) -> int:
        return len(self._pool)

    def alive_count(self) -> int:
        now = time.time()
        return sum(1 for p in self._pool.values() if p.alive and not p.is_expired(now))

    def _candidates(self) -> List[Proxy]:
        now = time.time()
        return [p for p in self._pool.values() if p.alive and not p.is_expired(now)]

    def pick(self) -> Optional[Proxy]:
        cands = self._candidates()
        if not cands:
            return None
        strat = self.cfg.strategy
        if strat == "round_robin":
            self._rr %= len(cands)
            p = cands[self._rr]
            self._rr += 1
            return p
        if strat == "fastest":
            checked = [p for p in cands if p.latency_ms > 0]
            return min(checked, key=lambda p: p.latency_ms) if checked else random.choice(cands)
        if strat == "weighted":
            weights = [1.0 / (1.0 + p.fail + p.latency_ms / 1000.0) for p in cands]
            return random.choices(cands, weights=weights, k=1)[0]
        return random.choice(cands)

    def stats(self) -> Dict[str, Any]:
        now = time.time()
        total = self.count()
        alive = self.alive_count()
        by_provider: Dict[str, int] = {}
        for p in self._pool.values():
            by_provider[p.provider] = by_provider.get(p.provider, 0) + 1
        return {
            "backend": "memory",
            "total": total,
            "alive": alive,
            "expired": total - alive,
            "strategy": self.cfg.strategy,
            "by_provider": by_provider,
            "now": now,
        }

    # -- 私有 --
    def _trim(self) -> None:
        max_size = self.cfg.max_size
        if self.count() <= max_size:
            return
        # 先清死/过期
        now = time.time()
        for k in [k for k, p in self._pool.items() if not p.alive or p.is_expired(now)]:
            if self.count() <= max_size:
                return
            del self._pool[k]
        # 再按 fail 降序清理
        while self.count() > max_size:
            k = max(self._pool, key=lambda k: (self._pool[k].fail, -self._pool[k].fetched_at))
            del self._pool[k]


class RedisPool:
    """Redis 共享版代理池（多进程 / 多服务器共享同一份代理列表）。

    列表按 `RPOPLPUSH` 环形轮转，任何一台服务器的 pick 都会公平取到下一个 IP。
    """

    def __init__(self, cfg: PoolConfig, store_cfg: StoreConfig):
        self.cfg = cfg
        self.store_cfg = store_cfg
        self.key = store_cfg.redis_key
        self._redis = None

    def _r(self):
        if self._redis is None:
            import redis  # 惰性导入：不用 Redis 就不需要这个依赖

            self._redis = redis.Redis.from_url(self.store_cfg.redis_url, decode_responses=True)
            self._redis.ping()
        return self._redis

    def add(self, p: Proxy) -> None:
        self._r().rpush(self.key, json.dumps(_dump(p)))

    def clear(self) -> None:
        self._r().delete(self.key)

    def rebuild(self, proxies: List[Proxy]) -> None:
        r = self._r()
        pipe = r.pipeline()
        pipe.delete(self.key)
        for p in proxies:
            pipe.rpush(self.key, json.dumps(_dump(p)))
        pipe.execute()

    def snapshot(self) -> List[Proxy]:
        blobs = self._r().lrange(self.key, 0, -1)
        return [_load(json.loads(b)) for b in blobs]

    def count(self) -> int:
        return int(self._r().llen(self.key))

    def pick(self) -> Optional[Proxy]:
        r = self._r()
        blob = r.rpoplpush(self.key, self.key)  # 原子轮转
        if blob is None:
            return None
        p = _load(json.loads(blob))
        now = time.time()
        if not p.alive or p.is_expired(now):
            r.lrem(self.key, 1, blob)  # 丢弃这条，下一条继续
            return self.pick()
        return p

    def stats(self) -> Dict[str, Any]:
        proxies = self.snapshot()
        total = len(proxies)
        alive = sum(1 for p in proxies if p.alive)
        by_provider: Dict[str, int] = {}
        for p in proxies:
            by_provider[p.provider] = by_provider.get(p.provider, 0) + 1
        return {
            "backend": "redis",
            "total": total,
            "alive": alive,
            "expired": total - alive,
            "strategy": "round_robin (shared)",
            "by_provider": by_provider,
            "now": time.time(),
            "redis_key": self.key,
        }


def create_pool(pool_cfg: PoolConfig, store_cfg: StoreConfig) -> Any:
    if store_cfg.backend == "redis":
        log.info("使用 Redis 共享代理池: %s", store_cfg.redis_url)
        return RedisPool(pool_cfg, store_cfg)
    return MemoryPool(pool_cfg)