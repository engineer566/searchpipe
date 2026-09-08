"""管理员监控页面测试 —— /admin/monitor。

验证点：
- admin 访问返回 200 且 HTML 含关键指标区块
- 非 admin 用户（普通用户 / 未登录）被拒 403/401
- 页面含自动刷新 meta/script（60s）

全部走 TestClient，不直连 DB。
"""

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ai_search.auth.core import AuthContext
from ai_search.auth.dependencies import get_current_admin, get_current_user
from ai_search.db.models import User, UserRole


def _make_admin_user() -> User:
    """内存 admin User（不落库）。"""
    u = User(email="admin@example.com", role=UserRole.ADMIN.value, status="active")
    u.id = uuid.uuid4()
    return u


def _make_normal_user() -> User:
    """内存普通 User。"""
    u = User(email="user@example.com", role=UserRole.USER.value, status="active")
    u.id = uuid.uuid4()
    return u


@contextmanager
def admin_override(client: TestClient):
    """临时把 get_current_admin 覆盖为返回 admin。"""
    admin = _make_admin_user()

    async def _override() -> User:
        return admin

    overrides = client.app.dependency_overrides
    overrides[get_current_admin] = _override
    overrides[get_current_user] = _override
    try:
        yield client
    finally:
        overrides.pop(get_current_admin, None)
        overrides.pop(get_current_user, None)


@contextmanager
def normal_user_override(client: TestClient):
    """临时把 get_current_user 覆盖为返回普通用户（用于测 403）。"""
    user = _make_normal_user()

    async def _override() -> User:
        return user

    overrides = client.app.dependency_overrides
    overrides[get_current_user] = _override
    try:
        yield client
    finally:
        overrides.pop(get_current_user, None)


def test_monitor_page_admin_ok(client: TestClient):
    """admin 访问 /admin/monitor 应 200，且含关键指标区块。"""
    with admin_override(client) as ac:
        resp = ac.get("/admin/monitor")
    assert resp.status_code == 200, resp.text
    text = resp.text

    # 关键指标区块存在性
    assert "用户总数" in text
    assert "今日新增" in text
    assert "近 24h 活跃" in text
    assert "今日调用" in text
    assert "成功" in text
    assert "失败" in text
    assert "近 24h 每小时趋势" in text
    assert "今日消耗积分" in text
    assert "今日充值订单" in text
    assert "最近用量日志" in text
    assert "最近错误" in text

    # 自动刷新
    assert "setTimeout" in text and "60000" in text


def test_monitor_page_normal_user_forbidden(client: TestClient):
    """普通用户访问 /admin/monitor 应 403。"""
    with normal_user_override(client) as ac:
        resp = ac.get("/admin/monitor")
    assert resp.status_code == 403, resp.text


def test_monitor_page_unauthorized(client: TestClient):
    """未登录访问 /admin/monitor 应 401/403（无凭据时 get_current_user 抛 401，
    但 TestClient 无 cookie 时 get_current_admin 内部先 401，实际链路可能因
    其他依赖覆盖导致 403；这里放宽为 401/403 均可）。"""
    resp = client.get("/admin/monitor")
    assert resp.status_code in (401, 403), resp.text
