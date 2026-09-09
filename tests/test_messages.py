"""站内信测试 —— 管理端定向/全局发送、用户端列表/已读、越权校验。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB/Redis，不直连。

管理员身份：站内信落库有 sender_id 外键，admin_override 注入的内存 admin 不落库
会违反外键。故用「真实注册 + admin_override 提权 PATCH /admin/users/{id}」得到
真实 admin 账号，再用其 JWT 走管理端路由。
"""

import uuid

import pytest

from tests.test_admin_unlimited import admin_override

_PW = "test-pass-1234"


def _unique_email() -> str:
    return f"msg-{uuid.uuid4().hex[:8]}@example.com"


def _register(client, email: str) -> str:
    """注册并返回 JWT。"""
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _headers(jwt: str) -> dict:
    return {"Authorization": f"Bearer {jwt}"}


def _make_admin(client) -> str:
    """注册真实用户并提权为 admin，返回其 JWT（role 已落库）。"""
    jwt = _register(client, _unique_email())
    resp = client.get("/auth/me", headers=_headers(jwt))
    assert resp.status_code == 200, resp.text
    user_id = resp.json()["id"]
    with admin_override(client) as ac:
        resp = ac.patch(f"/admin/users/{user_id}", json={"role": "admin"})
        assert resp.status_code == 200, resp.text
    # 校验提权生效
    resp = client.get("/admin/users", headers=_headers(jwt))
    assert resp.status_code == 200, resp.text
    return jwt


def _send_direct(client, admin_jwt: str, email: str, title: str = "定向标题", content: str = "定向内容"):
    return client.post(
        "/admin/messages",
        data={"target_type": "user", "target_email": email, "title": title, "content": content},
        headers=_headers(admin_jwt),
        follow_redirects=False,
    )


# ---------- 管理端发送 + 用户端读取闭环 ----------


def test_admin_send_to_user_then_user_reads(client):
    """管理员定向发送 → 用户列表可见 → 标记已读 → 未读数变化。"""
    admin_jwt = _make_admin(client)
    email = _unique_email()
    user_jwt = _register(client, email)

    # 发送前未读 0
    resp = client.get("/messages/unread-count", headers=_headers(user_jwt))
    assert resp.status_code == 200
    assert resp.json()["unread"] == 0

    # 管理员定向发送
    resp = _send_direct(client, admin_jwt, email)
    assert resp.status_code == 303, resp.text
    assert "sent=1" in resp.headers["location"]

    # 用户列表可见
    resp = client.get("/messages", headers=_headers(user_jwt))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["unread_count"] >= 1
    item = next(i for i in body["items"] if i["title"] == "定向标题")
    assert item["read"] is False
    assert item["kind"] == "user"

    # 未读数
    resp = client.get("/messages/unread-count", headers=_headers(user_jwt))
    assert resp.json()["unread"] >= 1

    # 标记已读
    resp = client.post(f"/messages/{item['id']}/read", headers=_headers(user_jwt))
    assert resp.status_code == 200
    assert resp.json()["msg"] == "已读"

    # 已读后未读数下降、状态翻转（重复标记幂等）
    resp = client.post(f"/messages/{item['id']}/read", headers=_headers(user_jwt))
    assert resp.status_code == 200
    resp = client.get("/messages", headers=_headers(user_jwt))
    body = resp.json()
    item2 = next(i for i in body["items"] if i["id"] == item["id"])
    assert item2["read"] is True
    assert item2["read_at"] is not None


