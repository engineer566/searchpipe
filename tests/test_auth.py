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
    assert "已发送" in resp.json()["detail"]
    assert sent_mails == []


def test_reset_password_full_flow(client, sent_mails):
    email = _unique_email()
    _register(client, email)

    # 发起重置 → 收到含链接的邮件
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert len(sent_mails) == 1
    assert sent_mails[0]["to"] == email
    token = _extract_reset_token(sent_mails[0])

    # 冷却期内再次请求 → 不再发信
    resp = client.post("/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert len(sent_mails) == 1

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
    assert "确认密码" in resp.text


def test_dashboard_register_success(client):
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW},
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
        data={"email": email, "password": _PW, "password_confirm": _PW},
    )
    assert resp.status_code == 200
    assert "该邮箱已注册" in resp.text


def test_dashboard_register_password_mismatch(client):
    resp = client.post(
        "/dashboard/register",
        data={
            "email": _unique_email(),
            "password": _PW,
            "password_confirm": "different-99",
        },
    )
    assert resp.status_code == 200
    assert "两次输入的密码不一致" in resp.text


def test_dashboard_login_wrong_password_renders_error(client):
    email = _unique_email()
    _register(client, email)
    resp = client.post(
        "/dashboard/login", data={"email": email, "password": "wrong-pass-1"}
    )
    assert resp.status_code == 200  # 页面内报错，而非裸 401
    assert "邮箱或密码错误" in resp.text


def test_dashboard_forgot_and_reset_pages(client, sent_mails):
    email = _unique_email()
    _register(client, email)

    # 忘记密码页
    resp = client.get("/dashboard/forgot-password")
    assert resp.status_code == 200

    # 提交 → 统一话术 + 发信
    resp = client.post("/dashboard/forgot-password", data={"email": email})
    assert resp.status_code == 200
    assert "已发送" in resp.text
    assert len(sent_mails) == 1
    token = _extract_reset_token(sent_mails[0])

    # 重置页：有效 token 显示表单，伪造 token 显示失效
    resp = client.get(f"/dashboard/reset-password?token={token}")
    assert resp.status_code == 200
    assert "设置新密码" in resp.text or "新密码" in resp.text
    assert "无效或已过期" not in resp.text

    resp = client.get("/dashboard/reset-password?token=forged")
    assert resp.status_code == 200
    assert "无效或已过期" in resp.text

    # 两次密码不一致 → 页面报错
    resp = client.post(
        "/dashboard/reset-password",
        data={"token": token, "password": _PW_NEW, "password_confirm": "different-99"},
    )
    assert resp.status_code == 200
    assert "两次输入的密码不一致" in resp.text

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
