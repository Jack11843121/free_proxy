"""上游隧道建立：把客户端流量链到上游代理，支持 HTTP 与 SOCKS5 两种上游协议。"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
import struct
from typing import Tuple

from .model import Proxy


def _set_nodelay(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass


async def open_tcp(proxy: Proxy, timeout: float) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """直接 TCP 连到上游代理（用于 HTTP 上游的绝对 URI 转发）。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(proxy.ip, proxy.port), timeout=timeout
    )
    _set_nodelay(writer)
    return reader, writer


async def _read_head(reader: asyncio.StreamReader, limit: int = 65536):
    try:
        return await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return None


def _basic_auth(user: str, password: str) -> bytes:
    if not user:
        return b""
    raw = f"{user}:{password or ''}".encode()
    return b"Proxy-Authorization: Basic " + base64.b64encode(raw) + b"\r\n"


def _addr_bytes(host: str, port: int) -> bytes:
    """SOCKS5 地址编码：IPv4 / IPv6 / 域名三种 ATYP。"""
    try:
        ip = ipaddress.ip_address(host)
        if ip.version == 6:
            return b"\x04" + ip.packed + struct.pack(">H", port)
        return b"\x01" + ip.packed + struct.pack(">H", port)
    except ValueError:
        h = host.encode("idna")
        return b"\x03" + bytes([len(h)]) + h + struct.pack(">H", port)


async def open_http_tunnel(proxy: Proxy, host: str, port: int, timeout: float):
    """通过 HTTP 上游代理 CONNECT 到目标。"""
    reader, writer = await open_tcp(proxy, timeout)
    req = (
        f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n".encode()
        + _basic_auth(proxy.user or "", proxy.password or "")
        + b"\r\n"
    )
    writer.write(req)
    await writer.drain()
    head = await _read_head(reader)
    if head is None:
        writer.close()
        raise ConnectionError("上游 HTTP CONNECT 无响应")
    status = head.split(b"\r\n", 1)[0]
    if b" 200" not in status:
        writer.close()
        raise ConnectionError(f"上游 HTTP CONNECT 失败: {status[:80].decode('latin-1', 'replace')}")
    return reader, writer


async def open_socks5_tunnel(proxy: Proxy, host: str, port: int, timeout: float):
    """通过 SOCKS5 上游代理 CONNECT 到目标（含可选 RFC1929 用户名密码认证）。"""
    reader, writer = await open_tcp(proxy, timeout)

    # 1) 问候：优先无认证，其次用户名密码
    methods = b"\x05\x02\x00\x02" if proxy.user else b"\x05\x01\x00"
    writer.write(methods)
    await writer.drain()
    resp = await _readexactly(reader, 2)
    if resp[0] != 0x05:
        writer.close()
        raise ConnectionError("上游 SOCKS5 版本错误")
    method = resp[1]
    if method == 0x02:
        u = (proxy.user or "").encode()
        p = (proxy.password or "").encode()
        writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        await writer.drain()
        auth = await _readexactly(reader, 2)
        if auth[1] != 0x00:
            writer.close()
            raise ConnectionError("上游 SOCKS5 认证失败")
    elif method != 0x00:
        writer.close()
        raise ConnectionError(f"上游 SOCKS5 不支持的认证方法 {method}")

    # 2) CONNECT
    writer.write(b"\x05\x01\x00" + _addr_bytes(host, port))
    await writer.drain()
    hdr = await _readexactly(reader, 4)
    if hdr[0] != 0x05 or hdr[1] != 0x00:
        writer.close()
        raise ConnectionError(f"上游 SOCKS5 CONNECT 被拒 REP={hdr[1]}")
    atyp = hdr[3]
    if atyp == 0x01:
        await _readexactly(reader, 6)  # IPv4 + port
    elif atyp == 0x03:
        ln = (await _readexactly(reader, 1))[0]
        await _readexactly(reader, ln + 2)
    elif atyp == 0x04:
        await _readexactly(reader, 18)  # IPv6 + port
    return reader, writer


async def open_tunnel(proxy: Proxy, host: str, port: int, timeout: float):
    """按 proxy.scheme 建立到目标 host:port 的隧道。"""
    if proxy.scheme == "socks5":
        return await open_socks5_tunnel(proxy, host, port, timeout)
    return await open_http_tunnel(proxy, host, port, timeout)


async def _readexactly(reader: asyncio.StreamReader, n: int) -> bytes:
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise ConnectionError("上游 SOCKS5 连接提前关闭") from e