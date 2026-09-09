"""SEO 相关测试：robots.txt、sitemap.xml、landing 页 meta 标签与语义化 HTML。"""

import xml.etree.ElementTree as ET


def test_robots_txt_ok(client):
    """robots.txt 返回 200 且包含 Disallow 规则。"""
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    text = resp.text
    assert "User-agent: *" in text
    assert "Disallow: /dashboard" in text
    assert "Disallow: /admin" in text
    assert "Disallow: /api" in text
    assert "Disallow: /auth" in text
    assert "Disallow: /mcp" in text
    assert "Sitemap:" in text


def test_sitemap_xml_ok(client):
    """sitemap.xml 返回 200 且为合法 XML，包含首页 URL。"""
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    # 默认命名空间
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [loc.text for loc in root.findall("sm:url/sm:loc", ns)]
    assert any(u.endswith("/") for u in urls)
    assert any("/dashboard/login" in u for u in urls)
    assert any("/dashboard/register" in u for u in urls)
    assert any("/dashboard/docs" in u for u in urls)


def test_landing_meta_tags(client):
    """landing 页包含 description、og: 系列标签与唯一 h1。"""
    resp = client.get("/")
    assert resp.status_code == 200
    text = resp.text

    # description
    assert '<meta name="description"' in text
    # Open Graph
    assert '<meta property="og:title"' in text
    assert '<meta property="og:description"' in text
    assert '<meta property="og:type"' in text
    assert '<meta property="og:url"' in text
    # Twitter Card
    assert '<meta name="twitter:card"' in text
    # canonical
    assert '<link rel="canonical"' in text

    # 唯一 h1
    assert text.count("<h1>") == 1
    assert text.count("</h1>") == 1

    # lang="zh-CN"
    assert '<html lang="zh-CN">' in text

    # 语义化：main 标签、section 带 aria-labelledby
    assert "<main>" in text
    assert "</main>" in text
    assert 'aria-labelledby=' in text


def test_landing_agent_first_positioning(client):
    """落地页以 Agent 接入为第一卖点：首屏是 MCP 配置，不再宣传 RAG。"""
    resp = client.get("/")
    assert resp.status_code == 200
    text = resp.text

    # 首屏 Hero 即给出 MCP 一键接入命令
    assert "claude mcp add --transport http searchpipe" in text
    # Agent 自服务配置入口
    assert "/agent-setup/SKILL.md" in text
    # MCP 链接内嵌 Key
    assert "/mcp?api_key=" in text
    # 弱化 RAG：不再作为卖点出现
    assert "RAG" not in text


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
