"""搜索引擎适配器测试（行为契约回收自旧仓"文成" pipeline/http_research.py）。

不碰公网：解析器用 fixture HTML，runner 用注入 fetch。
"""

import pytest

from app import search_engines


# ---------- URL 校验 ----------

def test_validate_public_url_rejects_unsafe():
    for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd",
                "/relative/path", "ftp://x.com/a", ""):
        with pytest.raises(ValueError):
            search_engines.validate_public_url(bad)


def test_validate_public_url_accepts_http():
    assert search_engines.validate_public_url("https://example.com/a?b=1") == "https://example.com/a?b=1"


# ---------- 解析器（fixture HTML） ----------

BING_HTML = """
<html><body><ol>
<li class="b_algo"><h2><a href="https://a.com/1">中文标题A</a></h2><p>摘要文字A</p></li>
<li class="b_algo"><h2><a href="javascript:void(0)">坏链接</a></h2><p>摘要</p></li>
<li class="b_algo"><h2><a href="https://b.com/2">Title B</a></h2><p>snippet b</p></li>
</ol></body></html>
"""


def test_parse_bing_extracts_and_skips_unsafe_and_non_chinese():
    out = search_engines.parse_bing(BING_HTML)
    assert len(out) == 1
    assert out[0]["title"] == "中文标题A"
    assert out[0]["url"] == "https://a.com/1"
    assert out[0]["source"] == "web"


SO_MOBILE_HTML = """
<html><body>
<a href="https://m.so.com/link?u=https%3A%2F%2Ftarget.com%2Fx">文章标题不错</a>
<a href="/link?u=">短</a>
</body></html>
"""


def test_parse_so_mobile_extracts_redirect_target():
    out = search_engines.parse_so_mobile(SO_MOBILE_HTML)
    assert len(out) == 1
    assert out[0]["title"] == "文章标题不错"
    assert out[0]["url"] == "https://target.com/x"


SOGOU_MOBILE_HTML = """
<html><body>
<h3><a href="https://wap.sogou.com/web/redirect?url=https%3A%2F%2Fexample.com%2Fa">搜狗结果标题</a></h3>
</body></html>
"""


def test_parse_sogou_mobile_extracts_redirect_target():
    out = search_engines.parse_sogou_mobile(SOGOU_MOBILE_HTML)
    assert len(out) == 1
    assert out[0]["url"] == "https://example.com/a"


BAIDU_HTML = """
<html><body><h3><a href="https://www.baidu.com/link?url=abc">百度结果标题</a></h3></body></html>
"""


def test_parse_baidu_keeps_href():
    out = search_engines.parse_baidu(BAIDU_HTML)
    assert len(out) == 1
    assert out[0]["url"] == "https://www.baidu.com/link?url=abc"


DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsite.com%2Fp">DuckDuckGo 结果</a>
  <a class="result__snippet">片段文字</a>
</div>
</body></html>
"""


def test_parse_ddg_html_extracts_uddg_target():
    out = search_engines.parse_ddg_html(DDG_HTML)
    assert len(out) == 1
    assert out[0]["url"] == "https://site.com/p"
    assert "片段文字" in out[0]["snippet"]


# ---------- runner：并发合并/去重/失败可观测 ----------

def _engines():
    return [
        ("bing", "https://bing.test/{q}", search_engines.parse_bing),
        ("so", "https://so.test/{q}", search_engines.parse_so_mobile),
        ("broken", "https://broken.test/{q}", search_engines.parse_bing),
    ]


def _fetch_map():
    return {
        "https://bing.test/x": BING_HTML,
        "https://so.test/x": SO_MOBILE_HTML,
    }


def test_search_all_merges_engines_and_reports_failures():
    fetched = []
    pages = _fetch_map()

    def fake_fetch(url, timeout):
        fetched.append(url)
        if url not in pages:
            raise RuntimeError("boom")
        return pages[url]

    results, failures = search_engines.search_all("x", engines=_engines(), fetch=fake_fetch)
    assert failures == ["broken"]
    assert len(results) == 2
    assert {r["url"] for r in results} == {"https://a.com/1", "https://target.com/x"}
    assert all(r["source"] == "web" for r in results)
    assert len(fetched) == 3


def test_search_all_dedupes_by_url():
    html = """
    <html><body><h3><a href="https://dup.com/a">重复一</a></h3>
    <h3><a href="https://dup.com/a">重复二</a></h3></body></html>
    """
    engines = [("baidu", "https://baidu.test/{q}", search_engines.parse_baidu)]

    def fake_fetch(url, timeout):
        return html

    results, failures = search_engines.search_all("x", engines=engines, fetch=fake_fetch)
    assert failures == []
    assert len(results) == 1


def test_search_all_caps_max_results():
    html = "".join(f'<h3><a href="https://site.com/{i}">标题{i}</a></h3>' for i in range(10))
    engines = [("baidu", "https://baidu.test/{q}", search_engines.parse_baidu)]

    def fake_fetch(url, timeout):
        return html

    results, _ = search_engines.search_all("x", engines=engines, fetch=fake_fetch, max_results=5)
    assert len(results) == 5


def test_search_all_all_fail_returns_empty_not_crash():
    def fake_fetch(url, timeout):
        raise RuntimeError("offline")

    results, failures = search_engines.search_all("x", engines=_engines(), fetch=fake_fetch)
    assert results == []
    assert set(failures) == {"bing", "so", "broken"}
