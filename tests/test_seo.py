"""SEO 测试 —— 覆盖技术 SEO 基建（robots/sitemap/元信息/结构化数据/站点图标/404）。

2026-09-13 全面 SEO 整改，本文件从「断言几个标签存在」升级为**一致性防回归**：
- robots.txt 与 sitemap.xml 必须自洽（sitemap 里不许出现被 Disallow 的 URL）
- sitemap 里每个 URL 必须 200 且 canonical 指向自身（否则收录的是错页）
- 每个可索引公开页：唯一 title/description/canonical、单一 h1、robots 允许收录
- 结构化数据必须是合法 JSON 且类型齐全
- 私有路径必须带 X-Robots-Tag: noindex（robots.txt 之外的响应头兜底）
"""

import json
import re
import xml.etree.ElementTree as ET

import pytest

from ai_search.dashboard import seo

# 可索引公开页（与 seo.PUBLIC_PAGES 同源，避免测试与实现各写一份）
PUBLIC_PATHS = [path for path, _priority, _changefreq in seo.PUBLIC_PAGES]
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _sitemap_urls(client) -> list[str]:
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    return [loc.text for loc in root.findall("sm:url/sm:loc", SITEMAP_NS)]


def _head(html: str) -> str:
    return html.split("</head>")[0]


def _attr(html: str, pattern: str) -> str:
    match = re.search(pattern, html)
    assert match, f"未匹配到：{pattern}"
    return match.group(1)


def _jsonld_blocks(html: str) -> list[object]:
    """取出页面里全部 JSON-LD 并解析（解析失败即断言失败）。"""
    blocks = re.findall(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.S
    )
    assert blocks, "页面缺少 application/ld+json 结构化数据"
    return [json.loads(block) for block in blocks]


def _jsonld_types(payloads: list[object]) -> set[str]:
    """递归收集 @type（含 @graph 与嵌套节点）。"""

    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("@type"), str):
                found.add(node["@type"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for payload in payloads:
        walk(payload)
    return found


# ---------- robots.txt ----------


def test_robots_txt_ok(client):
    """robots.txt 返回 200 且为 text/plain，含放行规则、私有路径屏蔽与 sitemap 声明。"""
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")

    text = resp.text
    assert "User-agent: *" in text
    assert "Allow: /" in text
    for prefix in seo.PRIVATE_PATH_PREFIXES:
        assert f"Disallow: {prefix}" in text
    assert "Sitemap:" in text and "/sitemap.xml" in text


def test_robots_does_not_block_public_pages(client):
    """公开页不得出现在任何 Disallow 规则下（老实现把 /dashboard/docs 写进 sitemap 又 Disallow）。"""
    text = client.get("/robots.txt").text
    rules = re.findall(r"^Disallow:\s*(\S+)\s*$", text, re.M)
    assert rules, "robots.txt 未解析到 Disallow 规则"
    for path in PUBLIC_PATHS:
        for rule in rules:
            assert not path.startswith(rule), f"公开页 {path} 被 robots 规则 {rule} 屏蔽"


# ---------- sitemap.xml ----------


def test_sitemap_xml_content_type_is_xml(client):
    """sitemap.xml 必须以 XML Content-Type 返回（老实现返回 text/plain，爬虫会忽略）。"""
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]


def test_sitemap_xml_ok(client):
    """sitemap.xml 是合法 XML，覆盖全部公开页，且每条带 lastmod/priority。"""
    resp = client.get("/sitemap.xml")
    root = ET.fromstring(resp.text)
    entries = root.findall("sm:url", SITEMAP_NS)
    locs = [e.findtext("sm:loc", None, SITEMAP_NS) for e in entries]

    assert len(entries) == len(seo.PUBLIC_PAGES)
    for loc in locs:
        assert loc.startswith("http"), loc
    base = seo.get_base_url()
    assert f"{base}/" in locs
    assert f"{base}/docs" in locs
    assert f"{base}/mcp-server" in locs
    assert f"{base}/pricing" in locs
    assert f"{base}/faq" in locs
    assert f"{base}/terms" in locs

    for entry in entries:
        assert entry.findtext("sm:lastmod", None, SITEMAP_NS)
        assert entry.findtext("sm:priority", None, SITEMAP_NS)


def test_sitemap_and_robots_are_consistent(client):
    """sitemap 里的每个 URL 都不能被 robots.txt 屏蔽（一致性的核心断言）。"""
    text = client.get("/robots.txt").text
    rules = re.findall(r"^Disallow:\s*(\S+)\s*$", text, re.M)
    for url in _sitemap_urls(client):
        path = url.split(seo.get_base_url(), 1)[-1] or "/"
        for rule in rules:
            assert not path.startswith(rule), f"sitemap 收录了被屏蔽的 URL：{url}（规则 {rule}）"


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_sitemap_urls_are_indexable(client, path):
    """sitemap 里列出的页面必须可访问、可索引，且 canonical 指向自身。"""
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} 不是 200（sitemap 收录了不可访问的页）"

    head = _head(resp.text)
    assert "noindex" not in _attr(head, r'<meta name="robots" content="([^"]*)"')
    assert _attr(head, r'<link rel="canonical" href="([^"]+)"') == seo.canonical_url(path)
    assert "X-Robots-Tag" not in resp.headers


