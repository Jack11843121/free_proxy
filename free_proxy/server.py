"""前向代理服务器：把本地收到的 HTTP / CONNECT 流量链到池中挑选的上游代理。

纯 asyncio 字节级中继，不解析业务内容，HTTP 与 HTTPS(CONNECT) 全透明。
"""
from __future__ import annotations

import asyncio
import base64
import re
import socket
import urllib.parse
from typing import Optional, Tuple

from .config import ServerConfig
from .logger import get_logger
from .model import Proxy
from .tunnel import open_socks5_tunnel, open_tcp, open_tunnel
from .util import parse_listen

log = get_logger("server")

MAX_HEAD = 65536
RELAY_CHUNK = 256 * 1024  # 大缓冲中转，减少系统调用次数
REQUEST_RE = re.compile(rb"^(\w+)\s+(\S+)\s+HTTP/1\.[01]$", re.I)

_STATUS = {
    400: "Bad Request",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


async def _reply(writer: asyncio.StreamWriter, code: int, text: str) -> None:
    body = text.encode()
    writer.write(
        f"HTTP/1.1 {code} {_STATUS.get(code, 'Error')}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    )
    await writer.drain()


async def _read_head(reader: asyncio.StreamReader, limit: int = MAX_HEAD) -> Optional[bytes]:
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        return None


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(RELAY_CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


def _set_nodelay(writer: asyncio.StreamWriter) -> None:
    """关闭 Nagle 算法，降低小包（CONNECT 握手 / 短响应）的往返延迟。"""
    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass


def _auth_header(proxy: Proxy) -> bytes:
    if not proxy.user:
        return b""
    raw = f"{proxy.user}:{proxy.password or ''}".encode()
    return b"Proxy-Authorization: Basic " + base64.b64encode(raw) + b"\r\n"


def _rehead(head: bytes, method: bytes, target: bytes, proxy: Proxy) -> bytes:
    """重写首行（保证绝对 URI）并注入上游鉴权，其余头透传。"""
    core = head[:-4]  # 去掉结尾的 \r\n\r\n
    lines = core.split(b"\r\n")
    ver = lines[0].split(b" ", 2)[-1] if len(lines[0].split(b" ", 2)) == 3 else b"HTTP/1.1"
    parts = [method + b" " + target + b" " + ver]
    for h in lines[1:]:
        lh = h.lower()
        if lh.startswith(b"proxy-authorization:") or lh.startswith(b"proxy-connection:"):
            continue  # 去掉客户端自己的代理头，避免污染上游
        parts.append(h)
    auth = _auth_header(proxy)
    if auth:
        parts.append(auth.rstrip(b"\r\n"))
    return b"\r\n".join(parts) + b"\r\n\r\n"


def _origin_rehead(head: bytes, method: bytes, path: bytes) -> bytes:
    """重写首行为 origin-form（用于 SOCKS5 上游：先 CONNECT 后走普通 HTTP）。"""
    core = head[:-4]
    lines = core.split(b"\r\n")
    ver = lines[0].split(b" ", 2)[-1] if len(lines[0].split(b" ", 2)) == 3 else b"HTTP/1.1"
    parts = [method + b" " + path + b" " + ver]
    for h in lines[1:]:
        lh = h.lower()
        if lh.startswith(b"proxy-authorization:") or lh.startswith(b"proxy-connection:"):
            continue
        parts.append(h)
    return b"\r\n".join(parts) + b"\r\n\r\n"


def _parse_connect_target(line: bytes) -> Optional[Tuple[str, int]]:
    """解析 CONNECT 目标行，返回 (host, port)；支持 IPv4 / 域名 / IPv6 字面量 [::1]:port。"""
    parts = line.split(b" ")
    if len(parts) < 3 or parts[0].upper() != b"CONNECT":
        return None
    target = parts[1]
    if target.startswith(b"["):
        close = target.find(b"]")
        if close < 0 or len(target) <= close + 2 or target[close + 1:close + 2] != b":":
            return None
        host = target[1:close].decode("ascii")
        try:
            port = int(target[close + 2:])
        except ValueError:
            return None
    else:
        host_s, sep, port_s = target.rpartition(b":")
        if not sep or not host_s:
            return None
        host = host_s.decode("ascii")
        try:
            port = int(port_s)
        except ValueError:
            return None
    return host, port


class ForwardProxyServer:
    def __init__(self, cfg: ServerConfig, pool):
        self.cfg = cfg
        self.pool = pool
        self._server: Optional[asyncio.Server] = None

    async def start(self) -> None:
        host, port = parse_listen(self.cfg.listen)
        self._server = await asyncio.start_server(self._handle, host, port, backlog=self.cfg.backlog)
        log.info("前向代理已监听 %s:%d", host, port)

    async def serve_forever(self) -> None:
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _set_nodelay(writer)
        try:
            head = await _read_head(reader)
            if not head:
                return
            first_line = head.split(b"\r\n", 1)[0]
            if first_line.upper().startswith(b"CONNECT"):
                target = _parse_connect_target(first_line)
                if not target:
                    await _reply(writer, 400, "bad CONNECT request line")
                    return
                await self._tunnel(reader, writer, target[0], target[1])
            else:
                await self._forward_http(reader, writer, head, first_line)
        except Exception as e:  # noqa: BLE001
            log.debug("连接处理异常: %s", e)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _tunnel(self, reader, writer, host: str, port: int) -> None:
        """CONNECT 隧道：带 failover 换上游代理（HTTP CONNECT / SOCKS5 CONNECT）。"""
        conn = None
        attempted = False
        for _ in range(max(1, self.cfg.retry)):
            proxy = self.pool.pick()
            if proxy is None:
                break
            attempted = True
            try:
                conn = await open_tunnel(proxy, host, port, self.cfg.connect_timeout)
                break
            except Exception as e:  # noqa: BLE001
                log.debug("上游隧道失败 %s: %s", proxy.key, e)
                continue
        if conn is None:
            await _reply(writer, 502 if attempted else 503,
                         "free_proxy: no working upstream proxy" if attempted else "free_proxy: no available proxy in pool")
            return
        up_reader, up_writer = conn
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_relay(reader, up_writer), _relay(up_reader, writer))

    async def _forward_http(self, reader, writer, head: bytes, first_line: bytes) -> None:
        """普通 HTTP 前向：按上游协议转发（HTTP 绝对 URI / SOCKS5 先 CONNECT 再 origin-form）。"""
        m = REQUEST_RE.match(first_line.rstrip(b"\r\n"))
        if not m:
            await _reply(writer, 400, "bad request line")
            return
        method = m.group(1)
        target = m.group(2)

        # 目标不是绝对 URI（origin-form）时，用 Host 头补全
        if target.startswith(b"/"):
            host = self._extract_host(head)
            if not host:
                await _reply(writer, 400, "missing Host header")
                return
            target = b"http://" + host + target

        u = urllib.parse.urlsplit(target.decode("latin-1"))
        host = u.hostname
        port = u.port or 80
        if not host:
            await _reply(writer, 400, "bad absolute URI")
            return
        origin = (u.path or "/") + (("?" + u.query) if u.query else "")
        origin_bytes = origin.encode("latin-1")

        conn = None
        attempted = False
        for _ in range(max(1, self.cfg.retry)):
            proxy = self.pool.pick()
            if proxy is None:
                break
            attempted = True
            try:
                if proxy.scheme == "socks5":
                    up_reader, up_writer = await open_socks5_tunnel(proxy, host, port, self.cfg.connect_timeout)
                    out = _origin_rehead(head, method, origin_bytes)
                else:
                    up_reader, up_writer = await open_tcp(proxy, self.cfg.connect_timeout)
                    out = _rehead(head, method, target, proxy)
                up_writer.write(out)
                await up_writer.drain()
                conn = (up_reader, up_writer)
                break
            except Exception as e:  # noqa: BLE001
                log.debug("上游转发失败 %s: %s", proxy.key, e)
                continue
        if conn is None:
            await _reply(writer, 502 if attempted else 503,
                         "free_proxy: no working upstream proxy" if attempted else "free_proxy: no available proxy in pool")
            return
        up_reader, up_writer = conn
        await asyncio.gather(_relay(reader, up_writer), _relay(up_reader, writer))

    @staticmethod
    def _extract_host(head: bytes) -> Optional[bytes]:
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"host:"):
                return line.split(b":", 1)[1].strip()
        return None