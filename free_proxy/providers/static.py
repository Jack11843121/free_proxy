"""静态/自有代理来源：直接把固定 ip:port 加入池（也用于隧道代理网关）。"""
from __future__ import annotations

from typing import List

from .base import BaseProvider, FetchResult, RawProxy, parse_entry


class StaticProvider(BaseProvider):
    type = "static"

    async def fetch(self, session=None) -> FetchResult:  # noqa: ARG002
        proxies: List[RawProxy] = []
        scheme = self.config.get("scheme", "http") or "http"
        entries = self.config.get("proxies") or []
        for e in entries:
            p = parse_entry(str(e))
            if p:
                p.scheme = scheme
                proxies.append(p)
        user = self.config.get("user")
        pwd = self.config.get("password")
        if user and proxies:
            for p in proxies:
                p.user = user
                p.password = pwd
        return FetchResult(provider=self.name, proxies=proxies, raw="")