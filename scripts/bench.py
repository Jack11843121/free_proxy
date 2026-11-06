#!/usr/bin/env python3
"""free_proxy 性能基准。

在 loopback 上起「本地 mock 上游 + 本地源站」，测量 free_proxy 转发链路自身的
吞吐与延迟（不依赖任何真实代理 IP，衡量网关开销是否够低）。

用法：
  python scripts/bench.py
  python scripts/bench.py --concurrency 10,50,200 --requests 3000 --megabytes 64
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse
from pathlib import Path

# 保证从任意目录运行都能找到项目包（如 python scripts/bench.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from aiohttp import web

from free_proxy.config import PoolConfig, ServerConfig
from free_proxy.model import Proxy
from free_proxy.pool import MemoryPool
from free_proxy.server import ForwardProxyServer

RELAY_CHUNK = 256 * 1024
HTTP_BODY = b"x" * 1024  # 1 KB 典型短响应


async def _relay(r, w):
    try:
        while True:
            data = await r.read(RELAY_CHUNK)
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


async def _upstream(reader, writer):
    """极简上游代理：CONNECT 与绝对 URI 两类请求都透明转发到目标。"""
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
            writer.close()
        return
    parts = line.split(b" ")
    method, target, ver = parts[0], parts[1], parts[2]
    u = urllib.parse.urlsplit(target.decode())
    try:
        tr, tw = await asyncio.open_connection(u.hostname, u.port or 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        tw.write(method + b" " + path.encode() + b" " + ver + b"\r\n" + head[len(line) + 2:])
        await tw.drain()
        await asyncio.gather(_relay(reader, tw), _relay(tr, writer))
    except Exception:
        writer.close()


async def _origin(request):
    return web.Response(body=HTTP_BODY)


async def _tcp_sink(reader, writer):
    """只读不回应，用于 CONNECT 隧道吞吐测试。"""
    try:
        while await reader.read(RELAY_CHUNK):
            pass
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


async def bench_http(proxy_port, origin_port, concurrency, total):
    lat = []
    per = max(1, total // concurrency)

    async def worker(session):
        for _ in range(per):
            t0 = time.perf_counter()
            try:
                async with session.get(
                    f"http://127.0.0.1:{origin_port}/",
                    proxy=f"http://127.0.0.1:{proxy_port}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    await r.read()
                lat.append((time.perf_counter() - t0) * 1000)
            except Exception:
                pass

    connector = aiohttp.TCPConnector()
    t0 = time.perf_counter()
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(*[worker(session) for _ in range(concurrency)])
    wall = time.perf_counter() - t0
    done = len(lat)
    rps = done / wall if wall > 0 else 0.0
    return rps, (_pct(lat, 0.50), _pct(lat, 0.90), _pct(lat, 0.99))


async def bench_connect(proxy_port, megabytes):
    sink = await asyncio.start_server(_tcp_sink, "127.0.0.1", 0)
    sink_port = sink.sockets[0].getsockname()[1]
    total = int(megabytes * 1024 * 1024)
    payload = b"x" * (256 * 1024)

    cr, cw = await asyncio.open_connection("127.0.0.1", proxy_port)
    cw.write(f"CONNECT 127.0.0.1:{sink_port} HTTP/1.1\r\nHost: 127.0.0.1:{sink_port}\r\n\r\n".encode())
    await cw.drain()
    await cr.readuntil(b"\r\n\r\n")

    sent = 0
    t0 = time.perf_counter()
    while sent < total:
        cw.write(payload)
        await cw.drain()
        sent += len(payload)
    cw.close()
    await cw.wait_closed()
    wall = time.perf_counter() - t0

    sink.close()
    await sink.wait_closed()
    return (total / (1024 * 1024)) / wall if wall > 0 else 0.0


async def main(args):
    origin = web.Application()
    origin.router.add_get("/", _origin)
    runner = web.AppRunner(origin)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    origin_port = site._server.sockets[0].getsockname()[1]

    up = await asyncio.start_server(_upstream, "127.0.0.1", 0)
    up_port = up.sockets[0].getsockname()[1]

    pool = MemoryPool(PoolConfig(max_size=10))
    pool.add(Proxy(ip="127.0.0.1", port=up_port, provider="bench"))

    srv = ForwardProxyServer(ServerConfig(listen="127.0.0.1:0", backlog=2048), pool)
    await srv.start()
    proxy_port = srv._server.sockets[0].getsockname()[1]
    serve_task = asyncio.create_task(srv.serve_forever())

    print("=" * 62)
    print("free_proxy 性能基准（loopback · mock 上游 · 本地源站）")
    print("=" * 62)
    print("HTTP 前向转发（1 KB 响应）：")
    print(f"{'并发':>6} {'请求':>8} {'吞吐(req/s)':>13} {'p50(ms)':>9} {'p90(ms)':>9} {'p99(ms)':>9}")
    results = []
    try:
        for c in args.concurrency:
            rps, (p50, p90, p99) = await bench_http(proxy_port, origin_port, c, args.requests)
            print(f"{c:>6} {args.requests:>8} {rps:>13.0f} {p50:>9.2f} {p90:>9.2f} {p99:>9.2f}")
            results.append({"concurrency": c, "rps": round(rps, 1),
                            "p50_ms": round(p50, 2), "p90_ms": round(p90, 2), "p99_ms": round(p99, 2)})

        mbps = await bench_connect(proxy_port, args.megabytes)
        print(f"\nCONNECT 隧道吞吐：{mbps:0.2f} MB/s（{args.megabytes} MB 单连接大块转发）")
        results.append({"connect_mbps": round(mbps, 2)})
    finally:
        serve_task.cancel()
        await srv.close()
        up.close()
        await up.wait_closed()
        await runner.cleanup()

    return results


def parse_args():
    p = argparse.ArgumentParser(description="free_proxy 性能基准")
    p.add_argument("--concurrency", default="10,50,200", help="并发梯度，逗号分隔")
    p.add_argument("--requests", type=int, default=2000, help="每个并发档位的总请求数")
    p.add_argument("--megabytes", type=int, default=64, help="CONNECT 隧道吞吐测试的传输量(MB)")
    return p.parse_args()


if __name__ == "__main__":
    a = parse_args()
    a.concurrency = [int(x) for x in a.concurrency.split(",") if x.strip()]
    out = asyncio.run(main(a))
    print("\n[结果 JSON] " + json.dumps(out, ensure_ascii=False))