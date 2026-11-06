"""花生代理（huashengdaili.com / ip.huashengdaili.com）适配器。

后台「提取IP → 获取API链接」生成完整 URL（含用户专属标识 + 白名单鉴权，无签名）。
字段与其它家不同：主机字段是 `sever`（不是 ip），密码字段是 `pw`，成功码 status=="0"。
数量（count）与返回类型由生成链接里的参数决定，本适配器只做解析与账密透传。
"""
from __future__ import annotations

import json

import aiohttp

from .base import BaseProvider, FetchError, FetchResult, RawProxy, http_get, parse_entry


class HuashengProvider(BaseProvider):
    type = "huasheng"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        url = cfg.get("api_url") or ""
        if not url:
            raise FetchError("花生代理需在 config 里填 api_url（后台「获取API链接」生成的完整链接）")

        text = await http_get(session, url)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict) and "list" in data:
            if str(data.get("status", "")) != "0":
                raise FetchError(f"花生错误 原始={text[:200]}")
            proxies = []
            for item in data.get("list") or []:
                ip = item.get("sever")
                port = item.get("port")
                if ip and port:
                    proxies.append(RawProxy(
                        ip=str(ip), port=int(port),
                        user=item.get("user"), password=item.get("pw"),
                    ))
            return FetchResult(provider=self.name, proxies=proxies, raw=text)

        # 文本回退：一行一个 ip:port（或 ip:port:user:pw）
        proxies = [p for p in (parse_entry(e) for e in text.strip().splitlines()) if p]
        if not proxies:
            raise FetchError(f"花生返回无法解析: {text[:200]}")
        return FetchResult(provider=self.name, proxies=proxies, raw=text)