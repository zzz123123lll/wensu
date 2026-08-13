"""浏览器无关的 HTTP 搜索适配器（行为契约回收自旧仓"文成" pipeline/http_research.py）。

- httpx + BeautifulSoup，无 Playwright 依赖
- 每引擎独立超时；单引擎失败不影响其他引擎，失败名单以 failures 返回（可观测，不静默吞）
- 结果统一 {title, url, snippet, source:"web"}；URL 必须通过公网校验（http/https + host）
- 解析器为纯函数（html → 结果），测试注入 fixture 即可，不依赖公网
"""

import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DEFAULT_TIMEOUT = 5.0
MAX_RESULTS = 8

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """共享 httpx.Client（线程安全、连接池复用）。"""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(follow_redirects=True,
                                   headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        return _client


def validate_public_url(href: str) -> str:
    """公网 URL 校验：仅 http/https 且有 host；非法抛 ValueError（安全白名单）。"""
    value = str(href or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"非法 URL: {value[:60]!r}")
    return value


def fetch_html(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """抓取引擎结果页 HTML；非 200/网络错误抛异常（由 runner 记为失败）。"""
    r = _get_client().get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def _result(title: str, url: str, snippet: str) -> dict:
    return {"title": title, "url": url, "snippet": snippet[:400], "source": "web"}


# ---------- 解析器（纯函数） ----------

def parse_bing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("li.b_algo"):
        link = item.select_one("h2 a")
        if not link:
            continue
        try:
            href = validate_public_url(link.get("href", ""))
        except ValueError:
            continue
        title = link.get_text(" ", strip=True)
        snippet_tag = item.select_one("p")
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""
        if title and (_CJK.search(title) or _CJK.search(snippet)):
            out.append(_result(title, href, snippet))
    return out


def parse_so_mobile(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    for item in soup.select("a[href]"):
        title = item.get_text(" ", strip=True)
        if not title or len(title) < 4:
            continue
        href = item.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        query = urlsplit(href).query
        real_url = parse_qs(query).get("u", [""])[0] or parse_qs(query).get("url", [""])[0]
        host = (urlsplit(href).hostname or "").lower()
        if not real_url and host.endswith("so.com") and urlsplit(href).path.startswith("/link"):
            continue
        if real_url:
            href = real_url
        try:
            href = validate_public_url(href)
        except ValueError:
            continue
        if href in seen:
            continue
        seen.add(href)
        container = item.parent
        snippet = ""
        if container is not None:
            snippet = container.get_text(" ", strip=True)
            if title and title in snippet:
                snippet = snippet.replace(title, "", 1)
        out.append(_result(title, href, snippet[:300]))
    return out


def parse_sogou_mobile(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("h3 a[href]"):
        title = item.get_text(" ", strip=True)
        if not title or "大家还在搜" in title:
            continue
        href = item.get("href", "")
        if not href or href.startswith("javascript"):
            continue
        query = urlsplit(href).query
        real_url = parse_qs(query).get("url", [""])[0]
        if real_url:
            href = real_url
        try:
            href = validate_public_url(href)
        except ValueError:
            continue
        container = item.parent
        snippet = ""
        if container is not None:
            snippet = container.get_text(" ", strip=True)
            if title and title in snippet:
                snippet = snippet.replace(title, "", 1)
        out.append(_result(title, href, snippet[:300]))
    return out


def parse_baidu(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    for h3 in soup.select("h3"):
        link = h3.select_one("a[href]")
        if not link:
            continue
        title = h3.get_text(" ", strip=True)
        if not title or title in seen:
            continue
        seen.add(title)
        try:
            href = validate_public_url(link.get("href", ""))
        except ValueError:
            continue
        container = h3.parent
        snippet = ""
        if container is not None:
            snippet = container.get_text(" ", strip=True)
            if title and title in snippet:
                snippet = snippet.replace(title, "", 1)
        out.append(_result(title, href, snippet[:300]))
    return out


def parse_ddg_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for item in soup.select("div.result"):
        link = item.select_one("a.result__a")
        if not link:
            continue
        href = link.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        try:
            parsed = urlsplit(href)
            if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
                target = parse_qs(parsed.query).get("uddg", [""])[0]
                if target:
                    href = target
        except Exception:
            continue
        try:
            href = validate_public_url(href)
        except ValueError:
            continue
        title = link.get_text(" ", strip=True)
        snippet_tag = item.select_one(".result__snippet") or item.select_one("a.result__snippet")
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""
        if title:
            out.append(_result(title, href, snippet))
    return out


# ---------- 引擎清单与并发 runner ----------

ENGINE_SPECS = [
    ("bing", "https://www.bing.com/search?q={q}&mkt=zh-CN&ensearch=0", parse_bing),
    ("so_mobile", "https://m.so.com/s?q={q}", parse_so_mobile),
    ("sogou_mobile", "https://wap.sogou.com/web/searchList.jsp?keyword={q}", parse_sogou_mobile),
    ("baidu", "https://www.baidu.com/s?wd={q}&ie=utf-8&rn=10", parse_baidu),
    ("ddg_html", "https://html.duckduckgo.com/html/?q={q}", parse_ddg_html),
]


def search_all(query: str, engines=None, max_results: int = MAX_RESULTS,
               timeout: float = DEFAULT_TIMEOUT, fetch=None) -> tuple[list[dict], list[str]]:
    """并发执行多引擎；按声明顺序合并、按 URL 去重、截断 max_results。

    返回 (results, failures)：单引擎失败不影响其他引擎，失败名单可观测。
    """
    specs = list(engines) if engines is not None else list(ENGINE_SPECS)
    if not specs:
        return [], []
    fetch_fn = fetch or fetch_html
    encoded = urllib.parse.quote(str(query))

    def run_one(spec):
        name, template, parser = spec
        try:
            html = fetch_fn(template.format(q=encoded), timeout)
            return name, parser(html) or []
        except Exception:
            return name, None

    by_engine = {}
    with ThreadPoolExecutor(max_workers=len(specs)) as ex:
        futures = [ex.submit(run_one, spec) for spec in specs]
        for future in as_completed(futures):
            name, payload = future.result()
            by_engine[name] = payload

    collected, seen, failures = [], set(), []
    for name, _template, _parser in specs:
        payload = by_engine.get(name)
        if payload is None:
            failures.append(name)
            continue
        for r in payload:
            key = r.get("url") or r.get("title")
            if key not in seen:
                seen.add(key)
                collected.append(r)
        if len(collected) >= max_results:
            break
    return collected[:max(0, max_results)], failures
