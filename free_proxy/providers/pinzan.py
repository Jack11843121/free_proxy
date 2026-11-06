"""品赞代理（ipzan.com）适配器。

参考官方后台「生成提取链接」：https://www.ipzan.com/user/extractapi
提取接口与参数来自开源实现交叉印证，接入前建议在后台核对最终 URL。
"""
from __future__ import annotations

import json

import aiohttp

from .base import BaseProvider, FetchError, FetchResult, RawProxy, http_get

DEFAULT_URL = "https://service.ipzan.com/core-extract"


class PinzanProvider(BaseProvider):
    type = "pinzan"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        params = {
            "no": cfg.get("no", ""),
            "secret": cfg.get("secret", ""),
            "num": cfg.get("num", 5),
            "format": "json",
        }
        for k in ("mode", "protocol", "pool", "minute", "area"):
            if cfg.get(k) is not None:
                params[k] = cfg[k]

        url = cfg.get("api_url") or DEFAULT_URL
        text = await http_get(session, url, params=params)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise FetchError(f"品赞返回非 JSON: {text[:200]}") from e

        if data.get("code") != 0:
            raise FetchError(f"品赞错误 code={data.get('code')} msg={data.get('msg')} 原始={text[:200]}")

        proxies = []
        for item in (data.get("data") or {}).get("list") or []:
            ip = item.get("ip") or item.get("host")
            port = item.get("port")
            if ip and port:
                proxies.append(RawProxy(ip=str(ip), port=int(port)))
                if len(proxies) >= int(cfg.get("num", 5)):
                    break
        return FetchResult(provider=self.name, proxies=proxies, raw=text)