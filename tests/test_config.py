"""配置加载单元测试。"""
import pytest

from free_proxy.config import Config


def test_defaults():
    cfg = Config.from_dict({})
    assert cfg.server.listen == "0.0.0.0:8899"
    assert cfg.control.listen == "127.0.0.1:8891"
    assert cfg.pool.strategy == "weighted"
    assert cfg.providers == []


def test_unknown_key_raises():
    with pytest.raises(ValueError):
        Config.from_dict({"pool": {"bogus_field": 1}})


def test_env_resolution(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "abc123")
    cfg = Config.from_dict({"providers": [{"type": "pinzan", "secret": "${MY_SECRET}"}]})
    assert cfg.providers[0]["secret"] == "abc123"


def test_env_unresolved_kept():
    cfg = Config.from_dict({"providers": [{"type": "pinzan", "secret": "${NOT_SET_XYZ}"}]})
    assert cfg.providers[0]["secret"] == "${NOT_SET_XYZ}"


def test_enabled_providers_default_true():
    cfg = Config.from_dict({"providers": [
        {"type": "pinzan", "enabled": True},
        {"type": "kuaidaili", "enabled": False},
        {"type": "julang"},
    ]})
    assert [p["type"] for p in cfg.enabled_providers()] == ["pinzan", "julang"]


def test_load_example_yaml(tmp_path, monkeypatch):
    # 示例配置引用了环境变量，但加载本身不应报错
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "conf" / "config.example.yaml"
    dst = tmp_path / "config.yaml"
    shutil.copy(src, dst)
    cfg = Config.load(str(dst))
    assert cfg.server.listen == "0.0.0.0:8899"
    assert len(cfg.providers) >= 3