def test_broadcast_reaches_multiple_users(client):
    """全局广播 → 多个用户均可见，kind=broadcast。"""
    admin_jwt = _make_admin(client)
    email_a, email_b = _unique_email(), _unique_email()
    jwt_a = _register(client, email_a)
    jwt_b = _register(client, email_b)

    marker = f"广播标题-{uuid.uuid4().hex[:6]}"
    resp = client.post(
        "/admin/messages",
        data={"target_type": "broadcast", "target_email": "", "title": marker, "content": "全局通知内容"},
        headers=_headers(admin_jwt),
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    # sent 数 ≥ 3（admin 自己 + 两个用户）
    sent = int(resp.headers["location"].split("sent=")[1])
    assert sent >= 3

    for jwt in (jwt_a, jwt_b):
        resp = client.get("/messages", headers=_headers(jwt))
        assert resp.status_code == 200
        items = resp.json()["items"]
        hit = [i for i in items if i["title"] == marker]
        assert len(hit) == 1
        assert hit[0]["kind"] == "broadcast"
        assert hit[0]["read"] is False


def test_send_to_unknown_email_fails(client):
    """定向发送到未注册邮箱：重渲染表单页并提示（不裸 4xx）。"""
    admin_jwt = _make_admin(client)
    resp = _send_direct(client, admin_jwt, f"ghost-{uuid.uuid4().hex[:6]}@example.com")
    assert resp.status_code == 200
    assert "未注册" in resp.text


# ---------- 管理端页面 ----------


def test_admin_messages_page_and_stats(client):
    """admin 访问 /admin/messages 应 200，含表单与已读统计；发送后列表含批次。"""
    admin_jwt = _make_admin(client)
    email = _unique_email()
    user_jwt = _register(client, email)

    resp = client.get("/admin/messages", headers=_headers(admin_jwt))
    assert resp.status_code == 200, resp.text
    assert "发送站内信" in resp.text
    assert "已发消息" in resp.text

    # 发一条定向 → 管理页出现该批次（已读 0/1）；用户读后为 1/1
    marker = f"统计标题-{uuid.uuid4().hex[:6]}"
    resp = _send_direct(client, admin_jwt, email, title=marker)
    assert resp.status_code == 303

    resp = client.get("/admin/messages", headers=_headers(admin_jwt))
    assert marker in resp.text
    assert "0/1" in resp.text

    resp = client.get("/messages", headers=_headers(user_jwt))
    mid = next(i for i in resp.json()["items"] if i["title"] == marker)["id"]
    client.post(f"/messages/{mid}/read", headers=_headers(user_jwt))

    resp = client.get("/admin/messages", headers=_headers(admin_jwt))
    assert "1/1" in resp.text


def test_admin_messages_prefill_from_ticket(client):
    """?ticket= 预填收件人邮箱与工单回复文案（工单场景入口）。"""
    admin_jwt = _make_admin(client)
    email = _unique_email()
    user_jwt = _register(client, email)

    resp = client.post(
        "/feedback",
        json={"category": "bug", "subject": "页面打不开", "content": "详细内容"},
        headers=_headers(user_jwt),
    )
    assert resp.status_code == 201, resp.text
    ticket_id = resp.json()["id"]

    resp = client.get(f"/admin/messages?ticket={ticket_id}", headers=_headers(admin_jwt))
    assert resp.status_code == 200
    assert email in resp.text
    assert "回复：工单「页面打不开」" in resp.text


def test_admin_feedback_html_has_notify_entry(client):
    """浏览器（Accept: text/html）访问 /admin/feedback 渲染管理页，含「通知该用户」入口。"""
    admin_jwt = _make_admin(client)
    email = _unique_email()
    user_jwt = _register(client, email)
    resp = client.post(
        "/feedback",
        json={"category": "other", "subject": "工单入口测试", "content": "内容"},
        headers=_headers(user_jwt),
    )
    assert resp.status_code == 201, resp.text

    resp = client.get(
        "/admin/feedback",
        headers={**_headers(admin_jwt), "Accept": "text/html"},
    )
    assert resp.status_code == 200
    assert "工单管理" in resp.text
    assert "通知该用户" in resp.text
    assert f"/admin/messages?to={email}" in resp.text

    # 程序调用（默认 Accept: */*）仍返回 JSON
    resp = client.get("/admin/feedback", headers=_headers(admin_jwt))
    assert resp.status_code == 200
    assert "items" in resp.json()


# ---------- 越权 ----------


def test_non_admin_cannot_access_admin_messages(client):
    """普通用户访问/发送站内信管理端应 403。"""
    jwt = _register(client, _unique_email())
    resp = client.get("/admin/messages", headers=_headers(jwt))
    assert resp.status_code == 403
    resp = client.post(
        "/admin/messages",
        data={"target_type": "broadcast", "target_email": "", "title": "t", "content": "c"},
        headers=_headers(jwt),
    )
    assert resp.status_code == 403


def test_cannot_read_others_message(client):
    """越权标记他人消息已读应 404（不暴露存在性）。"""
    admin_jwt = _make_admin(client)
    email_a = _unique_email()
    jwt_a = _register(client, email_a)
    jwt_b = _register(client, _unique_email())

    resp = _send_direct(client, admin_jwt, email_a, title="A的私信")
    assert resp.status_code == 303

    resp = client.get("/messages", headers=_headers(jwt_a))
    mid = next(i for i in resp.json()["items"] if i["title"] == "A的私信")["id"]

    # B 标记 A 的消息 → 404
    resp = client.post(f"/messages/{mid}/read", headers=_headers(jwt_b))
    assert resp.status_code == 404

    # B 的列表里看不到 A 的私信
    resp = client.get("/messages", headers=_headers(jwt_b))
    titles = [i["title"] for i in resp.json()["items"]]
    assert "A的私信" not in titles

    # 不存在的消息 / 非法 ID → 404
    resp = client.post(f"/messages/{uuid.uuid4()}/read", headers=_headers(jwt_a))
    assert resp.status_code == 404
    resp = client.post("/messages/not-a-uuid/read", headers=_headers(jwt_a))
    assert resp.status_code == 404


def test_messages_api_requires_auth(client):
    """未认证访问用户侧消息 API 应 401。"""
    client.cookies.clear()
    assert client.get("/messages").status_code == 401
    assert client.get("/messages/unread-count").status_code == 401
    assert client.post(f"/messages/{uuid.uuid4()}/read").status_code == 401


# ---------- Dashboard 页面 ----------


def test_messages_page_requires_login(client):
    """未登录访问 /dashboard/messages 应 302/303 到登录页。"""
    client.cookies.clear()
    resp = client.get("/dashboard/messages", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "/dashboard/login" in resp.headers["location"]


def test_messages_page_logged_in(client):
    """登录后可访问站内信页，导航含「消息」入口。"""
    email = _unique_email()
    resp = client.post(
        "/dashboard/register",
        data={"email": email, "password": _PW, "password_confirm": _PW, "agree_terms": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ai_search_session" in resp.cookies

    resp = client.get("/dashboard/messages")
    assert resp.status_code == 200
    assert "站内信" in resp.text
    assert "我的消息" in resp.text

    # 导航入口与未读角标脚本
    resp = client.get("/dashboard")
    assert "/dashboard/messages" in resp.text
    assert "nav-msg-badge" in resp.text