# ---------- 公开页元信息 ----------


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_page_meta_complete(client, path):
    """每个公开页都有完整的 title/description/canonical/og/twitter/图标/单一 h1。"""
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.text
    head = _head(html)

    title = _attr(head, r"<title>(.*?)</title>")
    description = _attr(head, r'<meta name="description" content="([^"]*)"')
    assert 15 <= len(title) <= 90, f"{path} title 长度不合适：{title}"
    assert 50 <= len(description) <= 200, f"{path} description 长度不合适"

    assert '<html lang="zh-CN">' in html
    assert 'name="viewport"' in head
    assert 'name="author"' in head

    # Open Graph / Twitter
    assert _attr(head, r'<meta property="og:title" content="([^"]*)"') == title
    assert _attr(head, r'<meta property="og:url" content="([^"]*)"') == seo.canonical_url(path)
    assert _attr(head, r'<meta property="og:image" content="([^"]*)"').endswith("/static/og-image.png")
    assert "zh_CN" in head
    assert "<meta property=\"og:locale\"" in head
    assert _attr(head, r'<meta name="twitter:card" content="([^"]*)"') == "summary_large_image"
    assert _attr(head, r'<meta name="twitter:image" content="([^"]*)"').endswith("/static/og-image.png")

    # 图标与 manifest
    assert 'rel="icon"' in head and "/favicon.ico" in head
    assert "/favicon.svg" in head
    assert 'rel="apple-touch-icon"' in head

    # 文案结构：单一 h1
    assert html.count("<h1") == 1, f"{path} 的 h1 数量不是 1"
    assert html.count("</h1>") == 1

    # 结构化数据合法且带 @context
    payloads = _jsonld_blocks(html)
    for payload in payloads:
        for node in payload if isinstance(payload, list) else [payload]:
            assert "@context" in node


def test_public_pages_have_unique_titles_and_canonicals(client):
    """公开页之间不得出现重复 title / canonical（重复即内耗权重）。"""
    titles: dict[str, str] = {}
    canonicals: dict[str, str] = {}
    for path in PUBLIC_PATHS:
        head = _head(client.get(path).text)
        title = _attr(head, r"<title>(.*?)</title>")
        canonical = _attr(head, r'<link rel="canonical" href="([^"]+)"')
        assert title not in titles, f"{path} 与 {titles[title]} 的 title 重复：{title}"
        assert canonical not in canonicals, f"{path} 与 {canonicals[canonical]} 的 canonical 重复"
        titles[title] = path
        canonicals[canonical] = path


def test_landing_structured_data(client):
    """首页结构化数据：Organization / WebSite / SoftwareApplication / FAQPage 齐全。"""
    html = client.get("/").text
    types = _jsonld_types(_jsonld_blocks(html))
    assert {"Organization", "WebSite", "SoftwareApplication", "FAQPage", "Question"} <= types

    # FAQ 结构化数据必须与页面可见文案一致（防只写 JSON-LD 不写正文的作弊式标注）
    for question, _answer in seo.LANDING_FAQ:
        assert question in html


