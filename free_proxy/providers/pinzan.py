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
        # YAML 1.1 会把未加引号的 `no:` 解析成布尔 False（键名变成 False 而不是字符串 "no"），这里做兼容
        order_no = cfg.get("no") or cfg.get(False) or ""
        params = {
            "no": str(order_no),
            "secret": cfg.get("secret", ""),
            "num": cfg.get("num", 5),
            "format": "json",
        }
        for k in ("mode", "protocol", "pool", "minute", "area", "repeat"):
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

        d = data.get("data") or {}
        # protocol: 1=HTTP 2=SOCKS5
        scheme = "socks5" if str(cfg.get("protocol", 1)) == "2" else "http"
        # auth 模式：账号密码可能出现在顶层，或每条 IP 里，或用配置里的固定账密
        top_user = d.get("user") or d.get("username") or d.get("account")
        top_pwd = d.get("password") or d.get("pwd")

        proxies = []
        for item in d.get("list") or []:
            ip = item.get("ip") or item.get("host")
            port = item.get("port")
            if ip and port:
                proxies.append(RawProxy(
                    ip=str(ip),
                    port=int(port),
                    scheme=scheme,
                    user=item.get("user") or item.get("username") or top_user or cfg.get("auth_user"),
                    password=item.get("password") or item.get("pwd") or top_pwd or cfg.get("auth_password"),
                ))
                if len(proxies) >= int(cfg.get("num", 5)):
                    break
        return FetchResult(provider=self.name, proxies=proxies, raw=text)