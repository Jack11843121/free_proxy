"""SOCKS5 协议与 failover 全面覆盖测试。"""
import asyncio
import socket
import struct

import aiohttp
from aiohttp import web

from free_proxy.config import PoolConfig, ServerConfig
from free_proxy.model import Proxy
from free_proxy.pool import MemoryPool
from free_proxy.server import ForwardProxyServer
from free_proxy.socks5 import Socks5Server
from free_proxy.tunnel import _addr_bytes


async def _relay(r, w):
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


def _map_localhost(host):
    """测试环境：把 localhost 映射成 127.0.0.1，避免 macOS 优先解析到 ::1 导致连接卡住。"""
    h = host.decode() if isinstance(host, bytes) else host
    return "127.0.0.1" if h == "localhost" else h


async def _http_upstream(reader, writer):
    """极简 HTTP 上游（CONNECT + 绝对 URI 转发），用于给 SOCKS5 监听当上游。"""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return
    line = head.split(b"\r\n", 1)[0]
    if line.upper().startswith(b"CONNECT"):
        parts = line.split(b" ")
        host, _, port = parts[1].partition(b":")
        try:
            tr, tw = await asyncio.open_connection(_map_localhost(host), int(port))
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
        except Exception:
            writer.close()
        return
    parts = line.split(b" ")
    method, target, ver = parts[0], parts[1], parts[2]
    import urllib.parse
    u = urllib.parse.urlsplit(target.decode())
    try:
        tr, tw = await asyncio.open_connection(_map_localhost(u.hostname), u.port or 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        tw.write(method + b" " + path.encode() + b" " + ver + b"\r\n" + head[len(line) + 2:])
        await tw.drain()
        await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
    except Exception:
        writer.close()


async def _socks5_upstream(reader, writer):
    """极简 SOCKS5 上游（仅 CONNECT，转发到目标），用于链式转发测试。"""
    try:
        ver, nmethods = await reader.readexactly(2)
        methods = await reader.readexactly(nmethods)
        if 0x00 in methods:
            writer.write(b"\x05\x00")
        elif 0x02 in methods:
            writer.write(b"\x05\x02")
            await reader.readexactly(1)
            ulen = (await reader.readexactly(1))[0]
            await reader.readexactly(ulen)
            plen = (await reader.readexactly(1))[0]
            await reader.readexactly(plen)
            writer.write(b"\x01\x00")
        else:
            writer.write(b"\x05\xff")
            writer.close()
            return
        await writer.drain()

        hdr = await reader.readexactly(4)
        atyp = hdr[3]
        if atyp == 0x01:
            host = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
        elif atyp == 0x03:
            ln = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(ln)).decode()
        else:
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        port = struct.unpack(">H", await reader.readexactly(2))[0]

        tr, tw = await asyncio.open_connection(_map_localhost(host), port)
        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _origin(request):
    return web.Response(text="ORIGIN_OK")


async def _start_origin():
    app = web.Application()
    app.router.add_get("/", _origin)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


def _dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _socks5_connect(host, port, target_host, target_port, user=None, pwd=None):
    """极简 SOCKS5 客户端：完成协商 + CONNECT，返回 (reader, writer)。"""
    r, w = await asyncio.open_connection(host, port)
    if user:
        w.write(b"\x05\x02\x00\x02")
    else:
        w.write(b"\x05\x01\x00")
    await w.drain()
    a = await r.readexactly(2)
    if a[1] == 0x02:
        u, p = user.encode(), pwd.encode()
        w.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        await w.drain()
        ar = await r.readexactly(2)
        if ar[1] != 0x00:
            w.close()
            raise ConnectionError("认证失败")
    elif a[1] != 0x00:
        w.close()
        raise ConnectionError(f"无可用认证方法 {a[1]}")
    w.write(b"\x05\x01\x00" + _addr_bytes(target_host, target_port))
    await w.drain()
    rep = await r.readexactly(4)
    if rep[1] != 0x00:
        w.close()
        raise ConnectionError(f"CONNECT 被拒 REP={rep[1]}")
    atyp = rep[3]
    if atyp == 0x01:
        await r.readexactly(6)
    elif atyp == 0x03:
        ln = (await r.readexactly(1))[0]
        await r.readexactly(ln + 2)
    elif atyp == 0x04:
        await r.readexactly(18)
    return r, w


