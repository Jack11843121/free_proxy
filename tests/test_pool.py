"""代理池单元测试。"""
import time

from free_proxy.config import PoolConfig
from free_proxy.model import Proxy
from free_proxy.pool import MemoryPool


def make_pool(**kw):
    return MemoryPool(PoolConfig(**kw))


def test_add_and_pick():
    pool = make_pool(max_size=10)
    pool.add(Proxy(ip="1.1.1.1", port=80))
    p = pool.pick()
    assert p is not None and p.key == "1.1.1.1:80"
    assert pool.count() == 1


def test_dedupe_by_key():
    pool = make_pool()
    pool.add(Proxy(ip="1.1.1.1", port=80))
    pool.add(Proxy(ip="1.1.1.1", port=80))
    assert pool.count() == 1


def test_pick_skips_dead():
    pool = make_pool()
    dead = Proxy(ip="1.1.1.1", port=80)
    dead.alive = False
    pool.add(dead)
    pool.add(Proxy(ip="2.2.2.2", port=80))
    assert pool.pick().key == "2.2.2.2:80"


def test_pick_skips_expired():
    pool = make_pool()
    pool.add(Proxy(ip="1.1.1.1", port=80, expire_at=time.time() - 10))
    assert pool.pick() is None


def test_pick_returns_none_when_empty():
    assert make_pool().pick() is None


def test_trim_removes_dead_first():
    pool = make_pool(max_size=1)
    dead = Proxy(ip="1.1.1.1", port=80)
    dead.alive = False
    pool.add(dead)
    pool.add(Proxy(ip="2.2.2.2", port=80))
    assert pool.count() == 1
    assert pool.snapshot()[0].key == "2.2.2.2:80"


def test_rebuild_and_stats():
    pool = make_pool()
    pool.rebuild([Proxy(ip="1.1.1.1", port=80, provider="pz")])
    st = pool.stats()
    assert st["total"] == 1
    assert st["alive"] == 1
    assert st["by_provider"] == {"pz": 1}


def test_round_robin_cycles():
    pool = make_pool(strategy="round_robin")
    pool.add(Proxy(ip="1.1.1.1", port=80))
    pool.add(Proxy(ip="2.2.2.2", port=80))
    keys = [pool.pick().key for _ in range(4)]
    assert keys == ["1.1.1.1:80", "2.2.2.2:80", "1.1.1.1:80", "2.2.2.2:80"]