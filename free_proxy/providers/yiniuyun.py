"""16云 / 亿牛云（16yun.cn）适配器。

开发手册：https://www.16yun.cn/help/plain/dev-manual/api-proxy-guide/
鉴权为链接内置静态密钥（s + u）+ IP 白名单，无哈希签名。
返回 JSON：{"status": true, "proxy": [{"ip": "...", "port": ..., ...}]}
"""
from __future__ import annotations

import json

import aiohttp

from .base import BaseProvider, FetchError, FetchResult, RawProxy, http_get


class YiniuyunProvider(BaseProvider):
    type = "yiniuyun"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        order_id = cfg.get("order_id", "")
        url = cfg.get("api_url") or f"http://ip.16yun.cn:817/myip/pl/{order_id}/"
        params = {
            "s": cfg.get("sign", ""),
            "u": cfg.get("user", ""),
            "format": "json",
        }
        num = cfg.get("num") or cfg.get("count")
        if num:
            params["count"] = num

        text = await http_get(session, url, params=params)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise FetchError(f"16云返回非 JSON: {text[:200]}") from e

        if data.get("status") is not True:
            raise FetchError(f"16云错误 原始={text[:200]}")

        proxies = []
        for item in data.get("proxy") or []:
            ip = item.get("ip")
            port = item.get("port")
            if ip and port:
                proxies.append(RawProxy(ip=str(ip), port=int(port)))
        return FetchResult(provider=self.name, proxies=proxies, raw=text)