async def test_socks5_listen_connect_ipv4():
    runner, origin_port = await _start_origin()
    up = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]
    pool = MemoryPool(PoolConfig())
    pool.add(Proxy(ip="127.0.0.1", port=up_port, provider="t"))

    cfg = ServerConfig(socks5_listen="127.0.0.1:0")
    srv = Socks5Server(cfg, pool)
    await srv.start()
    socks_port = srv._server.sockets[0].getsockname()[1]
    try:
        r, w = await _socks5_connect("127.0.0.1", socks_port, "127.0.0.1", origin_port)
        w.write(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        await w.drain()
        body = await r.read()
        assert b"ORIGIN_OK" in body
        w.close()
    finally:
        await srv.close()
        up.close()
        await up.wait_closed()
        await runner.cleanup()


async def test_socks5_listen_domain_address():
    runner, origin_port = await _start_origin()
    up = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    pool = MemoryPool(PoolConfig())
    pool.add(Proxy(ip="127.0.0.1", port=up.sockets[0].getsockname()[1], provider="t"))
    cfg = ServerConfig(socks5_listen="127.0.0.1:0")
    srv = Socks5Server(cfg, pool)
    await srv.start()
    socks_port = srv._server.sockets[0].getsockname()[1]
    try:
        # ATYP=0x03，用 "localhost" 域名
        r, w = await _socks5_connect("127.0.0.1", socks_port, "localhost", origin_port)
        w.write(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await w.drain()
        assert b"ORIGIN_OK" in await r.read()
        w.close()
    finally:
        await srv.close()
        up.close()
        await up.wait_closed()
        await runner.cleanup()


async def test_socks5_auth_ok_and_fail():
    runner, origin_port = await _start_origin()
    up = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    pool = MemoryPool(PoolConfig())
    pool.add(Proxy(ip="127.0.0.1", port=up.sockets[0].getsockname()[1], provider="t"))
    cfg = ServerConfig(socks5_listen="127.0.0.1:0", socks5_user="u", socks5_password="p")
    srv = Socks5Server(cfg, pool)
    await srv.start()
    socks_port = srv._server.sockets[0].getsockname()[1]
    try:
        # 正确账密：认证通过 + CONNECT 成功 + 能转发
        r, w = await _socks5_connect("127.0.0.1", socks_port, "127.0.0.1", origin_port, user="u", pwd="p")
        w.write(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        await w.drain()
        assert b"ORIGIN_OK" in await r.read()
        w.close()
        # 错误密码：认证失败
        try:
            await _socks5_connect("127.0.0.1", socks_port, "127.0.0.1", origin_port, user="u", pwd="wrong")
            assert False, "应认证失败"
        except ConnectionError:
            pass
    finally:
        await srv.close()
        up.close()
        await up.wait_closed()
        await runner.cleanup()


async def test_socks5_reject_no_auth_method():
    pool = MemoryPool(PoolConfig())
    cfg = ServerConfig(socks5_listen="127.0.0.1:0", socks5_user="u", socks5_password="p")
    srv = Socks5Server(cfg, pool)
    await srv.start()
    socks_port = srv._server.sockets[0].getsockname()[1]
    try:
        r, w = await asyncio.open_connection("127.0.0.1", socks_port)
        w.write(b"\x05\x01\x00")  # 只提供 0x00，但服务器要求认证
        await w.drain()
        assert await r.readexactly(2) == b"\x05\xff"
        w.close()
    finally:
        await srv.close()


async def test_socks5_unsupported_command():
    up = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    pool = MemoryPool(PoolConfig())
    pool.add(Proxy(ip="127.0.0.1", port=up.sockets[0].getsockname()[1], provider="t"))
    cfg = ServerConfig(socks5_listen="127.0.0.1:0")
    srv = Socks5Server(cfg, pool)
    await srv.start()
    socks_port = srv._server.sockets[0].getsockname()[1]
    try:
        r, w = await asyncio.open_connection("127.0.0.1", socks_port)
        w.write(b"\x05\x01\x00")
        await w.drain()
        assert await r.readexactly(2) == b"\x05\x00"
        w.write(b"\x05\x02\x00\x01\x7f\x00\x00\x01\x00\x50")  # CMD=0x02 BIND
        await w.drain()
        rep = await r.readexactly(2)
        assert rep[1] == 0x07  # command not supported
        w.close()
    finally:
        await srv.close()
        up.close()
        await up.wait_closed()


async def test_socks5_upstream_chain():
    """free_proxy HTTP 前向 → SOCKS5 上游 → 目标。"""
    runner, origin_port = await _start_origin()
    sup = await asyncio.start_server(_socks5_upstream, "127.0.0.1", 0)
    pool = MemoryPool(PoolConfig())
    pool.add(Proxy(ip="127.0.0.1", port=sup.sockets[0].getsockname()[1], provider="t", scheme="socks5"))
    srv = ForwardProxyServer(ServerConfig(listen="127.0.0.1:0"), pool)
    await srv.start()
    proxy_port = srv._server.sockets[0].getsockname()[1]
    task = asyncio.create_task(srv.serve_forever())
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{origin_port}/",
                             proxy=f"http://127.0.0.1:{proxy_port}",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                assert r.status == 200
                assert await r.text() == "ORIGIN_OK"
    finally:
        task.cancel()
        await srv.close()
        sup.close()
        await sup.wait_closed()
        await runner.cleanup()


async def test_failover_dead_then_good():
    """上游先死代理、再活代理，会话应自动 failover 成功。"""
    runner, origin_port = await _start_origin()
    good = await asyncio.start_server(_http_upstream, "127.0.0.1", 0)
    dead_port = _dead_port()
    pool = MemoryPool(PoolConfig(strategy="round_robin"))
    pool.add(Proxy(ip="127.0.0.1", port=dead_port, provider="dead"))
    pool.add(Proxy(ip="127.0.0.1", port=good.sockets[0].getsockname()[1], provider="good"))

    srv = ForwardProxyServer(ServerConfig(listen="127.0.0.1:0", retry=2), pool)
    await srv.start()
    proxy_port = srv._server.sockets[0].getsockname()[1]
    task = asyncio.create_task(srv.serve_forever())
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{origin_port}/",
                             proxy=f"http://127.0.0.1:{proxy_port}",
                             timeout=aiohttp.ClientTimeout(total=5)) as r:
                assert r.status == 200
                assert await r.text() == "ORIGIN_OK"
    finally:
        task.cancel()
        await srv.close()
        good.close()
        await good.wait_closed()
        await runner.cleanup()