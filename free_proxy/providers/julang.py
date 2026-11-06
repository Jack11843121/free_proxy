"""巨量代理（juliangip.com）适配器。

官方文档：https://www.juliangip.com/help/api/dynamic/
sign = md5( 按参数名 ASCII 序的 k=v&... + "&key=" + APIKey)，32 位小写；key 不进入请求参数。
"""
from __future__ import annotations

import json

import aiohttp

from .base import BaseProvider, FetchError, FetchResult, RawProxy, http_get, parse_entry, sign_md5

DEFAULT_URL = "https://v2.api.juliangip.com/dynamic/getips"


class JulangProvider(BaseProvider):
    type = "julang"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        url = cfg.get("api_url") or DEFAULT_URL
        params = {
            "trade_no": cfg.get("trade_no", ""),
            "num": cfg.get("num", 5),
            "result_type": "json",
        }
        for k in ("pt", "split", "city_name", "city_code", "ip_remain", "area", "isp", "filter", "auth_info"):
            if cfg.get(k) is not None:
                params[k] = cfg[k]

        # 签名前 params 不含 sign
        params["sign"] = sign_md5(cfg.get("api_key", ""), params)

        text = await http_get(session, url, params=params)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise FetchError(f"巨量返回非 JSON: {text[:200]}") from e

        if data.get("code") != 200:
            raise FetchError(f"巨量错误 code={data.get('code')} msg={data.get('msg')} 原始={text[:200]}")

        proxies = []
        # pt: 1=HTTP 2=SOCK(SOCKS5)
        scheme = "socks5" if str(cfg.get("pt", 1)) == "2" else "http"
        for e in (data.get("data") or {}).get("proxy_list") or []:
            p = parse_entry(str(e))
            if p:
                p.scheme = scheme
                proxies.append(p)
        return FetchResult(provider=self.name, proxies=proxies, raw=text)