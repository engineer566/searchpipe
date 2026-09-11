"""注册 / 登录 / 忘记密码 —— API 与 Dashboard 页面流程测试。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB/Redis，不直连。邮件通过 monkeypatch mailer.send_mail
捕获（发信在请求 task 内执行，无跨 loop 问题）。
"""

import re
import uuid

import pytest

from ai_search.utils import mailer

_PW = "test-pass-1234"
_PW_NEW = "new-pass-5678"


def _unique_email() -> str:
    return f"auth-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture()
def sent_mails(monkeypatch) -> list:
    """捕获 send_mail 调用；password_reset 经模块属性调用，patch 模块级函数即可。"""
    out: list[dict] = []

    async def fake_send(to: str, subject: str, text: str) -> bool:
        out.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return out


def _extract_reset_token(mail: dict) -> str:
    m = re.search(r"token=([A-Za-z0-9_\-]+)", mail["text"])
    assert m, f"重置邮件中未找到 token: {mail}"
    return m.group(1)


def _register(client, email: str, password: str = _PW) -> dict:
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- API：注册 / 登录 / 刷新 ----------


def test_register_and_me(client):
    email = _unique_email()
    tokens = _register(client, email.upper())  # 大写注册应被规范化为小写
    resp = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == email


def test_register_duplicate_409(client):
    email = _unique_email()
    _register(client, email)
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 409
    # 大小写变体同样 409
    resp = client.post("/auth/register", json={"email": email.upper(), "password": _PW})
    assert resp.status_code == 409


def test_register_short_password_422(client):
    resp = client.post(
        "/auth/register", json={"email": _unique_email(), "password": "short"}
    )
    assert resp.status_code == 422


def test_login_success_and_wrong_password(client):
    email = _unique_email()
    _register(client, email)

    resp = client.post("/auth/login", json={"email": email, "password": "wrong-pass-1"})
    assert resp.status_code == 401

    resp = client.post("/auth/login", json={"email": email.upper(), "password": _PW})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]


def test_refresh_token_flow(client):
    tokens = _register(client, _unique_email())
    resp = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200, resp.text
    new_access = resp.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert resp.status_code == 200

    resp = client.post("/auth/refresh", json={"refresh_token": "not-a-token"})
    assert resp.status_code == 401


# ---------- API：忘记密码 / 重置密码 ----------


def test_forgot_password_neutral_response(client, sent_mails):
    """不存在的邮箱也返回 200（防枚举），且不发信。"""
    resp = client.post("/auth/forgot-password", json={"email": _unique_email()})
    assert resp.status_code == 200
    assert "reset link has been sent" in resp.json()["detail"]
    assert sent_mails == []


def test_reset_password_full_flow(client, sent_mails):
    email = _unique_email()
    _register(client, email)  # 注册会发一封验证邮件
    initial = len(sent_mails)

    # 发起重置 → 收到含链接的邮件
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert len(sent_mails) == initial + 1
    assert sent_mails[-1]["to"] == email
    token = _extract_reset_token(sent_mails[-1])

    # 冷却期内再次请求 → 不再发信
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert len(sent_mails) == initial + 1

    # 改密 → 旧密码 401、新密码 200
    resp = client.post(
        "/auth/reset-password", json={"token": token, "new_password": _PW_NEW}
    )
    assert resp.status_code == 200, resp.text
    resp = client.post("/auth/login", json={"email": email, "password": _PW})
    assert resp.status_code == 401
    resp = client.post("/auth/login", json={"email": email, "password": _PW_NEW})
    assert resp.status_code == 200

    # token 一次性：复用 400
    resp = client.post(
        "/auth/reset-password", json={"token": token, "new_password": "another-pass-9"}
    )
    assert resp.status_code == 400


def test_reset_password_fake_token_400(client):
    resp = client.post(
        "/auth/reset-password",
        json={"token": "forged-token", "new_password": _PW_NEW},
    )
    assert resp.status_code == 400


