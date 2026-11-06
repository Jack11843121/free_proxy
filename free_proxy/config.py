"""配置加载（config.yaml）。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

import os
import re

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(v):
    """把字符串里的 ${VAR} 替换为环境变量值（分辨率密信息不硬编码进配置）。"""
    if isinstance(v, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), v)
    if isinstance(v, dict):
        return {k: _resolve_env(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve_env(x) for x in v]
    return v


@dataclass
class ServerConfig:
    listen: str = "0.0.0.0:8899"
    backlog: int = 256
    connect_timeout: float = 10.0


@dataclass
class ControlConfig:
    listen: str = "127.0.0.1:8891"
    token: str = ""  # 为空则控制面不鉴权（仅建议内网监听时留空）


@dataclass
class PoolConfig:
    strategy: str = "weighted"  # round_robin | random | fastest | weighted
    max_size: int = 500
    min_size: int = 50
    ttl: float = 60.0  # 供应商未返回过期时间时，代理默认存活秒数
    check_interval: float = 30.0
    check_timeout: float = 10.0
    check_url: str = "https://www.baidu.com"  # 健康检查目标（建议国内可达）
    max_fail: int = 3
    refresh_interval: float = 60.0
    cooldown: float = 5.0  # 判死后冷却秒数，之后才允许重新入池


@dataclass
class StoreConfig:
    backend: str = "memory"  # memory | redis
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_key: str = "free_proxy:pool"


T = TypeVar("T")


def _build(cls: Type[T], data: Dict[str, Any]) -> T:
    """用 section 字典构建 dataclass：未知键直接报错，缺失键用默认值。"""
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"配置 [{cls.__name__}] 包含未知字段: {sorted(unknown)}（可用字段: {sorted(known)}）"
        )
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    providers: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        data: Dict[str, Any] = {}
        if path and Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        cfg = Config()
        if isinstance(data.get("server"), dict):
            cfg.server = _build(ServerConfig, data["server"])
        if isinstance(data.get("control"), dict):
            cfg.control = _build(ControlConfig, data["control"])
        if isinstance(data.get("pool"), dict):
            cfg.pool = _build(PoolConfig, data["pool"])
        if isinstance(data.get("store"), dict):
            cfg.store = _build(StoreConfig, data["store"])
        cfg.providers = [_resolve_env(dict(p)) for p in (data.get("providers") or [])]
        return cfg

    def enabled_providers(self) -> List[Dict[str, Any]]:
        return [p for p in self.providers if p.get("enabled", True)]