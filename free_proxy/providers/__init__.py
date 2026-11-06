"""Provider 注册表。"""
from __future__ import annotations

from typing import Dict, Type

from .base import BaseProvider, FetchError, FetchResult, RawProxy
from .generic import GenericProvider
from .julang import JulangProvider
from .kuaidaili import KuaidailiProvider
from .pinzan import PinzanProvider
from .static import StaticProvider

_REGISTRY: Dict[str, Type[BaseProvider]] = {
    StaticProvider.type: StaticProvider,
    PinzanProvider.type: PinzanProvider,
    KuaidailiProvider.type: KuaidailiProvider,
    JulangProvider.type: JulangProvider,
    GenericProvider.type: GenericProvider,
}


def available_types() -> list:
    return sorted(_REGISTRY)


def create_provider(config: dict) -> BaseProvider:
    t = config.get("type") or config.get("name")
    cls = _REGISTRY.get(t)
    if cls is None:
        raise ValueError(f"未知代理商类型 '{t}'，可用: {available_types()}")
    return cls(config)


__all__ = [
    "BaseProvider",
    "FetchError",
    "FetchResult",
    "RawProxy",
    "create_provider",
    "available_types",
]