# ---------- Dashboard 页面 ----------


def test_login_page_email_only(client):
    resp = client.get("/dashboard/login")
    assert resp.status_code == 200
    assert "GitHub" not in resp.text
    assert "微信" not in resp.text
    assert "/dashboard/forgot-password" in resp.text
    assert "/dashboard/register" in resp.text


def test_register_page_get(client):
    resp = client.get("/dashboard/register")
    assert resp.status_code == 200
    assert "Confirm password" in resp.text


def test_dashboard_register_success(client):
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "ai_search_session" in resp.cookies
    # 携带 session cookie 可访问控制台
    resp = client.get("/dashboard")
    assert resp.status_code == 200, resp.text


def test_dashboard_register_duplicate_email(client):
    email = _unique_email()
    _register(client, email)
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
    )
    assert resp.status_code == 200
    assert "already registered" in resp.text


def test_dashboard_register_password_mismatch(client):
    resp = client.post(
        "/dashboard/register",
        data={
            "email": _unique_email(),
            "password": _PW,
            "password_confirm": "different-99",
            "agree_terms": "on",
        },
    )
    assert resp.status_code == 200
    assert "Passwords do not match" in resp.text


def test_dashboard_login_wrong_password_renders_error(client):
    email = _unique_email()
    _register(client, email)
    resp = client.post(
        "/dashboard/login", data={"email": email, "password": "wrong-pass-1", "agree_terms": "on"}
    )
    assert resp.status_code == 200  # 页面内报错，而非裸 401
    assert "Incorrect password" in resp.text
    assert email in resp.text  # 邮箱回填


