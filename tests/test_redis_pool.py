"""Redis 共享代理池（RedisPool）逻辑单测：用内存版 FakeRedis 验证轮转/重建/淘汰。"""
import time

from free_proxy.config import PoolConfig, StoreConfig
from free_proxy.model import Proxy
from free_proxy.pool import RedisPool


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.cmds = []

    def delete(self, key):
        self.cmds.append(("delete", key))
        return self

    def rpush(self, key, val):
        self.cmds.append(("rpush", key, val))
        return self

    def execute(self):
        for c in self.cmds:
            getattr(self.redis, c[0])(*c[1:])


class FakeRedis:
    """只实现 RedisPool 用到的那几个列表命令。"""

    def __init__(self):
        self.lists = {}

    def ping(self):
        return True

    def rpush(self, key, val):
        self.lists.setdefault(key, []).append(val)

    def delete(self, key):
        self.lists.pop(key, None)

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, a, b):
        lst = self.lists.get(key, [])
        # Redis 的 -1 表示“到末尾”，对应 Python 切片是 None
        return lst[a: (b if b >= 0 else None)]

    def rpoplpush(self, key, dst):
        lst = self.lists.setdefault(key, [])
        if not lst:
            return None
        v = lst.pop()  # 右侧弹出
        self.lists.setdefault(dst, []).insert(0, v)  # 左侧推入
        return v

    def lrem(self, key, count, val):
        lst = self.lists.get(key, [])
        kept, removed = [], 0
        for x in lst:
            if removed < count and x == val:
                removed += 1
            else:
                kept.append(x)
        self.lists[key] = kept
        return removed

    def pipeline(self):
        return FakePipeline(self)


def _pool():
    p = RedisPool(PoolConfig(max_size=10), StoreConfig(redis_key="test:pool"))
    p._redis = FakeRedis()
    return p


def test_add_count_snapshot():
    p = _pool()
    p.add(Proxy(ip="1.1.1.1", port=80, provider="a"))
    p.add(Proxy(ip="2.2.2.2", port=80, provider="b"))
    assert p.count() == 2
    keys = sorted(x.key for x in p.snapshot())
    assert keys == ["1.1.1.1:80", "2.2.2.2:80"]


def test_pick_rotation():
    p = _pool()
    p.add(Proxy(ip="1.1.1.1", port=80))
    p.add(Proxy(ip="2.2.2.2", port=80))
    seen = {p.pick().key, p.pick().key}
    assert seen == {"1.1.1.1:80", "2.2.2.2:80"}


def test_rebuild_and_stats():
    p = _pool()
    p.rebuild([Proxy(ip="3.3.3.3", port=80, provider="pz")])
    assert p.count() == 1
    assert p.snapshot()[0].key == "3.3.3.3:80"
    st = p.stats()
    assert st["backend"] == "redis" and st["total"] == 1 and st["alive"] == 1


def test_pick_drops_expired():
    p = _pool()
    p.rebuild([Proxy(ip="9.9.9.9", port=80, expire_at=time.time() - 10)])
    assert p.pick() is None  # 过期 → 弹出后 LREM 丢弃
    assert p.count() == 0