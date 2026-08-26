"""管理员无限额度测试 —— admin/owner 跳过扣费与限流。

三条路径分别验证：
  - /billing/balance 对 admin 返回 unlimited=true（普通用户 unlimited=false）
  - 限流：把 rate_limit_rpm 调到 1，普通用户连发触发 429，admin 不触发
  - 扣费：单元级验证 charge_credits 对 admin 返回 cost=0、不调 deduct_credits

admin 角色注入：用 app.dependency_overrides 覆盖鉴权依赖，返回一个 admin User，
**完全不碰 DB**（admin 扣费路径本就不查库），天然规避 conftest.py 文档的 asyncpg
跨 loop 约束——无需 portal_factory、无需直连 session。

注意：dependency_overrides 是 app 全局的，admin 与普通用户断言不能同时活跃。
故用 admin_override 上下文管理器精确控制覆盖作用域：with 块内是 admin，块外是
真实注册的普通用户。
"""

import asyncio
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from ai_search.auth.core import AuthContext
from ai_search.auth.dependencies import get_current_user, get_current_user_or_api_key
from ai_search.db.models import User, UserRole


def _make_admin_user() -> User:
    """构造一个内存中的 admin User（不落库）。admin 扣费路径不查 DB，故无需持久化。"""
    u = User(email="admin@example.com", role=UserRole.ADMIN.value, status="active")
    u.id = uuid.uuid4()
    return u


@contextmanager
def admin_override(client: TestClient):
    """临时把鉴权依赖覆盖为返回 admin 上下文。with 块退出即还原。"""
    admin = _make_admin_user()
    ctx = AuthContext(user=admin)

    async def _override_search() -> AuthContext:
        return ctx

    async def _override_billing() -> User:
        return admin

    overrides = client.app.dependency_overrides
    overrides[get_current_user_or_api_key] = _override_search
    overrides[get_current_user] = _override_billing
    try:
        yield client
    finally:
        overrides.pop(get_current_user_or_api_key, None)
        overrides.pop(get_current_user, None)


def test_balance_unlimited_for_admin(
    client: TestClient, auth_headers: dict
):
    """普通用户 unlimited=False；admin unlimited=True。"""
    # 普通用户（真实注册号）
    resp = client.get("/billing/balance", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["unlimited"] is False

    # admin（依赖覆盖注入）
    with admin_override(client) as ac:
        resp = ac.get("/billing/balance")
    assert resp.status_code == 200
    assert resp.json()["unlimited"] is True


def test_admin_exempt_from_rate_limit(
    client: TestClient, auth_headers: dict, monkeypatch
):
    """rate_limit_rpm=1 时：普通用户第 2 次连发 429；admin 连发不触发 429。

    限流在扣费前，429 发生在 run_search 之前，不依赖 SearXNG。admin 即使搜索
    因无 SearXNG 返回 502 也不应 429。
    """
    from ai_search.config import get_settings

    # 调低限流：每分钟 1 次，burst 0
    # 滑动窗口逻辑 count > limit+burst：zadd 在 zcard 后，故第 1、2 次均放行，第 3 次触发 429。
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_rpm", 1)
    monkeypatch.setattr(settings, "rate_limit_burst", 0)

    payload = {"query": "test", "max_results": 1, "include_answer": False}

    # 普通用户：连发两次均非 429（放行），第三次应 429
    r = client.post("/search", json=payload, headers=auth_headers)
    assert r.status_code != 429
    r = client.post("/search", json=payload, headers=auth_headers)
    assert r.status_code != 429
    r = client.post("/search", json=payload, headers=auth_headers)
    assert r.status_code == 429, r.text

    # admin：连发多次都不应 429（无 SearXNG 可能 502，但绝不 429）
    with admin_override(client) as ac:
        for _ in range(5):
            r = ac.post("/search", json=payload)
            assert r.status_code != 429, "admin 不应被限流"


def test_charge_credits_admin_short_circuits(monkeypatch):
    """单元级：charge_credits 对 admin 返回 cost=0 且不调 deduct_credits。"""
    from ai_search.billing.pipeline import charge_credits

    async def _boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("admin 不应触发 deduct_credits")

    monkeypatch.setattr("ai_search.billing.pipeline.deduct_credits", _boom)

    admin = _make_admin_user()
    ctx = AuthContext(user=admin)

    async def _run() -> None:
        # 用 None 作 db（admin 路径不碰 DB）
        result = await charge_credits(None, ctx, "advanced")  # type: ignore[arg-type]
        assert result.cost == 0
        assert result.balance_after == 0

    asyncio.run(_run())
