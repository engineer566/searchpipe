"""Agent Setup 端点测试。

覆盖：
- GET /agent-setup/SKILL.md 返回 200、text/plain、内容含 MCP 端点与配置示例
- docs 页含「一句话配置 MCP」引导文案
"""

from fastapi.testclient import TestClient


def test_agent_setup_skill_md(client: TestClient):
    """GET /agent-setup/SKILL.md 返回 200，内容含关键配置信息。"""
    resp = client.get("/agent-setup/SKILL.md")
    assert resp.status_code == 200
    # Content-Type 应为 text/plain（PlainTextResponse 默认）或 text/markdown
    ct = resp.headers.get("content-type", "")
    assert "text/plain" in ct or "text/markdown" in ct

    text = resp.text
    # 必须包含 MCP 端点
    assert "/mcp" in text
    # 必须推荐 URL 内嵌 API Key 的 MCP 链接（Tavily 式远程 MCP）
    assert "/mcp?api_key=sp-" in text
    # 必须包含 API Key 获取指引
    assert "sp-" in text
    # 必须包含各客户端配置示例
    assert "Claude Code" in text
    assert "Cursor" in text
    # 必须包含工具名
    assert "ai_search_search" in text
    # 必须包含验证指引
    assert "配置验证" in text or "故障排查" in text


def test_agent_setup_no_auth_required(client: TestClient):
    """SKILL.md 端点公开，无需鉴权。"""
    resp = client.get("/agent-setup/SKILL.md")
    assert resp.status_code == 200


def test_docs_page_has_agent_setup_hint(client: TestClient):
    """控制台文档页含「一句话配置 MCP」引导文案。"""
    # docs 页需要 session cookie，先走 dashboard 注册流程
    import uuid
    email = f"setup-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": "test-pass-1234", "password_confirm": "test-pass-1234", "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # 携带 session cookie 访问 docs 页
    resp = client.get("/dashboard/docs")
    assert resp.status_code == 200
    text = resp.text
    # 引导文案
    assert "一句话配置" in text or "agent-setup" in text
    assert "SKILL.md" in text


def test_api_keys_page_shows_mcp_link(client: TestClient):
    """API Keys 页展示 MCP 链接（URL 内嵌 Key 格式）。"""
    import uuid
    email = f"mcpkeys-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": "test-pass-1234", "password_confirm": "test-pass-1234", "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp = client.get("/dashboard/api-keys")
    assert resp.status_code == 200
    # 页面头部与创建弹窗均应包含 MCP 链接格式说明
    assert "/mcp?api_key=" in resp.text
