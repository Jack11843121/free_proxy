"""free_proxy 主程序：组装配置、调度器、代理服务器、控制面。"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal

import aiohttp

from . import __version__
from .config import Config
from .control import ControlAPI
from .logger import get_logger, setup_logging
from .pool import create_pool
from .scheduler import Scheduler
from .server import ForwardProxyServer
from .socks5 import Socks5Server

log = get_logger("app")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="free_proxy",
        description="多代理商聚合的本地 IP 代理网关：一个本地入口聚合品赞/快代理/巨量等所有 IP 代理供应商。",
    )
    p.add_argument("-c", "--config", default="config.yaml", help="配置文件路径（默认 config.yaml）")
    p.add_argument("--check-once", action="store_true", help="拉取 + 健康检查各一轮后退出（调试用）")
    p.add_argument("-v", "--version", action="version", version=__version__)
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = Config.load(args.config)
    setup_logging("INFO")
    try:
        asyncio.run(_run(cfg, check_once=args.check_once))
    except KeyboardInterrupt:
        log.info("收到中断，退出")
    return 0


async def _run(cfg: Config, check_once: bool = False) -> None:
    session = aiohttp.ClientSession()
    pool = create_pool(cfg.pool, cfg.store)
    scheduler = Scheduler(cfg, pool, session)
    proxy_server = ForwardProxyServer(cfg.server, pool)
    control = ControlAPI(cfg.control, pool, scheduler)
    socks5_server = Socks5Server(cfg.server, pool) if cfg.server.socks5_listen else None

    if check_once:
        await scheduler.refresh_once()
        await scheduler.check_once()
        print("pool stats:", pool.stats())
        await session.close()
        return

    await proxy_server.start()
    await control.start()
    if socks5_server is not None:
        await socks5_server.start()
    sched_task = asyncio.create_task(scheduler.run())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    endpoints = f"前向代理={cfg.server.listen}"
    endpoints += f" SOCKS5={cfg.server.socks5_listen}" if socks5_server is not None else ""
    log.info("free_proxy v%s 已启动：%s 控制面=%s", __version__, endpoints, cfg.control.listen)
    try:
        await stop.wait()
    finally:
        log.info("正在关闭…")
        sched_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sched_task
        await proxy_server.close()
        if socks5_server is not None:
            await socks5_server.close()
        await control.close()
        await session.close()
        log.info("已退出")


if __name__ == "__main__":
    main()