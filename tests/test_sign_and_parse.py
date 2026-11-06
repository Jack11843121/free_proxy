"""签名算法与代理串解析的单元测试。"""
import base64
import hashlib
import hmac

from free_proxy.providers.base import parse_entry, sign_hmacsha1, sign_md5


def test_parse_ip_port():
    p = parse_entry("1.2.3.4:8000")
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8000, None, None)


def test_parse_ip_port_user_pwd():
    p = parse_entry("1.2.3.4:8000:u:p")
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8000, "u", "p")


def test_parse_user_pwd_ip_port():
    p = parse_entry("u:p:1.2.3.4:8000")
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8000, "u", "p")


def test_parse_host_first_at():
    p = parse_entry("1.2.3.4:8000@u:p")
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8000, "u", "p")


def test_parse_auth_first_at():
    p = parse_entry("u:p@1.2.3.4:8000")
    assert (p.ip, p.port, p.user, p.password) == ("1.2.3.4", 8000, "u", "p")


def test_sign_md5_recipe():
    # sign = md5(sorted(k=v&...) + "&key=" + key)，32 位小写
    params = {"trade_no": "abc", "num": "5"}
    expected = hashlib.md5(b"num=5&trade_no=abc&key=KEY123").hexdigest()
    assert sign_md5("KEY123", params) == expected


def test_sign_hmacsha1_recipe():
    key = "SECRET"
    params = {"num": "5", "secret_id": "SID1", "sign_type": "hmacsha1", "timestamp": "1700000000"}
    text = "GET/api/getdps?num=5&secret_id=SID1&sign_type=hmacsha1&timestamp=1700000000"
    expected = base64.b64encode(hmac.new(key.encode(), text.encode(), hashlib.sha1).digest()).decode()
    assert sign_hmacsha1(key, "GET", "/api/getdps", params) == expected