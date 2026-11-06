"""HTTP CONNECT 目标解析（含 IPv6 字面量）单元测试。"""
from free_proxy.server import _parse_connect_target


def test_parse_ipv4():
    assert _parse_connect_target(b"CONNECT 1.2.3.4:443 HTTP/1.1") == ("1.2.3.4", 443)


def test_parse_domain():
    assert _parse_connect_target(b"CONNECT example.com:8080 HTTP/1.1") == ("example.com", 8080)


def test_parse_ipv6_literal():
    assert _parse_connect_target(b"CONNECT [::1]:443 HTTP/1.1") == ("::1", 443)
    assert _parse_connect_target(b"CONNECT [2001:db8::1]:8080 HTTP/1.0") == ("2001:db8::1", 8080)


def test_parse_bad():
    assert _parse_connect_target(b"GET / HTTP/1.1") is None
    assert _parse_connect_target(b"CONNECT no-port HTTP/1.1") is None
    assert _parse_connect_target(b"CONNECT abc:bad HTTP/1.1") is None