"""控制面（Web 控制台 + API）测试。"""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from free_proxy.config import ControlConfig, PoolConfig
from free_proxy.control import ControlAPI
from free_proxy.pool import MemoryPool


class StubScheduler:
    provider_names = ["pinzan"]
    last_refresh = 0.0
    last_check = 0.0

    async def refresh_once(self):
        return None

    async def check_once(self):
        return None


@pytest.fixture
async def make_client():
    async def _make(token=""):
        pool = MemoryPool(PoolConfig())
        api = ControlAPI(ControlConfig(token=token), pool, StubScheduler())
        return TestClient(TestServer(api.build_app()))

    return _make


async def test_index_serves_html(make_client):
    async with await make_client() as client:
        r = await client.get("/")
        assert r.status == 200
        assert "text/html" in r.headers["Content-Type"]
        assert "free_proxy 控制台" in await r.text()


async def test_healthz(make_client):
    async with await make_client() as client:
        r = await client.get("/healthz")
        assert r.status == 200
        assert (await r.json())["status"] == "ok"


async def test_status_no_token(make_client):
    async with await make_client() as client:
        r = await client.get("/api/v1/status")
        assert r.status == 200
        data = await r.json()
        assert data["providers"] == ["pinzan"]


async def test_token_protects_api_but_not_page(make_client):
    async with await make_client("secret123") as client:
        # 静态页面无需鉴权
        assert (await client.get("/")).status == 200
        # API 无 token -> 401
        assert (await client.get("/api/v1/status")).status == 401
        # API 带 token -> 200
        r = await client.get("/api/v1/status?token=secret123")
        assert r.status == 200


async def test_refresh_endpoint(make_client):
    async with await make_client() as client:
        r = await client.post("/api/v1/refresh")
        assert r.status == 200
        assert (await r.json())["ok"] is True