def test_dashboard_login_unregistered_email_hint(client):
    """控制台显式区分：未注册邮箱提示去注册（API 仍统一 401 防枚举）。"""
    resp = client.post(
        "/dashboard/login", data={"email": _unique_email(), "password": _PW, "agree_terms": "on"}
    )
    assert resp.status_code == 200
    assert "not registered" in resp.text
    # API 层保持统一话术
    resp = client.post("/auth/login", json={"email": _unique_email(), "password": _PW})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_dashboard_forgot_and_reset_pages(client, sent_mails):
    email = _unique_email()
    _register(client, email)

    # 忘记密码页
    resp = client.get("/dashboard/forgot-password")
    assert resp.status_code == 200

    # 提交 → 统一话术 + 发信（注册时已发一封验证邮件）
    resp = client.post("/dashboard/forgot-password", data={"email": email})
    assert resp.status_code == 200
    assert "reset link has been sent" in resp.text
    assert len(sent_mails) == 2
    token = _extract_reset_token(sent_mails[-1])

    # 重置页：有效 token 显示表单，伪造 token 显示失效
    resp = client.get(f"/dashboard/reset-password?token={token}")
    assert resp.status_code == 200
    assert "Set a new password" in resp.text or "New password" in resp.text
    assert "invalid or has expired" not in resp.text

    resp = client.get("/dashboard/reset-password?token=forged")
    assert resp.status_code == 200
    assert "invalid or has expired" in resp.text

    # 两次密码不一致 → 页面报错
    resp = client.post(
        "/dashboard/reset-password",
        data={"token": token, "password": _PW_NEW, "password_confirm": "different-99"},
    )
    assert resp.status_code == 200
    assert "Passwords do not match" in resp.text

    # 改密成功 → 303 跳登录页带提示；新密码可登录
    resp = client.post(
        "/dashboard/reset-password",
        data={"token": token, "password": _PW_NEW, "password_confirm": _PW_NEW},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "reset=1" in resp.headers["location"]
    resp = client.post("/auth/login", json={"email": email, "password": _PW_NEW})
    assert resp.status_code == 200


# ---------- 快速搜索体验入口（落地页 → 登录回跳 → /dashboard?q= 预填） ----------


def test_dashboard_q_redirects_anonymous_to_login_with_next(client):
    """未登录访问 /dashboard?q=xxx → 303 到登录页，next 携带完整 /dashboard?q=xxx。"""
    resp = client.get(
        "/dashboard",
        params={"q": "fastapi 部署"},
        follow_redirects=False,
        # 共享 TestClient 的 cookie jar 可能被先跑的用例写入有效 session，
        # 覆盖无效 cookie 模拟匿名访客（签名校验失败即未登录）
        cookies={"ai_search_session": "invalid"},
    )
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/dashboard/login?next=")
    # next 里是 URL 编码后的 /dashboard?q=...
    assert "%2Fdashboard%3Fq%3D" in loc


def test_dashboard_login_next_redirect_and_open_redirect_blocked(client):
    """登录成功按 next（站内路径）回跳；站外 next 被忽略，回退 /dashboard。"""
    email = _unique_email()
    _register(client, email)

    resp = client.post(
        "/dashboard/login",
        data={
            "email": email,
            "password": _PW,
            "agree_terms": "on",
            "next": "/dashboard?q=fastapi",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard?q=fastapi"

    # open redirect 防护：站外/协议相对地址一律回退 /dashboard
    for evil in ("https://evil.example.com", "//evil.example.com"):
        resp = client.post(
            "/dashboard/login",
            data={"email": email, "password": _PW, "agree_terms": "on", "next": evil},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"


def test_dashboard_register_next_redirect(client):
    """注册成功保留 next：跳登录页（registered=1&next=...），登录后回跳体验页。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={
            "email": email,
            "password": _PW,
            "password_confirm": _PW,
            "agree_terms": "on",
            "next": "/dashboard?q=hello",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    loc = resp.headers["location"]
    assert loc.startswith("/dashboard/login?registered=1")
    assert "next=%2Fdashboard%3Fq%3Dhello" in loc

    # 登录后按 next 回跳
    resp = client.post(
        "/dashboard/login",
        data={
            "email": email,
            "password": _PW,
            "agree_terms": "on",
            "next": "/dashboard?q=hello",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard?q=hello"


def test_dashboard_q_prefill_logged_in(client):
    """已登录访问 /dashboard?q=xxx → 200，页面内嵌 initial_q 供 JS 预填并自动搜索。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # cookie 已写入 jar
    resp = client.get("/dashboard", params={"q": "fastapi deploy"})
    assert resp.status_code == 200, resp.text
    assert 'initialQ="fastapi deploy"' in resp.text  # initial_q 渲染进页面脚本
    assert "requestSubmit" in resp.text


def test_landing_try_entry_logged_in(client):
    """已登录用户访问落地页：体验入口提示直接进入控制台。"""
    resp = client.post(
        "/dashboard/register",
        data={
            "email": _unique_email(),
            "password": _PW,
            "password_confirm": _PW,
            "agree_terms": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303  # 注册即登录，cookie 写入 jar
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Try it live" in resp.text
    assert "signed in" in resp.text


# ---------- 邮箱验证 ----------


def _extract_verify_token(mail: dict) -> str:
    m = re.search(r"token=([A-Za-z0-9_\-]+)", mail["text"])
    assert m, f"验证邮件中未找到 token: {mail}"
    return m.group(1)


def test_register_sends_verification_email(client, sent_mails):
    """注册成功后应发送验证邮件。"""
    email = _unique_email()
    tokens = _register(client, email)
    assert len(sent_mails) == 1
    assert sent_mails[0]["to"] == email
    assert "Verification" in sent_mails[0]["subject"] or "verify" in sent_mails[0]["subject"].lower()
    # 提取 token 并验证链接格式
    token = _extract_verify_token(sent_mails[0])
    assert token
    assert "/auth/verify-email?token=" in sent_mails[0]["text"]


def test_verify_email_success(client, sent_mails):
    """验证邮箱成功 → 跳转登录页带成功提示。"""
    email = _unique_email()
    _register(client, email)
    assert len(sent_mails) == 1
    token = _extract_verify_token(sent_mails[0])

    # 访问验证链接
    resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verify_success=1" in resp.headers["location"]

    # 验证后 /me 应返回 email_verified=true
    tokens = client.post("/auth/login", json={"email": email, "password": _PW}).json()
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["email_verified"] is True


def test_verify_email_invalid_token(client):
    """无效 token → 跳转登录页带错误提示。"""
    resp = client.get("/auth/verify-email?token=forged-token", follow_redirects=False)
    assert resp.status_code == 302
    assert "verify_error=1" in resp.headers["location"]


def test_verify_email_already_verified(client, sent_mails):
    """已验证的邮箱再次验证 → 跳转登录页带已验证提示。"""
    email = _unique_email()
    _register(client, email)
    token = _extract_verify_token(sent_mails[0])

    # 第一次验证
    resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verify_success=1" in resp.headers["location"]

    # 再次使用同一 token（已消费，应失败）
    resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "verify_error=1" in resp.headers["location"]


def test_dashboard_register_shows_verification_hint(client):
    """Dashboard 注册成功后跳转到登录页带 registered=1 提示。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "registered=1" in resp.headers["location"]

    # 跟随重定向到登录页，应显示验证邮件提示
    resp = client.get(resp.headers["location"])
    assert resp.status_code == 200
    assert "verification email" in resp.text or "verify" in resp.text.lower()


def test_dashboard_unverified_user_sees_warning(client, sent_mails):
    """未验证邮箱的用户访问 dashboard 应显示警告提示。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    # 注册成功 → 303 登录页（registered=1），但 session cookie 已写入
    assert resp.status_code == 303
    assert "registered=1" in resp.headers["location"]

    # 带 cookie 访问 dashboard → 未验证警告卡片
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Email not verified" in resp.text
    assert "Re-send verification email" in resp.text


def test_resend_verification_email(client, sent_mails, monkeypatch):
    """重发验证邮件功能。"""
    from ai_search.auth import email_verification

    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    # 注册 → 303 登录页，session cookie 已写入（已登录）
    assert resp.status_code == 303
    initial_count = len(sent_mails)

    # 注册时的发信已占 60s 冷却；换掉冷却键前缀以直接测重发成功路径
    monkeypatch.setattr(email_verification, "_COOLDOWN_PREFIX", "verify:test-cooldown:")

    # 重发验证邮件
    resp = client.post("/dashboard/resend-verification", follow_redirects=False)
    assert resp.status_code == 303
    assert "resend_success=1" in resp.headers["location"]

    # 应多了一封邮件
    assert len(sent_mails) == initial_count + 1
    assert sent_mails[-1]["to"] == email


def test_api_resend_verification(client, sent_mails, monkeypatch):
    """API 端点重发验证邮件。"""
    from ai_search.auth import email_verification

    email = _unique_email()
    tokens = _register(client, email)  # 注册即发验证邮件并占 60s 冷却
    initial_count = len(sent_mails)

    # 换掉冷却键前缀以直接测重发成功路径
    monkeypatch.setattr(email_verification, "_COOLDOWN_PREFIX", "verify:test-cooldown:")

    resp = client.post(
        "/auth/resend-verification",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 200
    assert "resent" in resp.json()["detail"]
    assert len(sent_mails) == initial_count + 1


def test_api_resend_verification_already_verified(client, sent_mails):
    """已验证邮箱重发验证邮件应返回 400。"""
    email = _unique_email()
    tokens = _register(client, email)
    token = _extract_verify_token(sent_mails[0])

    # 先验证邮箱
    client.get(f"/auth/verify-email?token={token}")

    # 再尝试重发
    resp = client.post(
        "/auth/resend-verification",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 400
    assert "already verified" in resp.json()["detail"]
