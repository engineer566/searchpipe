"""反馈工单测试 —— 用户侧提交/列表、管理侧列表/关闭、越权校验。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB/Redis，不直连。
"""

import time
import uuid

import pytest

_PW = "test-pass-1234"


def _unique_email() -> str:
    return f"fb-{uuid.uuid4().hex[:8]}@example.com"


def _register(client, email: str) -> dict:
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_ticket(client, jwt: str, subject: str = "测试主题", content: str = "测试内容", category: str = "bug") -> dict:
    resp = client.post(
        "/feedback",
        json={"category": category, "subject": subject, "content": content},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- 用户侧：提交与列表 ----------


def test_create_feedback_success(client):
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]

    resp = client.post(
        "/feedback",
        json={"category": "feature", "subject": "想要深色模式", "content": "建议增加深色模式支持"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["msg"] == "工单已提交"
    assert "id" in body


def test_create_feedback_validation(client):
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    # 空 subject
    resp = client.post("/feedback", json={"subject": "", "content": "内容"}, headers=headers)
    assert resp.status_code == 422

    # 空 content
    resp = client.post("/feedback", json={"subject": "主题", "content": ""}, headers=headers)
    assert resp.status_code == 422

    # 非法 category
    resp = client.post("/feedback", json={"category": "hacker", "subject": "主题", "content": "内容"}, headers=headers)
    assert resp.status_code == 422

    # subject 超长
    resp = client.post("/feedback", json={"subject": "x" * 256, "content": "内容"}, headers=headers)
    assert resp.status_code == 422


def test_create_feedback_rate_limit(client):
    """30 秒内重复提交应 429。"""
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    # 第一次成功
    resp = client.post("/feedback", json={"subject": "主题1", "content": "内容1"}, headers=headers)
    assert resp.status_code == 201

    # 第二次 429
    resp = client.post("/feedback", json={"subject": "主题2", "content": "内容2"}, headers=headers)
    assert resp.status_code == 429
    assert "秒后再试" in resp.json()["detail"]


def test_list_my_feedback(client):
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    # 先建工单（频率限制 30 秒，只能建一个）
    _create_ticket(client, jwt, "工单A", "内容A", "bug")

    resp = client.get("/feedback", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    subjects = [item["subject"] for item in body["items"]]
    assert "工单A" in subjects


def test_feedback_list_only_mine(client):
    """用户 A 的工单不应出现在用户 B 的列表中。"""
    email_a = _unique_email()
    tokens_a = _register(client, email_a)
    jwt_a = tokens_a["access_token"]

    email_b = _unique_email()
    tokens_b = _register(client, email_b)
    jwt_b = tokens_b["access_token"]

    _create_ticket(client, jwt_a, "A的工单", "内容", "bug")

    resp = client.get("/feedback", headers={"Authorization": f"Bearer {jwt_b}"})
    assert resp.status_code == 200
    body = resp.json()
    subjects = [item["subject"] for item in body["items"]]
    assert "A的工单" not in subjects


# ---------- 管理侧 ----------


def test_admin_list_feedback_and_close(client):
    """admin 可列全部工单并关闭。"""
    from tests.test_admin_unlimited import admin_override

    # 普通用户提交工单
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]
    ticket = _create_ticket(client, jwt, "需要关闭", "内容", "billing")
    ticket_id = ticket["id"]

    # admin 列表
    with admin_override(client) as ac:
        resp = ac.get("/admin/feedback")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        ids = [item["id"] for item in body["items"]]
        assert ticket_id in ids

    # admin 关闭
    with admin_override(client) as ac:
        resp = ac.post(f"/admin/feedback/{ticket_id}/close")
        assert resp.status_code == 200
        assert resp.json()["msg"] == "工单已关闭"

    # 关闭后再关 400
    with admin_override(client) as ac:
        resp = ac.post(f"/admin/feedback/{ticket_id}/close")
        assert resp.status_code == 400
        assert "已关闭" in resp.json()["detail"]

    # admin 按 status 过滤
    with admin_override(client) as ac:
        resp = ac.get("/admin/feedback?status=closed")
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["id"] for item in body["items"]]
        assert ticket_id in ids

        resp = ac.get("/admin/feedback?status=open")
        assert resp.status_code == 200
        body = resp.json()
        ids = [item["id"] for item in body["items"]]
        assert ticket_id not in ids


def test_admin_close_nonexistent_404(client):
    from tests.test_admin_unlimited import admin_override

    with admin_override(client) as ac:
        resp = ac.post(f"/admin/feedback/{uuid.uuid4()}/close")
        assert resp.status_code == 404


def test_admin_feedback_filter_invalid_status(client):
    from tests.test_admin_unlimited import admin_override

    with admin_override(client) as ac:
        resp = ac.get("/admin/feedback?status=invalid")
        assert resp.status_code == 400


# ---------- 越权 ----------


def test_non_admin_cannot_access_admin_feedback(client):
    """普通用户访问 /admin/feedback 应 403。"""
    email = _unique_email()
    tokens = _register(client, email)
    jwt = tokens["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    resp = client.get("/admin/feedback", headers=headers)
    assert resp.status_code == 403

    resp = client.post(f"/admin/feedback/{uuid.uuid4()}/close", headers=headers)
    assert resp.status_code == 403


# ---------- Dashboard 页面 ----------


def test_feedback_page_requires_login(client):
    """未登录访问 /dashboard/feedback 应 302/303 到登录页。"""
    client.cookies.clear()  # 清除之前测试留下的 cookie
    resp = client.get("/dashboard/feedback", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/dashboard/login" in resp.headers["location"]


def test_feedback_page_logged_in(client):
    """登录后可访问反馈页。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ai_search_session" in resp.cookies

    resp = client.get("/dashboard/feedback")
    assert resp.status_code == 200
    assert "反馈与工单" in resp.text
    assert "提交工单" in resp.text
    assert "我的工单" in resp.text
