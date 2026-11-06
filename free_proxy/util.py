"""通用小工具。"""
from __future__ import annotations

from typing import Tuple


def parse_listen(addr: str) -> Tuple[str, int]:
    """'host:port' -> (host, port)。'0.0.0.0:8899' / ':8899' / '127.0.0.1:8891'。"""
    if not addr or ":" not in addr:
        raise ValueError(f"监听地址格式应为 host:port，实际: {addr!r}")
    host, sep, port = addr.rpartition(":")
    return (host or "0.0.0.0"), int(port)