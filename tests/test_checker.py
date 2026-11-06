"""健康检查（HTTP 上游 + SOCKS5 上游）测试。"""
import asyncio
import socket
import struct

import aiohttp
from aiohttp import web

from free_proxy.checker import check_proxy
from free_proxy.model import Proxy


async def _relay(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def _http_upstream(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return
    line = head.split(b"\r\n", 1)[0]
    if line.upper().startswith(b"CONNECT"):
        parts = line.split(b" ")
        host, _, port = parts[1].partition(b":")
        tr, tw = await asyncio.open_connection(host.decode(), int(port))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
        return
    import urllib.parse
    parts = line.split(b" ")
    u = urllib.parse.urlsplit(parts[1].decode())
    tr, tw = await asyncio.open_connection(u.hostname, u.port or 80)
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    tw.write(parts[0] + b" " + path.encode() + b" " + parts[2] + b"\r\n" + head[len(line) + 2:])
    await tw.drain()
    await asyncio.gather(_relay(reader, tw), _relay(tr, writer))


async def _socks5_ok_upstream(reader, writer):
    """只完成 SOCKS5 握手并回成功，不真正转发（够健康检查用）。"""
    try:
        await reader.readexactly(2)
        nmethods = (await reader.readexactly(1))[0]
        await reader.readexactly(nmethods)
        writer.write(b"\x05\x00")
        await writer.drain()
        hdr = await reader.readexactly(4)
        atyp = hdr[3]
        if atyp == 0x01:
            await reader.readexactly(4)
        elif atyp == 0x03:
            ln = (await reader.readexactly(1))[0]
            await reader.readexactly(ln)
        elif atyp == 0x04:
            await reader.readexactly(16)
        await reader.readexactly(2)
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _origin(request):
    return web.Response(text="OK")


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_check_http_alive():
    app = web.Application()
    app.router.add_get("/", _origin)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin_port = site._server.sockets[0].getsockname()[1]

    up = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    proxy = Proxy(ip="127.0.0.1", port=up_port, scheme="http")

    async with aiohttp.ClientSession() as session:
        ok, latency = await check_proxy(session, proxy, f"http://127.0.0.1:{origin_port}/", 5.0)
    assert ok is True and latency > 0
    up.close()
    await up.wait_closed()
    await runner.cleanup()


async def test_check_http_dead():
    proxy = Proxy(ip="127.0.0.1", port=_dead_port(), scheme="http")
    async with aiohttp.ClientSession() as session:
        ok, _ = await check_proxy(session, proxy, "http://127.0.0.1:1/", 3.0)
    assert ok is False


async def test_check_socks5_alive():
    up = await asyncio.start_server(_socks5_ok_upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    proxy = Proxy(ip="127.0.0.1", port=up_port, scheme="socks5")
    async with aiohttp.ClientSession() as session:
        ok, latency = await check_proxy(session, proxy, "http://127.0.0.1:80/", 5.0)
    assert ok is True and latency > 0
    up.close()
    await up.wait_closed()


async def test_check_socks5_dead():
    proxy = Proxy(ip="127.0.0.1", port=_dead_port(), scheme="socks5")
    async with aiohttp.ClientSession() as session:
        ok, _ = await check_proxy(session, proxy, "http://127.0.0.1:80/", 3.0)
    assert ok is False