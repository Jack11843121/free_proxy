"""Provider 基类与共享工具：签名算法、响应解析。"""
from __future__ import annotations

import abc
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp


@dataclass
class RawProxy:
    """上游供应商返回的一条代理（尚未进入池）。"""

    ip: str
    port: int
    scheme: str = "http"  # http | socks5
    user: Optional[str] = None
    password: Optional[str] = None
    region: Optional[str] = None
    remain_seconds: Optional[int] = None  # 供应商返回的剩余可用时长

    def expire_at(self, now: Optional[float] = None) -> float:
        if self.remain_seconds:
            return (now if now is not None else time.time()) + self.remain_seconds
        return 0.0


@dataclass
class FetchResult:
    provider: str
    proxies: List[RawProxy] = field(default_factory=list)
    raw: str = ""


class FetchError(Exception):
    """供应商拉取失败（网络 / 鉴权 / 白名单 / 额度）。"""


# --------------------------------------------------------------------------- #
# 签名算法（快代理 HMAC-SHA1 / 巨量 MD5 / 品赞直传）
# --------------------------------------------------------------------------- #
def _sorted_query(params: Dict[str, Any]) -> str:
    """按参数名 ASCII 字典序拼成 k1=v1&k2=v2（值为未 urlencode 的原始值）。"""
    items = sorted((str(k), str(v)) for k, v in params.items() if v not in (None, ""))
    return "&".join(f"{k}={v}" for k, v in items)


def sign_hmacsha1(key: str, method: str, path: str, params: Dict[str, Any]) -> str:
    """快代理 hmacsha1：signature = base64(HMAC-SHA1(key, METHOD+path+'?'+sorted_query))"""
    text = f"{method.upper()}{path}?{_sorted_query(params)}"
    return base64.b64encode(
        hmac.new(key.encode(), text.encode(), hashlib.sha1).digest()
    ).decode()


def sign_md5(key: str, params: Dict[str, Any], suffix: str = "&key=", lower: bool = True) -> str:
    """巨量 MD5：sign = md5(sorted_query + suffix + key)，默认小写 32 位。"""
    digest = hashlib.md5((_sorted_query(params) + suffix + key).encode()).hexdigest()
    return digest.lower() if lower else digest.upper()


# --------------------------------------------------------------------------- #
# 响应解析
# --------------------------------------------------------------------------- #
def parse_ip_port_entries(text: str, sep: str = "\n") -> List[str]:
    return [ln.strip() for ln in text.split(sep) if ln.strip() and not ln.startswith("ERROR")]


def parse_entry(entry: str, default_port: Optional[int] = None) -> Optional[RawProxy]:
    """解析一条代理串。支持:
        ip:port
        ip:port:user:pwd
        ip:port@user:pwd
        user:pwd:ip:port
        user:pwd@ip:port
    """
    entry = entry.strip()
    if not entry:
        return None

    # user:pwd@ip:port 或 ip:port@user:pwd
    if "@" in entry:
        left, right = entry.rsplit("@", 1)
        if ":" in left and _looks_like_ip(left.split(":")[0]):
            hostpart, authpart = left, right
        else:
            authpart, hostpart = left, right
        user, _, pwd = authpart.partition(":")
        ip, port = _split_host(hostpart, default_port)
        if not ip or port is None:
            return None
        return RawProxy(ip=ip, port=port, user=user or None, password=pwd or None)

    parts = entry.split(":")
    if len(parts) == 2:
        return RawProxy(ip=parts[0], port=int(parts[1]))
    if len(parts) == 4:
        # 两种 4 段格式：ip:port:user:pwd 与 user:pwd:ip:port，按哪一段像 IP 来区分
        if _looks_like_ip(parts[0]):
            return RawProxy(ip=parts[0], port=int(parts[1]), user=parts[2], password=parts[3])
        if _looks_like_ip(parts[2]):
            return RawProxy(ip=parts[2], port=int(parts[3]), user=parts[0], password=parts[1])
    return None


def _looks_like_ip(s: str) -> bool:
    return all(ch.isdigit() or ch == "." for ch in s)


def _split_host(hostport: str, default_port: Optional[int]) -> Tuple[str, Optional[int]]:
    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return hostport, default_port
    return hostport, default_port


def dig(path: str, data: Any) -> Any:
    """按 'a.b.c' 路径从嵌套 dict 中取值；缺失返回 None。"""
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


async def http_get(session: aiohttp.ClientSession, url: str, params: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None, method: str = "GET",
                   timeout: float = 15.0) -> str:
    """统一发起供应商 HTTP 请求，返回响应文本；异常统一抛 FetchError。"""
    try:
        async with session.request(
            method.upper(), url, params=params, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise FetchError(f"HTTP {resp.status}: {text[:200]}")
            return text
    except FetchError:
        raise
    except Exception as e:  # 网络错误 / 超时等
        raise FetchError(f"{type(e).__name__}: {e}") from e


class BaseProvider(abc.ABC):
    """所有供应商适配器的基类。"""

    #: 适配器类型名（用于 config.yaml 的 type 字段）
    type: str = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name") or self.type

    @abc.abstractmethod
    async def fetch(self, session: aiohttp.ClientSession) -> FetchResult:
        """拉取一批代理，返回 FetchResult；失败抛 FetchError。"""