def test_landing_agent_first_positioning(client):
    """落地页以 Agent 接入为第一卖点：首屏是 MCP 配置，不再宣传 RAG。"""
    resp = client.get("/")
    assert resp.status_code == 200
    text = resp.text

    assert "claude mcp add --transport http searchpipe" in text
    assert "/agent-setup/SKILL.md" in text
    assert "/mcp?api_key=" in text
    assert "RAG" not in text


def test_landing_internal_links(client):
    """首页必须内链到全部公开页（爬虫靠内链发现页面，孤岛页不会被收录）。"""
    text = client.get("/").text
    for path in ["/docs", "/mcp-server", "/pricing", "/faq", "/terms"]:
        assert f'href="{path}"' in text, f"首页缺少到 {path} 的内链"


def test_public_pages_link_back_to_home(client):
    """每个公开页都有一条回首页的路径（避免死胡同页面）。"""
    for path in PUBLIC_PATHS:
        text = client.get(path).text
        assert ('href="/"' in text) or ("site-nav" in text), f"{path} 缺少回首页链接"


def test_landing_try_entry_anonymous(client):
    """落地页含「在线体验」入口：匿名态提示需登录，表单 GET 到 /dashboard?q=。

    共享 TestClient 的 cookie jar 可能被先跑的用例写入有效 session，
    这里显式覆盖一个无效 cookie 来模拟匿名访客（签名校验失败即视为未登录）。
    """
    resp = client.get("/", cookies={"ai_search_session": "invalid"})
    assert resp.status_code == 200
    text = resp.text
    assert "在线体验" in text
    assert 'action="/dashboard"' in text
    assert 'name="q"' in text
    assert "需登录后体验" in text
    assert "已登录，提交后进入控制台" not in text


# ---------- 内容页与结构化数据一致性 ----------


def test_docs_page_structured_data(client):
    """/docs 输出 TechArticle + 面包屑，且正文含参数表与调用示例。"""
    html = client.get("/docs").text
    assert {"TechArticle", "BreadcrumbList"} <= _jsonld_types(_jsonld_blocks(html))
    assert "POST /search" in html
    assert "max_results" in html
    assert "include_answer" in html
    assert "curl" in html and "Python" in html


def test_mcp_page_structured_data(client):
    """/mcp-server 输出 HowTo + 面包屑，正文含各客户端配置命令。"""
    html = client.get("/mcp-server").text
    assert {"HowTo", "HowToStep", "BreadcrumbList"} <= _jsonld_types(_jsonld_blocks(html))
    assert "claude mcp add --transport http searchpipe" in html
    assert "mcpServers" in html
    assert "ai_search_search" in html


def test_faq_page_structured_data_matches_visible_text(client):
    """/faq 的 FAQPage 条目与页面可见问答一一对应。"""
    html = client.get("/faq").text
    payloads = _jsonld_blocks(html)
    assert "FAQPage" in _jsonld_types(payloads)
    assert "BreadcrumbList" in _jsonld_types(payloads)

    for question, answer in seo.FAQ_ITEMS:
        assert question in html
        assert answer[:24] in html

    faq_node = next(
        node
        for payload in payloads
        for node in (payload if isinstance(payload, list) else [payload])
        if node.get("@type") == "FAQPage"
    )
    assert len(faq_node["mainEntity"]) == len(seo.FAQ_ITEMS)


def test_pricing_page_structured_data_matches_visible_prices(client):
    """/pricing 的 Offer 价格必须能在页面上看到（避免结构化数据与实际售价不符）。"""
    html = client.get("/pricing").text
    payloads = _jsonld_blocks(html)
    assert {"Product", "Offer", "WebPage", "BreadcrumbList"} <= _jsonld_types(payloads)

    product = next(
        node
        for payload in payloads
        for node in (payload if isinstance(payload, list) else [payload])
        if node.get("@type") == "Product"
    )
    prices = [offer["price"] for offer in product["offers"]]
    assert len(prices) >= 4
    for price in prices:
        assert f"¥{price}" in html, f"结构化数据里的价格 ¥{price} 未在页面展示"

    # 页面展示的充值档位与费率
    assert f"¥{seo.RECHARGE_RATE}" in html
    for amount, credits in seo.RECHARGE_TIERS:
        assert amount in html and credits in html


