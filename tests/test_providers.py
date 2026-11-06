"""供应商适配器单元测试（mock 掉 HTTP 请求，校验参数与解析）。"""
import pytest

from free_proxy.providers.base import FetchError, sign_md5
from free_proxy.providers.generic import GenericProvider
from free_proxy.providers.huasheng import HuashengProvider
from free_proxy.providers.julang import JulangProvider
from free_proxy.providers.kuaidaili import KuaidailiProvider
from free_proxy.providers.pinzan import PinzanProvider
from free_proxy.providers.static import StaticProvider
from free_proxy.providers.yiniuyun import YiniuyunProvider


class FakeHTTP:
    def __init__(self, body="", fail=False):
        self.body = body
        self.fail = fail
        self.calls = []

    async def __call__(self, session, url, params=None, headers=None, method="GET", timeout=15.0):
        self.calls.append({"url": url, "params": params, "method": method, "headers": headers})
        if self.fail:
            raise FetchError("boom")
        return self.body


def _patch(monkeypatch, modpath, fake):
    monkeypatch.setattr(modpath, fake)
    return fake


async def test_pinzan_parse(monkeypatch):
    body = '{"code":0,"data":{"list":[{"ip":"1.2.3.4","port":8080},{"host":"5.6.7.8","port":9090}]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.pinzan.http_get", fake)
    prov = PinzanProvider({"name": "pz", "no": "N1", "secret": "S1", "num": 5})
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080), ("5.6.7.8", 9090)]
    assert fake.calls[0]["params"]["no"] == "N1"
    assert fake.calls[0]["params"]["secret"] == "S1"


async def test_pinzan_error(monkeypatch):
    fake = FakeHTTP('{"code":1,"msg":"fail"}')
    _patch(monkeypatch, "free_proxy.providers.pinzan.http_get", fake)
    with pytest.raises(FetchError):
        await PinzanProvider({"name": "pz"}).fetch(None)


async def test_pinzan_auth_mode_fixed_cred(monkeypatch):
    # mode=auth：套餐固定账密，通过 auth_user/auth_password 下发
    body = '{"code":0,"data":{"list":[{"ip":"1.2.3.4","port":8080}]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.pinzan.http_get", fake)
    prov = PinzanProvider({"name": "pz", "no": "N1", "secret": "S1", "mode": "auth",
                           "auth_user": "u1", "auth_password": "p1", "num": 1})
    res = await prov.fetch(None)
    p = res.proxies[0]
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8080, "u1", "p1")


async def test_pinzan_auth_mode_per_ip_cred(monkeypatch):
    # 响应里每条自带账密时优先取响应
    body = '{"code":0,"data":{"list":[{"ip":"1.2.3.4","port":8080,"user":"ipu","password":"ipp"}]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.pinzan.http_get", fake)
    prov = PinzanProvider({"name": "pz", "no": "N1", "secret": "S1", "mode": "auth", "num": 1})
    res = await prov.fetch(None)
    p = res.proxies[0]
    assert (p.user, p.password) == ("ipu", "ipp")


async def test_pinzan_yaml_no_key_fallback(monkeypatch):
    # YAML 1.1 会把未加引号的 `no:` 解析成布尔 False 键，适配器需兼容
    body = '{"code":0,"data":{"list":[{"ip":"1.2.3.4","port":8080}]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.pinzan.http_get", fake)
    prov = PinzanProvider({False: "N1", "name": "pz", "secret": "S1", "num": 1})
    res = await prov.fetch(None)
    assert fake.calls[0]["params"]["no"] == "N1"
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080)]


async def test_kuaidaili_token(monkeypatch):
    body = '{"code":0,"data":{"count":1,"proxy_list":["1.2.3.4:8080"]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.kuaidaili.http_get", fake)
    prov = KuaidailiProvider({"name": "kdl", "secret_id": "SID", "sign_type": "token", "secret_token": "TOK", "num": 1})
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080)]
    assert fake.calls[0]["params"]["signature"] == "TOK"


async def test_kuaidaili_hmacsha1(monkeypatch):
    body = '{"code":0,"data":{"proxy_list":["1.2.3.4:8080"]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.kuaidaili.http_get", fake)
    prov = KuaidailiProvider({"name": "kdl", "secret_id": "SID", "sign_type": "hmacsha1", "secret_key": "KEY", "num": 1})
    await prov.fetch(None)
    p = fake.calls[0]["params"]
    assert p["sign_type"] == "hmacsha1"
    assert "timestamp" in p
    assert "signature" in p and p["signature"]


