"""safe_fetch 测试：SSRF 防护 / scheme / 重定向 / 大小上限（不真联网）。"""

import io
import urllib.error
import urllib.request

import pytest

from app import safe_fetch


class FakeResp:
    def __init__(self, body=b"<html>hello</html>", status=200, headers=None, url="https://ok.example/x"):
        self._body = body
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._body[:n] if n >= 0 else self._body

    def geturl(self):
        return self._url


def test_blocked_private_ip(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("127.0.0.1", 80))])
    assert safe_fetch.resolve_allowed("https://internal.example/") is False


def test_allowed_public_ip(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    assert safe_fetch.resolve_allowed("https://example.com/") is True


def test_mixed_ips_any_private_blocks(monkeypatch):
    ips = [("93.184.216.34", 80), ("10.0.0.5", 80)]
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, x) for x in ips])
    assert safe_fetch.resolve_allowed("https://mix.example/") is False


def test_bad_scheme_rejected():
    with pytest.raises(safe_fetch.SafeFetchError):
        safe_fetch.fetch_url("file:///etc/passwd")
    with pytest.raises(safe_fetch.SafeFetchError):
        safe_fetch.fetch_url("javascript:alert(1)")


def test_fetch_ok_returns_snapshot(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp("<html><body>你好世界</body></html>".encode("utf-8")))
    snap = safe_fetch.fetch_url("https://ok.example/page")
    assert snap["fetch_status"] == "ok"
    assert snap["content_hash"]  # sha256 存在
    assert "你好世界" in snap["excerpt"]
    assert snap["final_url"] == "https://ok.example/x"


def test_fetch_strips_scripts(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    body = "<html><script>alert(1)</script><p>正文</p></html>".encode("utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp(body))
    snap = safe_fetch.fetch_url("https://ok.example/xss")
    assert "alert(1)" not in snap["excerpt"]
    assert "正文" in snap["excerpt"]


def test_oversized_body_rejected(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    big = b"a" * (safe_fetch.MAX_BYTES + 10)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp(big))
    with pytest.raises(safe_fetch.SafeFetchError, match="大小上限"):
        safe_fetch.fetch_url("https://big.example/")

def test_redirect_to_private_blocked(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    seen = {"hits": 0}

    def fake_urlopen(req, timeout):
        seen["hits"] += 1
        if seen["hits"] == 1:
            return FakeResp(b"", status=302, headers={"Location": "https://internal.example/secret"}, url="https://ok.example/r")
        return FakeResp(b"secret data")

    # 第二次跳转时解析到内网
    real = safe_fetch.resolve_allowed
    def resolve_with_block(url):
        if "internal" in url:
            return False
        return real(url)
    monkeypatch.setattr(safe_fetch, "resolve_allowed", resolve_with_block)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(safe_fetch.SafeFetchError, match="内网/保留"):
        safe_fetch.fetch_url("https://ok.example/r")


def test_redirect_loop_rejected(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp(b"", status=302, headers={"Location": "https://ok.example/a"}, url="https://ok.example/b"))
    with pytest.raises(safe_fetch.SafeFetchError, match="重定向"):
        safe_fetch.fetch_url("https://ok.example/a")


def test_binary_mime_no_text(monkeypatch):
    monkeypatch.setattr(safe_fetch.socket, "getaddrinfo", lambda h, p, **k: [(0, 0, 0, 0, ("93.184.216.34", 80))])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp(b"\x89PNG\r\n\x1a\n", headers={"Content-Type": "image/png"}))
    snap = safe_fetch.fetch_url("https://img.example/x.png")
    assert snap["excerpt"] == ""
