"""通用模板 Provider：不改代码，仅凭 YAML 配置接入任意 HTTP 提取类代理商。

满足“本质 = URL + 签名 + 列表字段”的代理商都能用；只有签名算法极其特殊时
才需要像品赞/快代理/巨量那样新增一个适配器文件。
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import aiohttp

from .base import (
    BaseProvider,
    FetchError,
    FetchResult,
    RawProxy,
    dig,
    http_get,
    parse_entry,
    sign_hmacsha1,
    sign_md5,
)


def _render(url: str, ctx: dict) -> str:
    out = url
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
        out = out.replace("{{" + k + "}}", str(v))
    return out


class GenericProvider(BaseProvider):
    type = "generic"

    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        cfg = self.config
        method = str(cfg.get("method", "GET")).upper()
        url = cfg.get("url", "")

        # 参数：params 作为基础，叠加顶层凭证字段（便于引用）
        ctx = dict(cfg.get("params") or {})
        for k in ("secret", "id", "key", "trade_no", "secret_id", "no", "num"):
            if k in cfg:
                ctx.setdefault(k, cfg[k])
        url = _render(url, ctx)

        params = {k: v for k, v in ctx.items() if isinstance(v, (str, int, float))}

        # 可选签名
        sign_cfg = cfg.get("sign") or {}
        sign_type = sign_cfg.get("type", "none")
        sign_param = sign_cfg.get("param", "sign")
        if sign_type == "md5":
            params[sign_param] = sign_md5(sign_cfg.get("key", ""), params, sign_cfg.get("suffix", "&key="))
        elif sign_type == "hmacsha1":
            path = urlparse(url).path
            params[sign_param] = sign_hmacsha1(sign_cfg.get("key", ""), method, path, params)
        elif sign_type == "token":
            params[sign_param] = sign_cfg.get("key", "")
        # none / query：凭证已在 params 里，无需额外处理

        text = await http_get(session, url, params=params, method=method,
                              headers=cfg.get("headers"))

        proxies = []
        resp_cfg = cfg.get("response") or {}
        fmt = resp_cfg.get("format", "json").lower()
        if fmt == "text":
            entries = text.strip().split(resp_cfg.get("text_sep") or "\n")
            for e in entries:
                p = parse_entry(str(e))
                if p:
                    proxies.append(p)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise FetchError(f"{self.name} 返回非 JSON: {text[:200]}") from e
            # 成功判定
            ok_path = resp_cfg.get("success_path")
            if ok_path:
                val = dig(ok_path, data)
                if val != (resp_cfg.get("success_equals") or val):
                    raise FetchError(f"{self.name} 返回错误 原始={text[:200]}")
            list_path = resp_cfg.get("list_path", "data.proxy_list")
            raw_list = dig(list_path, data) or []
            if isinstance(raw_list, dict):  # 品赞风格：data.list 是 list，少数是 dict
                raw_list = raw_list.get("list") or raw_list.get("data") or []
            for item in raw_list:
                if isinstance(item, str):
                    p = parse_entry(item)
                elif isinstance(item, dict):
                    ip = item.get("ip") or item.get("host")
                    port = item.get("port")
                    if ip and port:
                        p = RawProxy(ip=str(ip), port=int(port),
                                     user=item.get("user"), password=item.get("password"))
                    else:
                        p = None
                else:
                    p = None
                if p:
                    proxies.append(p)

        scheme = cfg.get("scheme", "http") or "http"
        for p in proxies:
            p.scheme = scheme
        return FetchResult(provider=self.name, proxies=proxies, raw=text)