"""前向代理服务器：把本地收到的 HTTP / CONNECT 流量链到池中挑选的上游代理。

纯 asyncio 字节级中继，不解析业务内容，HTTP 与 HTTPS(CONNECT) 全透明。
"""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Optional, Tuple

from .config import ServerConfig
from .logger import get_logger
from .model import Proxy
from .util import parse_listen

log = get_logger("server")

MAX_HEAD = 65536
CONNECT_RE = re.compile(rb"^CONNECT\s+([^:\s]+):(\d+)\s+HTTP/1\.[01]$", re.I)
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
            data = await reader.read(65536)
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
        try:
            head = await _read_head(reader)
            if not head:
                return
            proxy = self.pool.pick()
            if proxy is None:
                await _reply(writer, 503, "free_proxy: no available proxy in pool")
                return
            first_line = head.split(b"\r\n", 1)[0]
            if first_line.upper().startswith(b"CONNECT"):
                m = CONNECT_RE.match(first_line)
                if not m:
                    await _reply(writer, 400, "bad CONNECT request line")
                    return
                await self._tunnel(reader, writer, m.group(1).decode(), int(m.group(2)), proxy)
            else:
                await self._forward_http(reader, writer, head, first_line, proxy)
        except Exception as e:  # noqa: BLE001
            log.debug("连接处理异常: %s", e)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _connect_upstream(self, proxy: Proxy) -> Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(proxy.ip, proxy.port),
                timeout=self.cfg.connect_timeout,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("连接上游 %s 失败: %s", proxy.key, e)
            return None

    async def _tunnel(self, reader, writer, host: str, port: int, proxy: Proxy) -> None:
        conn = await self._connect_upstream(proxy)
        if conn is None:
            await _reply(writer, 502, f"free_proxy: cannot reach upstream {proxy.key}")
            return
        up_reader, up_writer = conn

        req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n".encode() + _auth_header(proxy) + b"\r\n"
        up_writer.write(req)
        await up_writer.drain()

        up_head = await _read_head(up_reader)
        if up_head is None:
            up_writer.close()
            await _reply(writer, 502, "free_proxy: upstream tunnel failed")
            return
        status_line = up_head.split(b"\r\n", 1)[0]
        if b" 200 " not in status_line and not status_line.startswith(b"HTTP/1.1 200"):
            writer.write(up_head)  # 把上游错误转发给客户端
            await writer.drain()
            up_writer.close()
            return

        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_relay(reader, up_writer), _relay(up_reader, writer))

    async def _forward_http(self, reader, writer, head: bytes, first_line: bytes, proxy: Proxy) -> None:
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

        conn = await self._connect_upstream(proxy)
        if conn is None:
            await _reply(writer, 502, f"free_proxy: cannot reach upstream {proxy.key}")
            return
        up_reader, up_writer = conn

        up_writer.write(_rehead(head, method, target, proxy))
        await up_writer.drain()
        await asyncio.gather(_relay(reader, up_writer), _relay(up_reader, writer))

    @staticmethod
    def _extract_host(head: bytes) -> Optional[bytes]:
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"host:"):
                return line.split(b":", 1)[1].strip()
        return None