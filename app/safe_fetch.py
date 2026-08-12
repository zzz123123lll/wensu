"""安全抓取：SSRF 防护 + 重定向限制 + 大小上限 + 内容 hash。

抓取不可信 URL 前必须通过本模块。解析后的 IP 命中内网/保留段直接拒绝；
重定向逐跳重新校验；响应体、提取文本均设上限。返回 evidence snapshot。
"""

import hashlib
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MAX_BYTES = 1_000_000    # 响应体上限 1MB
MAX_TEXT_CHARS = 50_000  # 提取文本上限
MAX_REDIRECTS = 5
TIMEOUT = 10
UA = "wensu/0.3 (research assistant)"


class SafeFetchError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def resolve_allowed(url: str) -> bool:
    """域名解析后任一 IP 命中内网/保留段 → 拒绝。"""
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return all(not _blocked(info[4][0]) for info in infos)


def extract_text(body: bytes, mime: str) -> str:
    if "html" in mime:
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body.decode("utf-8", "ignore"))
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT_CHARS]
    if mime.startswith("text/") or mime in ("application/json", "application/xml", "application/rss+xml"):
        return body.decode("utf-8", "ignore")[:MAX_TEXT_CHARS]
    return ""  # 二进制：不提取文本


def fetch_url(url: str) -> dict:
    """抓取 URL → evidence snapshot 字典；不安全/失败抛 SafeFetchError。"""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise SafeFetchError("仅支持 http/https 地址")
    if not resolve_allowed(url):
        raise SafeFetchError("目标解析到内网/保留地址，已拦截")
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not resolve_allowed(current):
            raise SafeFetchError("重定向目标解析到内网/保留地址，已拦截")
        req = urllib.request.Request(current, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status in (301, 302, 303, 307, 308):
                current = urllib.parse.urljoin(current, resp.headers.get("Location", ""))
                continue
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SafeFetchError("内容超过大小上限")
            mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            return {
                "requested_url": url,
                "final_url": resp.geturl(),
                "mime": mime,
                "content_hash": hashlib.sha256(body).hexdigest()[:32],
                "excerpt": extract_text(body, mime),
                "fetched_at": _now(),
                "fetch_status": "ok",
            }
    raise SafeFetchError("重定向次数过多")
