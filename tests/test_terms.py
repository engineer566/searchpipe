"""服务条款页与登录/注册勾选框校验测试。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB/Redis，不直连。
"""

import uuid

import pytest

_PW = "test-pass-1234"


def _unique_email() -> str:
    return f"terms-{uuid.uuid4().hex[:8]}@example.com"


def _register_api(client, email: str, password: str = _PW) -> dict:
    """通过 API 注册用户（绕过 dashboard 的 terms 勾选校验）。"""
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- 服务条款页 ----------


def test_terms_page_200(client):
    resp = client.get("/terms")
    assert resp.status_code == 200
    assert "服务条款" in resp.text
    assert "一经售出概不退款" in resp.text


# ---------- 登录页勾选校验 ----------


def test_login_page_has_terms_checkbox(client):
    resp = client.get("/dashboard/login")
    assert resp.status_code == 200
    assert "agree_terms" in resp.text
    assert "服务条款" in resp.text
    assert '/terms' in resp.text


def test_login_without_terms_checkbox_rejected(client):
    email = _unique_email()
    _register_api(client, email)
    resp = client.post("/dashboard/login", data={"email": email, "password": _PW})
    assert resp.status_code == 200
    assert "请先阅读并同意《服务条款》" in resp.text


def test_login_with_terms_checkbox_success(client):
    email = _unique_email()
    _register_api(client, email)
    resp = client.post(
        "/dashboard/login",
        data={"email": email, "password": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "ai_search_session" in resp.cookies


# ---------- 注册页勾选校验 ----------


def test_register_page_has_terms_checkbox(client):
    resp = client.get("/dashboard/register")
    assert resp.status_code == 200
    assert "agree_terms" in resp.text
    assert "服务条款" in resp.text
    assert '/terms' in resp.text


def test_register_without_terms_checkbox_rejected(client):
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW},
    )
    assert resp.status_code == 200
    assert "请先阅读并同意《服务条款》" in resp.text


def test_register_with_terms_checkbox_success(client):
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={
            "email": email,
            "password": _PW,
            "password_confirm": _PW,
            "agree_terms": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert "ai_search_session" in resp.cookies
    # 携带 session cookie 可访问控制台
    resp = client.get("/dashboard")
    assert resp.status_code == 200, resp.text


# ---------- 页脚链接 ----------


def test_landing_footer_has_terms_link(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/terms" in resp.text


# ---------- 前端自定义校验（app.js setCustomValidity） ----------


def test_app_js_has_terms_custom_validity(client):
    """app.js 对 #agree_terms 做 setCustomValidity，提示文案须体现"同意《服务条款》"。

    TestClient 不执行 JS，故直接检查 /static/app.js 静态资源内容。
    """
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert "agree_terms" in resp.text
    assert "setCustomValidity" in resp.text
    assert "同意《服务条款》" in resp.text


def test_login_register_pages_load_app_js(client):
    """登录页/注册页均加载 app.js，#agree_terms 自定义校验会自动生效。"""
    for path in ("/dashboard/login", "/dashboard/register"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert '<script src="/static/app.js"></script>' in resp.text
