"""端到端集成测试：客户端 → free_proxy → 上游(replica) → 目标源站。"""
import asyncio
import urllib.parse

import aiohttp
from aiohttp import web
import pytest

from free_proxy.config import PoolConfig, ServerConfig
from free_proxy.model import Proxy
from free_proxy.pool import MemoryPool
from free_proxy.server import ForwardProxyServer


async def _relay(reader, writer):
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
        except Exception:
            pass


async def _upstream_handler(reader, writer):
    """一个独立实现的极简上游代理：理解 CONNECT 与绝对 URI 两种请求，转发到目标。"""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return
    line = head.split(b"\r\n", 1)[0]

    if line.upper().startswith(b"CONNECT"):
        parts = line.split(b" ")
        host, _, port = parts[1].partition(b":")
        try:
            tr, tw = await asyncio.open_connection(host.decode(), int(port))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
        except Exception:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            writer.close()
        return

    # 绝对 URI HTTP 请求
    parts = line.split(b" ")
    method, target, version = parts[0], parts[1], parts[2]
    u = urllib.parse.urlsplit(target.decode())
    try:
        tr, tw = await asyncio.open_connection(u.hostname, u.port or 80)
        path = u.path or b"/"
        if u.query:
            path = path + "?" + u.query
        newline = method + b" " + path.encode() + b" " + version
        rest = head[len(line) + 2:]
        tw.write(newline + b"\r\n" + rest)
        await tw.drain()
        await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
    except Exception:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()


async def _start_proxy(pool) -> tuple:
    server = ForwardProxyServer(ServerConfig(listen="127.0.0.1:0"), pool)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    task = asyncio.create_task(server.serve_forever())
    return server, port, task


async def test_forward_proxy_http_chain():
    # 1) 源站
    async def origin(request):
        return web.Response(text="hello-from-origin", headers={"X-Test": "1"})

    app = web.Application()
    app.router.add_get("/", origin)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin_port = site._server.sockets[0].getsockname()[1]

    # 2) 上游代理（独立实现）
    up = await asyncio.start_server(_upstream_handler, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]

    # 3) free_proxy：池里只有一个上游
    pool = MemoryPool(PoolConfig(max_size=10))
    pool.add(Proxy(ip="127.0.0.1", port=up_port, provider="test"))

    server, proxy_port, task = await _start_proxy(pool)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{origin_port}/",
                proxy=f"http://127.0.0.1:{proxy_port}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                assert r.status == 200
                assert await r.text() == "hello-from-origin"
                assert r.headers.get("X-Test") == "1"
    finally:
        task.cancel()
        await server.close()
        up.close()
        await up.wait_closed()
        await runner.cleanup()


async def test_no_proxy_returns_503():
    pool = MemoryPool(PoolConfig(max_size=10))
    server, proxy_port, task = await _start_proxy(pool)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "http://127.0.0.1:1/",
                proxy=f"http://127.0.0.1:{proxy_port}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                assert r.status == 503
    finally:
        task.cancel()
        await server.close()


async def test_forward_proxy_connect_tunnel():
    # 1) 回显服务器（作为 CONNECT 隧道后的目标）
    async def echo(reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    echo_srv = await asyncio.start_server(echo, "127.0.0.1", 0)
    echo_port = echo_srv.sockets[0].getsockname()[1]

    up = await asyncio.start_server(_upstream_handler, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]

    pool = MemoryPool(PoolConfig(max_size=10))
    pool.add(Proxy(ip="127.0.0.1", port=up_port, provider="test"))

    server, proxy_port, task = await _start_proxy(pool)
    try:
        cr, cw = await asyncio.open_connection("127.0.0.1", proxy_port)
        cw.write(f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: 127.0.0.1:{echo_port}\r\n\r\n".encode())
        await cw.drain()
        head = await cr.readuntil(b"\r\n\r\n")
        assert b"200" in head.split(b"\r\n", 1)[0]

        cw.write(b"ping")
        await cw.drain()
        assert await cr.read(4) == b"ping"
        cw.close()
    finally:
        task.cancel()
        await server.close()
        up.close()
        await up.wait_closed()
        echo_srv.close()
        await echo_srv.wait_closed()