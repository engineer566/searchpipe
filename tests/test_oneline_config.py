"""一句话配置（Agent 一键接入）—— 测试。

对应需求 history/20260912.txt #5：一句话配置里默认带上用户的 API Key 与 MCP 链接，
Agent 拿到即可一键配置；这句话本身不在页面展示，只留复制按钮。

覆盖：提示词内容/落地页与控制台三处的「不显示只复制」行为、SKILL.md 指引。
策略同 conftest：真实 PG/Redis，全部经 TestClient → ASGI 内部 task。
"""

import re
import uuid

import pytest

from ai_search.agent_setup import build_agent_prompt, mcp_url
from ai_search.config import get_settings
from ai_search.utils import mailer

_PW = "test-pass-1234"


@pytest.fixture()
def sent_mails(monkeypatch) -> list:
    out: list[dict] = []

    async def fake_send(to: str, subject: str, text: str) -> bool:
        out.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return out


def _verified_dashboard_user(client, sent_mails) -> str:
    """注册 + 验证邮箱 + 控制台登录（留 session cookie）→ 返回邮箱。"""
    email = f"oneliner-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    token = re.search(r"token=([A-Za-z0-9_\-]+)", sent_mails[-1]["text"])
    assert token, sent_mails[-1]
    verify = client.get(f"/auth/verify-email?token={token.group(1)}", follow_redirects=False)
    assert "verify_success=1" in verify.headers["location"]
    client.cookies.clear()
    login = client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert login.status_code == 303, login.text
    return email


# ---------- 提示词内容 ----------


def test_agent_prompt_carries_key_and_mcp_url():
    """一句话配置默认带上 API Key、MCP 链接与接入说明地址。"""
    base = "https://searchpipe.tech"
    key = "sp-" + "a1B2c3D4" * 4
    prompt = build_agent_prompt(base, key)

    assert key in prompt
    assert mcp_url(base, key) in prompt
    assert f"{base}/agent-setup/SKILL.md" in prompt
    assert "ai_search_search" in prompt          # 告诉 Agent 用哪个工具
    assert "you do not need to ask me for any" in prompt  # 一键配置：不再来回索要凭据
    # 尾斜杠不应造成双斜杠
    assert build_agent_prompt(base + "/", key) == prompt


def test_reveal_returns_same_prompt(client, sent_mails):
    """控制台复制按钮拿到的提示词与 agent_setup 渲染结果一致（含当前 Key）。"""
    _verified_dashboard_user(client, sent_mails)
    data = client.get("/api-keys/reveal").json()
    base = get_settings().app_base_url.rstrip("/")
    assert data["agent_prompt"] == build_agent_prompt(base, data["key"])


def test_skill_md_tells_agent_to_reuse_prompt_credentials(client):
    """SKILL.md 指引 Agent：用户提示词里已给 Key/链接时直接用，别再索要。"""
    text = client.get("/agent-setup/SKILL.md").text
    assert "Check the user's prompt first" in text
    assert "Do not ask the user for credentials again" in text
    # 原有契约不回归
    assert "/mcp?api_key=sp-" in text
    assert "ai_search_search" in text


# ---------- API Keys 页：一句话不显示、只留复制按钮 ----------


def test_api_keys_page_has_oneliner_copy_only(client, sent_mails):
    """API Keys 页只有「复制一句话配置」按钮，不渲染提示词与明文 Key。"""
    _verified_dashboard_user(client, sent_mails)
    page = client.get("/dashboard/api-keys")
    assert page.status_code == 200
    text = page.text

    assert 'id="oneliner-copy"' in text
    assert "Copy one-line setup" in text
    # 提示词/明文不入 HTML（复制按钮按需现拉）
    data = client.get("/api-keys/reveal").json()
    assert data["key"] not in text
    assert data["mcp_url"] not in text
    assert data["agent_prompt"] not in text
    # 页面里那个「One-line setup」说明只是文案，不含 Key
    assert "One-line setup" in text


# ---------- 概览页：SKILL.md 链接块改为只复制 ----------


def test_dashboard_home_replaces_skill_link_with_copy_button(client, sent_mails):
    """概览页不再展示提示词/SKILL.md 链接，只留复制按钮与打码命令。"""
    _verified_dashboard_user(client, sent_mails)
    page = client.get("/dashboard")
    assert page.status_code == 200
    text = page.text

    assert 'id="home-oneliner-copy"' in text
    assert "Copy one-line setup" in text
    assert "/agent-setup/SKILL.md" not in text      # 原来的链接块已移除
    assert "或者把下面的链接发给你的 AI Agent" not in text
    # 明文 Key 不进 HTML
    data = client.get("/api-keys/reveal").json()
    assert data["key"] not in text
    assert data["mcp_url"] not in text


# ---------- 落地页：匿名隐藏、登录态可复制 ----------


def test_landing_anonymous_has_no_oneliner_copy(client):
    """匿名访客拿不到 Key：不渲染复制按钮，改为引导登录后用控制台复制。"""
    client.cookies.clear()
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="hero-oneliner-copy"' not in page.text
    assert "Sign in to configure your agent" in page.text
    assert "/dashboard/login?next=%2Fdashboard%2Fapi-keys" in page.text


def test_landing_logged_in_offers_oneliner_copy(client, sent_mails):
    """已登录访客可用一句话复制按钮，且页面不含明文 Key。"""
    _verified_dashboard_user(client, sent_mails)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="hero-oneliner-copy"' in page.text
    data = client.get("/api-keys/reveal").json()
    assert data["key"] not in page.text


def test_public_docs_mentions_oneliner_flow(client):
    """公开文档页指向控制台的一句话配置入口（匿名页只能给链接，不能给 Key）。"""
    client.cookies.clear()
    page = client.get("/docs")
    assert page.status_code == 200
    assert "Copy one-line setup" in page.text
    assert "/dashboard/api-keys" in page.text


def test_dashboard_home_legacy_default_key_guides_to_new_key(client, sent_mails, monkeypatch):
    """默认 Key 是老 Key（无密文）→ 概览卡不给复制按钮，改为引导去 API Keys 新建。

    直接给复制按钮的话，点击会因 /api-keys/reveal 返回 409 而弹错误提示，对老用户不友好。
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from ai_search.api_keys import service as keys_service

    _verified_dashboard_user(client, sent_mails)
    legacy = SimpleNamespace(
        id=_uuid.uuid4(),
        name="默认 Key",
        key_prefix="sp-LEGACY",
        is_default=True,
        key_cipher=None,          # 关键：没有密文
        last_used_at=None,
        revoked_at=None,
        created_at=datetime.now(timezone.utc),
    )

    async def _fake_ensure(db, user_id):
        return legacy

    monkeypatch.setattr(keys_service, "ensure_default_key", _fake_ensure)
    page = client.get("/dashboard")
    assert page.status_code == 200
    text = page.text
    assert "reveal plaintext" in text and "create a new key" in text
    assert 'id="home-oneliner-copy"' not in text
    assert 'id="home-mcp-copy"' not in text
