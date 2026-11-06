"""代理与相关数据模型。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class Proxy:
    """一个上游代理 IP。"""

    ip: str
    port: int
    provider: str = ""
    user: Optional[str] = None
    password: Optional[str] = None
    region: Optional[str] = None
    expire_at: float = 0.0  # 过期 unix 时间戳；0 表示未知（用池级 ttl 兜底）
    fetched_at: float = field(default_factory=time.time)

    # —— 运行时健康状态 ——
    alive: bool = True
    latency_ms: float = 0.0
    success: int = 0
    fail: int = 0
    last_used: float = 0.0
    last_check: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.ip}:{self.port}"

    @property
    def auth(self) -> Optional[Tuple[str, str]]:
        """返回 (user, password) 用于 Basic 鉴权；无鉴权时返回 None。"""
        if self.user:
            return (self.user, self.password or "")
        return None

    @property
    def url(self) -> str:
        return f"http://{self.ip}:{self.port}"

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expire_at <= 0:
            return False
        return (now if now is not None else time.time()) >= self.expire_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "port": self.port,
            "provider": self.provider,
            "user": self.user,
            "region": self.region,
            "expire_at": self.expire_at,
            "alive": self.alive,
            "latency_ms": round(self.latency_ms, 1),
            "success": self.success,
            "fail": self.fail,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Proxy":
        return Proxy(
            ip=str(d["ip"]),
            port=int(d["port"]),
            provider=d.get("provider", ""),
            user=d.get("user"),
            password=d.get("password"),
            region=d.get("region"),
            expire_at=float(d.get("expire_at", 0) or 0),
        )