def test_terms_page_meta_and_jsonld(client):
    """服务条款页补齐了元信息与结构化数据（整改前该页没有任何 SEO 元信息）。"""
    resp = client.get("/terms")
    assert resp.status_code == 200
    html = resp.text
    head = _head(html)
    assert 'name="description"' in head
    assert 'name="keywords"' in head
    assert 'rel="canonical"' in head
    assert {"WebPage", "BreadcrumbList"} <= _jsonld_types(_jsonld_blocks(html))
    assert "概不退款" not in html  # 2026-09-12 需求 6：法律上无效的声明已移除


# ---------- 私有路径：noindex 响应头 / 索引安全 ----------


@pytest.mark.parametrize(
    "path",
    ["/dashboard/login", "/dashboard/register", "/dashboard", "/admin/users", "/api-docs"],
)
def test_private_paths_send_noindex_header(client, path):
    """需登录/内部页面必须带 X-Robots-Tag: noindex, nofollow。"""
    resp = client.get(path, follow_redirects=False)
    assert resp.headers.get("x-robots-tag") == "noindex, nofollow", path


@pytest.mark.parametrize("path", ["/dashboard/login", "/dashboard/register"])
def test_auth_pages_meta_noindex(client, path):
    """登录/注册页自带 noindex 元标签（响应头之外的页面级声明）。"""
    head = _head(client.get(path).text)
    assert "noindex" in _attr(head, r'<meta name="robots" content="([^"]*)"')


def test_dashboard_docs_redirects_to_public_docs(client):
    """控制台旧文档路径 301 到公开 /docs（老链接不断，且不产生重复内容）。"""
    resp = client.get("/dashboard/docs", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/docs"
    assert client.get("/docs").status_code == 200


# ---------- 站点图标 / 分享图 ----------


@pytest.mark.parametrize(
    ("path", "content_type", "min_size"),
    [
        ("/favicon.ico", "image/", 1000),
        ("/favicon.svg", "image/svg+xml", 100),
        ("/apple-touch-icon.png", "image/png", 1000),
        ("/og-image.png", "image/png", 20000),
    ],
)
def test_site_icons_available(client, path, content_type, min_size):
    """站点图标与 og:image 均为真实文件（整改前 /favicon.ico 是 404）。"""
    resp = client.get(path)
    assert resp.status_code == 200
    assert content_type in resp.headers["content-type"]
    assert len(resp.content) >= min_size
    assert "max-age" in resp.headers.get("cache-control", "")


# ---------- llms.txt / 站长验证 ----------


def test_llms_txt_lists_public_pages(client):
    """llms.txt 面向 AI 问答引擎，必须覆盖全部公开页链接（llms.txt 约定）。"""
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert text.startswith("# SearchPipe")
    for path in PUBLIC_PATHS:
        assert f"({seo.get_base_url()}{path})" in text or path == "/"
    assert "/agent-setup/SKILL.md" in text


def test_verification_metas_only_when_configured(client, monkeypatch):
    """站长验证 meta 未配置时不出现在页面，配置后才渲染（避免空 content 标签）。"""
    from ai_search.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "baidu_site_verification", "code-abc123", raising=False)
    head = _head(client.get("/").text)
    assert '<meta name="baidu-site-verification" content="code-abc123">' in head

    monkeypatch.setattr(settings, "baidu_site_verification", "", raising=False)
    head = _head(client.get("/").text)
    assert "baidu-site-verification" not in head


# ---------- 404 页 ----------


def test_404_renders_html_for_browsers(client):
    """浏览器访问不存在的页面 → 带内链的 HTML 404（而不是裸 JSON）。"""
    resp = client.get("/no-such-page", headers={"Accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    text = resp.text
    assert "页面不存在" in text
    assert 'href="/docs"' in text
    assert "noindex" in _attr(_head(text), r'<meta name="robots" content="([^"]*)"')


def test_404_stays_json_for_api_clients(client):
    """接口调用（Accept 不含 text/html）仍返回 JSON 404，不破坏既有契约。"""
    resp = client.get("/no-such-page", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert "application/json" in resp.headers["content-type"]
    assert resp.json()["detail"] == "Not Found"