async def test_julang_sign_and_parse(monkeypatch):
    body = '{"code":200,"data":{"count":2,"proxy_list":["1.2.3.4:8080","5.6.7.8:9090"]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.julang.http_get", fake)
    prov = JulangProvider({"name": "jl", "trade_no": "TN", "api_key": "K", "num": 2, "pt": 1})
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080), ("5.6.7.8", 9090)]
    p = fake.calls[0]["params"]
    assert p["trade_no"] == "TN"
    # sign = md5(排序后除 sign 外的参数 + "&key=" + api_key)
    sub = {k: v for k, v in p.items() if k != "sign"}
    assert p["sign"] == sign_md5("K", sub)


async def test_julang_error(monkeypatch):
    fake = FakeHTTP('{"code":401,"msg":"not allowed"}')
    _patch(monkeypatch, "free_proxy.providers.julang.http_get", fake)
    with pytest.raises(FetchError):
        await JulangProvider({"name": "jl", "trade_no": "TN", "api_key": "K"}).fetch(None)


async def test_static():
    prov = StaticProvider({"name": "s", "proxies": ["1.2.3.4:80", "u:p@5.6.7.8:90"]})
    res = await prov.fetch()
    assert [(p.ip, p.port, p.user) for p in res.proxies] == [("1.2.3.4", 80, None), ("5.6.7.8", 90, "u")]


async def test_generic_text(monkeypatch):
    fake = FakeHTTP("1.2.3.4:80\n5.6.7.8:90\n")
    _patch(monkeypatch, "free_proxy.providers.generic.http_get", fake)
    prov = GenericProvider({
        "name": "g",
        "url": "https://v.example/get?key={secret}",
        "secret": "KEY1",
        "response": {"format": "text", "text_sep": "\n"},
    })
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 80), ("5.6.7.8", 90)]
    assert "key=KEY1" in fake.calls[0]["url"]


async def test_generic_json_md5_sign(monkeypatch):
    body = '{"code":0,"data":{"proxy_list":["1.2.3.4:80"]}}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.generic.http_get", fake)
    prov = GenericProvider({
        "name": "g",
        "url": "https://v.example/get",
        "params": {"orderid": "O1", "num": 1},
        "sign": {"type": "md5", "key": "K", "param": "sign"},
        "response": {"success_path": "code", "success_equals": 0, "list_path": "data.proxy_list"},
    })
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 80)]
    p = fake.calls[0]["params"]
    sub = {k: v for k, v in p.items() if k != "sign"}
    assert p["sign"] == sign_md5("K", sub)


async def test_yiniuyun_parse(monkeypatch):
    body = '{"status":true,"proxy":[{"ip":"1.2.3.4","port":8080},{"ip":"5.6.7.8","port":9090}]}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.yiniuyun.http_get", fake)
    prov = YiniuyunProvider({"name": "16yun", "order_id": "O1", "sign": "S1", "user": "U1", "num": 2})
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080), ("5.6.7.8", 9090)]
    params = fake.calls[0]["params"]
    assert params["s"] == "S1" and params["u"] == "U1"
    assert params["format"] == "json" and params["count"] == 2


async def test_yiniuyun_error(monkeypatch):
    fake = FakeHTTP('{"status":false}')
    _patch(monkeypatch, "free_proxy.providers.yiniuyun.http_get", fake)
    with pytest.raises(FetchError):
        await YiniuyunProvider({"name": "16yun", "order_id": "O1", "sign": "S1", "user": "U1"}).fetch(None)


async def test_huasheng_json_field_mapping(monkeypatch):
    body = '{"status":"0","list":[{"sever":"1.2.3.4","port":8080,"user":"u1","pw":"p1"}]}'
    fake = FakeHTTP(body)
    _patch(monkeypatch, "free_proxy.providers.huasheng.http_get", fake)
    prov = HuashengProvider({"name": "hs", "api_url": "https://ip.huashengdaili.com/get?x=1"})
    res = await prov.fetch(None)
    p = res.proxies[0]
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8080, "u1", "p1")


async def test_huasheng_text_fallback(monkeypatch):
    fake = FakeHTTP("1.2.3.4:8080\n5.6.7.8:9090\n")
    _patch(monkeypatch, "free_proxy.providers.huasheng.http_get", fake)
    prov = HuashengProvider({"name": "hs", "api_url": "https://ip.huashengdaili.com/get?x=1"})
    res = await prov.fetch(None)
    assert [(p.ip, p.port) for p in res.proxies] == [("1.2.3.4", 8080), ("5.6.7.8", 9090)]


async def test_huasheng_error_status(monkeypatch):
    fake = FakeHTTP('{"status":"1","msg":"no proxy"}')
    _patch(monkeypatch, "free_proxy.providers.huasheng.http_get", fake)
    with pytest.raises(FetchError):
        await HuashengProvider({"name": "hs", "api_url": "https://ip.huashengdaili.com/get?x=1"}).fetch(None)


async def test_huasheng_missing_url():
    with pytest.raises(FetchError):
        await HuashengProvider({"name": "hs"}).fetch(None)