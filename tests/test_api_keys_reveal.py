"""默认 API Key / 明文可查看 / MCP 配置选 Key —— 测试。

对应需求 history/20260912.txt #4：
- 邮箱验证完成后自动生成一把默认 API Key（is_default）
- Key 明文可随时查看（平时隐藏），不再是一次性展示
- MCP 链接可用复选框切换其它 API Key

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app 内部 task
访问 DB/Redis；邮件经 monkeypatch mailer.send_mail 捕获（发信在请求 task 内）。
"""

import re
import uuid

import pytest

from ai_search.api_keys.service import DEFAULT_KEY_NAME
from ai_search.config import get_settings
from ai_search.utils import mailer

_PW = "test-pass-1234"


def _unique_email(prefix: str = "keys") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture()
def sent_mails(monkeypatch) -> list:
    """捕获邮件（注册验证链接里的 token 要靠它取）。"""
    out: list[dict] = []

    async def fake_send(to: str, subject: str, text: str) -> bool:
        out.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return out


def _register(client, email: str) -> str:
    """API 注册 → 返回 JWT。"""
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _verify_email(client, mail: dict) -> None:
    """点验证链接完成邮箱验证。"""
    m = re.search(r"token=([A-Za-z0-9_\-]+)", mail["text"])
    assert m, f"验证邮件里没有 token：{mail}"
    resp = client.get(f"/auth/verify-email?token={m.group(1)}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verify_success=1" in resp.headers["location"]


def _login_jwt(client, email: str) -> str:
    resp = client.post("/auth/login", json={"email": email, "password": _PW})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _verified_user(client, sent_mails, prefix: str = "keys") -> tuple[str, str]:
    """注册 + 验证邮箱 → 返回 (email, jwt)。"""
    email = _unique_email(prefix)
    _register(client, email)
    assert sent_mails, "注册后应发出验证邮件"
    _verify_email(client, sent_mails[-1])
    return email, _login_jwt(client, email)


def _list_keys(client, jwt: str) -> list[dict]:
    resp = client.get("/api-keys", headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 加密存储（无 DB 单元测试）----------


def test_crypto_roundtrip_and_failures():
    """key_cipher 加解密：可逆；缺失/损坏密文返回 None 而不抛错。"""
    from ai_search.api_keys.crypto import decrypt_key, encrypt_key

    raw = "sp-" + "a1B2c3D4" * 4
    cipher = encrypt_key(raw)
    assert cipher != raw and raw not in cipher
    assert decrypt_key(cipher) == raw
    assert decrypt_key(None) is None
    assert decrypt_key("not-a-fernet-token") is None


# ---------- 默认 Key：邮箱验证后自动生成 ----------


def test_default_key_created_after_email_verified(client, sent_mails):
    """邮箱验证通过即自动生成「默认 Key」，且标记为默认。"""
    email, jwt = _verified_user(client, sent_mails, "defkey")
    keys = _list_keys(client, jwt)
    assert len(keys) == 1, keys
    assert keys[0]["name"] == DEFAULT_KEY_NAME
    assert keys[0]["is_default"] is True
    assert keys[0]["revoked_at"] is None
    assert keys[0]["key_prefix"].startswith("sp-")


def test_unverified_user_has_no_default_key(client, sent_mails):
    """未验证邮箱的用户不自动生成 Key（页面提示先验证）。"""
    email = _unique_email("unverified")
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/dashboard/api-keys")
    assert page.status_code == 200
    assert "A default key is generated automatically once your email is verified" in page.text
    assert 'class="mcp-key-pick" value' not in page.text


def test_manual_key_is_not_default(client, sent_mails):
    """已有默认 Key 时，手动创建的 Key 不抢默认标记。"""
    email, jwt = _verified_user(client, sent_mails, "manual")
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = client.post("/api-keys", json={"name": "生产环境"}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["is_default"] is False
    keys = _list_keys(client, jwt)
    defaults = [k for k in keys if k["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == DEFAULT_KEY_NAME


def test_first_manual_key_becomes_default(client):
    """老用户（无任何 Key）第一把手动创建的 Key 自动成为默认 Key。"""
    email = _unique_email("firstkey")
    jwt = _register(client, email)
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = client.post("/api-keys", json={"name": "手工 Key"}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    assert resp.json()["is_default"] is True


# ---------- 明文可查看（不再一次性）----------


def test_reveal_default_key(client, sent_mails):
    """默认 Key 明文可查看，并附带现成 MCP 链接与一句话配置。"""
    email, jwt = _verified_user(client, sent_mails, "reveal")
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = client.get("/api-keys/reveal", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["key"].startswith("sp-")
    assert len(data["key"]) == 35  # sp- + 32
    assert data["key_prefix"] == data["key"][:8]
    base = get_settings().app_base_url.rstrip("/")
    assert data["mcp_url"] == f"{base}/mcp?api_key={data['key']}"
    assert data["agent_prompt"].count(data["key"]) >= 1
    assert f"{base}/agent-setup/SKILL.md" in data["agent_prompt"]
    # 同一把默认 Key：重复读取 id 不变（幂等，不会每次新建）
    again = client.get("/api-keys/reveal", headers=headers).json()
    assert again["id"] == data["id"] and again["key"] == data["key"]


def test_reveal_specific_key(client, sent_mails):
    """可查看指定 Key 的明文（MCP 配置卡片切换 Key 用的就是它）。"""
    email, jwt = _verified_user(client, sent_mails, "reveal2")
    headers = {"Authorization": f"Bearer {jwt}"}
    created = client.post("/api-keys", json={"name": "Cursor 用"}, headers=headers).json()
    resp = client.get(f"/api-keys/{created['id']}/reveal", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["key"] == created["key"]
    assert resp.json()["name"] == "Cursor 用"


def test_reveal_available_with_session_cookie(client, sent_mails):
    """控制台登录态（session cookie）也能查看明文——页面「显示」按钮走的就是它。"""
    email, _ = _verified_user(client, sent_mails, "cookie")
    client.cookies.clear()
    resp = client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp = client.get("/api-keys/reveal")
    assert resp.status_code == 200, resp.text
    assert resp.json()["key"].startswith("sp-")


def test_reveal_requires_login(client, sent_mails):
    """未登录不带凭据 → 401。"""
    _verified_user(client, sent_mails, "noauth")
    client.cookies.clear()
    resp = client.get("/api-keys/reveal")
    assert resp.status_code == 401


def test_reveal_rejects_api_key_auth(client, test_user_creds):
    """API Key 本身不能用来读 Key 明文（Key 不能自举读取）。"""
    client.cookies.clear()
    resp = client.get(
        "/api-keys/reveal",
        headers={"Authorization": f"Bearer {test_user_creds['api_key']}"},
    )
    assert resp.status_code == 401


def test_reveal_other_users_key_404(client, sent_mails, test_user_creds):
    """不能查看别人的 Key：越权 → 404（不泄漏归属）。"""
    email, jwt = _verified_user(client, sent_mails, "cross")
    headers = {"Authorization": f"Bearer {jwt}"}
    other = client.get(
        "/api-keys", headers={"Authorization": f"Bearer {test_user_creds['jwt']}"}
    ).json()[0]
    resp = client.get(f"/api-keys/{other['id']}/reveal", headers=headers)
    assert resp.status_code == 404
    # 非法 id 同样 404，不 500
    resp = client.get("/api-keys/not-a-uuid/reveal", headers=headers)
    assert resp.status_code == 404


def test_reveal_legacy_key_without_cipher_409(client, sent_mails, monkeypatch):
    """上线前创建的老 Key 没有密文 → 409 提示重建，而不是 500。"""
    email, jwt = _verified_user(client, sent_mails, "legacy")
    headers = {"Authorization": f"Bearer {jwt}"}
    import ai_search.api_keys.routes as routes

    monkeypatch.setattr(routes, "key_plaintext", lambda api_key: None)
    resp = client.get("/api-keys/reveal", headers=headers)
    assert resp.status_code == 409
    assert "create a new one" in resp.json()["detail"]


def test_revoked_key_cannot_be_revealed(client, sent_mails):
    """已吊销的 Key 不再提供明文。"""
    email, jwt = _verified_user(client, sent_mails, "revoked")
    headers = {"Authorization": f"Bearer {jwt}"}
    created = client.post("/api-keys", json={"name": "临时"}, headers=headers).json()
    assert client.delete(f"/api-keys/{created['id']}", headers=headers).status_code == 204
    assert client.get(f"/api-keys/{created['id']}/reveal", headers=headers).status_code == 404


# ---------- 吊销默认 Key：自动提升下一把 ----------


def test_revoke_default_promotes_latest(client, sent_mails):
    """吊销默认 Key 后，剩下最新的一把自动成为默认 Key。"""
    email, jwt = _verified_user(client, sent_mails, "promote")
    headers = {"Authorization": f"Bearer {jwt}"}
    default_key = _list_keys(client, jwt)[0]
    assert default_key["is_default"] is True
    second = client.post("/api-keys", json={"name": "备选"}, headers=headers).json()

    assert client.delete(f"/api-keys/{default_key['id']}", headers=headers).status_code == 204
    keys = _list_keys(client, jwt)
    assert [k["id"] for k in keys if k["is_default"]] == [second["id"]]
    # 新默认能正常取明文
    resp = client.get("/api-keys/reveal", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == second["id"]
    assert resp.json()["key"] == second["key"]


def test_revoke_all_then_reveal_creates_new_default(client, sent_mails):
    """全部吊销后再取默认 Key：自动新建一把（保证 MCP 配置始终可用）。"""
    email, jwt = _verified_user(client, sent_mails, "recreate")
    headers = {"Authorization": f"Bearer {jwt}"}
    for key in _list_keys(client, jwt):
        client.delete(f"/api-keys/{key['id']}", headers=headers)
    resp = client.get("/api-keys/reveal", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    valid = [k for k in _list_keys(client, jwt) if k["revoked_at"] is None]
    assert len(valid) == 1 and valid[0]["id"] == data["id"]
    assert valid[0]["is_default"] is True


# ---------- 控制台页面 ----------


def test_api_keys_page_has_mcp_config_picker(client, sent_mails):
    """API Keys 页：Key 明文默认打码 + MCP 配置卡片带复选框（默认勾选默认 Key）。"""
    email, _ = _verified_user(client, sent_mails, "page")
    client.cookies.clear()
    client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    page = client.get("/dashboard/api-keys")
    assert page.status_code == 200
    text = page.text
    assert "MCP setup" in text
    assert 'class="mcp-key-pick" value' in text
    assert "checked" in text  # 默认 Key 预勾选
    # 必须断言渲染出的元素本身：类名只出现在内联 JS 时也会「命中」，测不出模板条件写错
    assert 'class="btn btn-sm key-reveal-btn"' in text  # 可点击查看明文
    assert "Not revealable" not in text
    assert "Show" in text
    # 页面本身不渲染明文（只渲染前缀掩码）
    keys = _list_keys(client, _login_jwt(client, email))
    plain = client.get("/api-keys/reveal").json()["key"]
    assert plain not in text
    assert keys[0]["key_prefix"] + "…" in text


def test_dashboard_home_uses_default_key_without_leaking_plaintext(client, sent_mails):
    """概览页 MCP 命令用默认 Key 打码展示，明文不进 HTML（复制时才现拉）。"""
    email, _ = _verified_user(client, sent_mails, "home")
    client.cookies.clear()
    client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "home-mcp-copy" in page.text
    assert "/mcp?api_key=" in page.text
    plain = client.get("/api-keys/reveal").json()["key"]
    assert plain not in page.text


def test_ensure_default_key_is_idempotent(client, sent_mails):
    """重复触发 ensure_default_key（页面访问 + reveal）不会产生多余 Key。"""
    email, jwt = _verified_user(client, sent_mails, "idem")
    headers = {"Authorization": f"Bearer {jwt}"}
    before = len(_list_keys(client, jwt))
    for _ in range(3):
        assert client.get("/api-keys/reveal", headers=headers).status_code == 200
    assert len(_list_keys(client, jwt)) == before == 1


def test_legacy_key_reports_not_viewable(client, sent_mails, monkeypatch):
    """上线前创建的老 Key（无 key_cipher）→ 列表 viewable=false，前端据此隐藏「显示」。

    用轻量替身注入 list_keys，避免绕开 conftest「夹具不直连 DB」的约束。
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import ai_search.api_keys.routes as routes

    email, jwt = _verified_user(client, sent_mails, "legacyview")
    headers = {"Authorization": f"Bearer {jwt}"}

    legacy = SimpleNamespace(
        id=_uuid.uuid4(),
        name="老 Key",
        key_prefix="sp-LEGACY",
        is_default=True,
        key_cipher=None,          # 关键：没有密文
        last_used_at=None,
        revoked_at=None,
        created_at=datetime.now(timezone.utc),
    )
    async def _fake_list(db, user_id):
        return [legacy]

    monkeypatch.setattr(routes, "list_keys", _fake_list)
    items = client.get("/api-keys", headers=headers).json()
    assert items[0]["viewable"] is False
    # 正常新 Key 应 viewable=true（同一接口的对照组）
    monkeypatch.undo()
    fresh = client.get("/api-keys", headers=headers).json()
    assert all(item["viewable"] is True for item in fresh)


def test_api_keys_page_shows_not_viewable_for_legacy_key(client, sent_mails, monkeypatch):
    """老 Key（key_cipher 为空）→ 列表渲染灰色「不可查看」，不渲染「显示」按钮。

    模板遍历的是 ORM 对象（不是 API 的 KeyItem），必须按 key_cipher 判定——
    2026-09-12 生产实测踩过：写成 k.viewable 时条件恒为假，所有 Key 都显示「不可查看」。
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from ai_search.api_keys import service as keys_service

    email, _ = _verified_user(client, sent_mails, "legacyui")
    client.cookies.clear()
    client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )

    legacy = SimpleNamespace(
        id=_uuid.uuid4(),
        name="老 Key",
        key_prefix="sp-LEGACY",
        is_default=True,
        key_cipher=None,          # 关键：没有密文
        last_used_at=None,
        revoked_at=None,
        created_at=datetime.now(timezone.utc),
    )

    async def _fake_list(db, user_id):
        return [legacy]

    monkeypatch.setattr(keys_service, "list_keys", _fake_list)
    page = client.get("/dashboard/api-keys")
    assert page.status_code == 200
    text = page.text
    assert "Not revealable" in text
    assert 'class="btn btn-sm key-reveal-btn"' not in text
