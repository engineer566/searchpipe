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
