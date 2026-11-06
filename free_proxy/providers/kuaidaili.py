"""快代理（kuaidaili.com）适配器。

官方文档：https://www.kuaidaili.com/doc/api/getdps/
支持 sign_type=token（默认，signature 直接填 secret_token）与 hmacsha1（需 secret_key）。
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import aiohttp

from .base import BaseProvider, FetchError, FetchResult, RawProxy, http_get, parse_entry, sign_hmacsha1

DEFAULT_URL = "https://dps.kdlapi.com/api/getdps"


class KuaidailiProvider(BaseProvider):
    type = "kuaidaili"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        url = cfg.get("api_url") or DEFAULT_URL
        params = {
            "secret_id": cfg.get("secret_id", ""),
            "num": cfg.get("num", 5),
            "format": "json",
        }

        sign_type = cfg.get("sign_type", "token")
        if sign_type == "hmacsha1":
            params["sign_type"] = "hmacsha1"
            params["timestamp"] = str(int(time.time()))
            if cfg.get("nonce") is not None:
                params["nonce"] = str(cfg["nonce"])
            path = urlparse(url).path
            params["signature"] = sign_hmacsha1(
                cfg.get("secret_key", ""), "GET", path, params
            )
        else:
            params["sign_type"] = "token"
            params["signature"] = cfg.get("secret_token") or cfg.get("secret_key") or ""

        for k in ("area", "area_ex", "dedup", "sep", "carrier", "f_et", "f_auth", "f_loc", "f_citycode"):
            if cfg.get(k) is not None:
                params[k] = cfg[k]

        text = await http_get(session, url, params=params)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise FetchError(f"快代理返回非 JSON: {text[:200]}") from e

        if data.get("code") != 0:
            raise FetchError(f"快代理错误 code={data.get('code')} msg={data.get('msg')} 原始={text[:200]}")

        proxies = []
        for e in (data.get("data") or {}).get("proxy_list") or []:
            p = parse_entry(str(e))
            if p:
                proxies.append(p)
        return FetchResult(provider=self.name, proxies=proxies, raw=text)