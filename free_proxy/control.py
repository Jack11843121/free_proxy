"""控制面：HTTP API + 自包含 Web 控制台（dashboard）。"""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from . import __version__
from .config import ControlConfig
from .logger import get_logger
from .util import parse_listen

log = get_logger("control")

_INDEX_PATH = Path(__file__).resolve().parent / "web" / "index.html"
# 无需鉴权即可访问的静态页面（API 仍受 control.token 保护）
_PUBLIC_PATHS = {"/", "/index.html", "/healthz"}


class ControlAPI:
    def __init__(self, cfg: ControlConfig, pool, scheduler):
        self.cfg = cfg
        self.pool = pool
        self.scheduler = scheduler
        self._runner = None

    def build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth])
        app.router.add_get("/", self.index)
        app.router.add_get("/index.html", self.index)
        app.router.add_get("/healthz", self.health)
        app.router.add_get("/api/v1/status", self.status)
        app.router.add_get("/api/v1/proxies", self.proxies)
        app.router.add_get("/api/v1/proxy", self.get_proxy)
        app.router.add_get("/api/v1/providers", self.providers)
        app.router.add_post("/api/v1/refresh", self.refresh)
        app.router.add_post("/api/v1/check", self.check)
        return app

    @web.middleware
    async def _auth(self, request, handler):
        if request.path in _PUBLIC_PATHS:
            return await handler(request)
        token = self.cfg.token
        if token:
            h = request.headers.get("Authorization", "")
            if h != f"Bearer {token}" and request.query.get("token") != token:
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def start(self) -> None:
        host, port = parse_listen(self.cfg.listen)
        self._runner = web.AppRunner(self.build_app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        log.info("控制面已监听 %s:%d", host, port)

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    # ---------------- handlers ----------------
    async def index(self, request):
        try:
            html = _INDEX_PATH.read_text(encoding="utf-8")
        except OSError:
            html = "<html><body><h1>free_proxy</h1><p>控制台静态文件缺失。</p></body></html>"
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def health(self, request):
        return web.json_response({"status": "ok", "version": __version__})

    async def status(self, request):
        stats = self.pool.stats()
        stats["version"] = __version__
        stats["providers"] = self.scheduler.provider_names
        stats["last_refresh"] = self.scheduler.last_refresh
        stats["last_check"] = self.scheduler.last_check
        return web.json_response(stats)

    async def proxies(self, request):
        limit = int(request.query.get("limit", "100"))
        data = [p.to_dict() for p in self.pool.snapshot()[:limit]]
        return web.json_response({"count": len(data), "proxies": data})

    async def get_proxy(self, request):
        p = self.pool.pick()
        if p is None:
            return web.json_response({"error": "no available proxy"}, status=503)
        return web.json_response(p.to_dict())

    async def providers(self, request):
        return web.json_response({"providers": self.scheduler.provider_names})

    async def refresh(self, request):
        await self.scheduler.refresh_once()
        return web.json_response({"ok": True, "count": self.pool.count()})

    async def check(self, request):
        await self.scheduler.check_once()
        return web.json_response({"ok": True, **self.pool.stats()})