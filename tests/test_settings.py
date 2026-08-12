"""settings 存储测试：DPAPI 加密用可注入实现（monkeypatch），另含真实 DPAPI 冒烟测试。"""

import base64

import pytest

from app import db, settings


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init(c)
    settings.ensure_table(c)
    return c


@pytest.fixture(autouse=True)
def fake_crypto(monkeypatch):
    """测试用 base64 假加密，避免依赖 Windows DPAPI。"""

    def enc(b: bytes) -> bytes:
        return b"ENC:" + base64.b64encode(b)

    def dec(b: bytes) -> bytes:
        assert b.startswith(b"ENC:")
        return base64.b64decode(b[4:])

    monkeypatch.setattr(settings, "_encrypt", enc)
    monkeypatch.setattr(settings, "_decrypt", dec)


def test_defaults_unconfigured(conn):
    s = settings.get_settings(conn)
    assert s["configured"] is False
    assert s["model"] == ""
    assert s["has_key"] is False


def test_save_get_roundtrip(conn):
    settings.save_settings(conn, "https://api.deepseek.com/v1", "deepseek-chat", "sk-test-123")
    s = settings.get_settings(conn)
    assert s["configured"] is True
    assert s["base_url"] == "https://api.deepseek.com/v1"
    assert s["model"] == "deepseek-chat"
    assert s["has_key"] is True


def test_key_stored_encrypted_not_plaintext(conn):
    settings.save_settings(conn, "https://api.example.com/v1", "m", "sk-secret")
    row = conn.execute("SELECT api_key_enc FROM settings WHERE id=1").fetchone()
    assert row is not None
    enc = bytes(row["api_key_enc"])
    assert enc != b"sk-secret"
    assert b"sk-secret" not in enc


def test_save_without_key_keeps_old(conn):
    settings.save_settings(conn, "https://api.example.com/v1", "m1", "sk-old")
    settings.save_settings(conn, "https://api.example.com/v2", "m2")  # 同 origin 不传 key → 保留
    s = settings.get_settings(conn)
    assert s["base_url"] == "https://api.example.com/v2"
    assert s["model"] == "m2"
    assert s["has_key"] is True


def test_get_api_key(conn):
    settings.save_settings(conn, "https://api.example.com/v1", "m", "sk-abc")
    assert settings.get_api_key(conn) == "sk-abc"


# ---------- origin 安全 ----------

def test_origin_change_clears_key(conn):
    settings.save_settings(conn, "https://api.a.com/v1", "m", "sk-old")
    assert settings.get_api_key(conn) == "sk-old"
    settings.save_settings(conn, "https://api.b.com/v1", "m")  # 换 origin 不提供新 Key → 清
    assert settings.get_api_key(conn) == ""
    assert settings.get_settings(conn)["has_key"] is False


def test_origin_unchanged_keeps_key(conn):
    settings.save_settings(conn, "https://api.a.com/v1", "m1", "sk-old")
    settings.save_settings(conn, "https://api.a.com/v2", "m2")  # 同 origin 换路径
    assert settings.get_api_key(conn) == "sk-old"


def test_new_key_overrides_on_origin_change(conn):
    settings.save_settings(conn, "https://api.a.com/v1", "m", "sk-old")
    settings.save_settings(conn, "https://api.b.com/v1", "m", "sk-new")  # 显式新 Key = 重新授权
    assert settings.get_api_key(conn) == "sk-new"


def test_invalid_scheme_rejected(conn):
    with pytest.raises(ValueError):
        settings.save_settings(conn, "javascript:alert(1)", "m")
    with pytest.raises(ValueError):
        settings.save_settings(conn, "ftp://x.com/v1", "m")
    with pytest.raises(ValueError):
        settings.save_settings(conn, "http://api.example.com/v1", "m")  # 明文 http 非本地


def test_local_http_allowed(conn):
    settings.save_settings(conn, "http://127.0.0.1:11434/v1", "m")  # 本地模型端点


@pytest.mark.skipif(not hasattr(settings, "_dpapi_available") or not settings._dpapi_available,
                    reason="Windows DPAPI 不可用")
def test_dpapi_real_roundtrip():
    enc = settings._dpapi_protect(b"sk-real-key")
    assert enc != b"sk-real-key"
    assert settings._dpapi_unprotect(enc) == b"sk-real-key"
