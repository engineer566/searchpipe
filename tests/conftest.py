"""测试公共夹具。

策略：用真实 Postgres + Redis（docker compose 已起）做端到端集成测试，
而非内存 mock——商业化后端的鉴权/计费/限流/日志链路强依赖真实 DB/Redis 行为
（行锁、ZSET 滑动窗口、JWT 解析），mock 会掩盖真实缺陷。

关键约束：所有 DB 访问必须经 TestClient → ASGI app 内部的请求 task 完成，
绝不让夹具自己开 session 直接操作 DB。原因：Starlette 的 BaseHTTPMiddleware
（限流/用量日志）用 anyio task group 在 call_next 里跑端点，而 asyncpg 的
连接 protocol 与创建它的 task/loop 绑定。夹具用 async_session_factory() 直连
会把连接绑到夹具 task 上，TestClient 第一个请求复用该连接时 ping 就会报
"Future attached to a different loop"。改为通过 HTTP（/auth/register）建号、
/api-keys 建 key，全部走 app 内部 task，连接池干净。

**跨测试文件 loop 隔离**：test_mcp_server.py 用 httpx.ASGITransport 跑在 pytest
session loop 上，会绑定 db.engine + Redis cache 到该 loop；本文件用同步 TestClient，
其 ASGI app 跑在 anyio BlockingPortal 的独立线程 loop 上。若 mcp 先跑留下绑定
session loop 的连接池，TestClient 首请求（pool_pre_ping）就跨 loop 报错。
故 client 夹具创建时先 dispose+rebind engine、清 Redis 单例，让连接池绑定
TestClient 自己的 portal loop。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from ai_search.main import app

_TEST_PASSWORD = "test-pass-1234"


def _rebind_engine_sync() -> None:
    """同步重建 db.engine + 清 Redis 单例（无 await，留给新 loop 首次使用时建池）。

    TestClient 的 portal loop 与 session loop 不同；调用方（client 夹具）已在
    TestClient 上下文内，portal loop 正在运行。但 dispose 旧 engine 的连接需要
    在它绑定的 loop 上 await——这里用 portal 的 call_soon 之外的方式：直接重建
    一个全新 engine（旧池的连接会被 GC，asyncpg protocol 析构时自行关闭），
    避免跨 loop await dispose。Redis 单例置 None 触发懒重连。
    """
    from ai_search.db import base as db_base
    from ai_search.utils import cache as cache_mod

    db_base.engine = db_base._build_engine()
    db_base.async_session_factory.configure(bind=db_base.engine)
    cache_mod._cache = None


@pytest.fixture(scope="session")
def client() -> TestClient:
    """FastAPI TestClient（ASGI 传输，不占端口）。session 级共用。

    创建前 rebind engine，确保连接池绑定 TestClient 的 portal loop 而非
    先前 test_mcp_server.py 留下的 session loop（防跨 loop 报错）。
    """
    _rebind_engine_sync()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def test_user_creds(client: TestClient) -> dict:
    """通过 HTTP /auth/register 创建测试用户（注册即送 free_tier_credits）。

    全部走 ASGI app 内部 task，避免夹具直连 DB 导致 asyncpg 跨 task 绑定。
    再通过 /api-keys 创建一把 API Key。返回 dict(jwt, api_key)。
    """
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    # 注册 → 拿 JWT（注册即送免费额度 free_tier_credits）
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": _TEST_PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    jwt = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {jwt}"}

    # 管理员补一笔大额充值，保证整轮测试余额充足（free_tier 仅 1000，搜索测试会扣）
    # —— 走 admin grant 需 admin 账号；这里改用直接调 billing 的充值 plan + mock 回调
    # 太重。简化：测试用例搜索次数有限，free_tier 1000 积分够用，不额外充值。
    # 若后续用例增多，再在此补充值逻辑。

    # 创建 API Key
    resp = client.post("/api-keys", json={"name": "pytest"}, headers=headers)
    assert resp.status_code in (200, 201), resp.text
    api_key = resp.json()["key"]

    return {"jwt": jwt, "api_key": api_key}


@pytest.fixture()
def auth_headers(test_user_creds) -> dict:
    """带 JWT 的请求头。"""
    return {"Authorization": f"Bearer {test_user_creds['jwt']}"}


@pytest.fixture()
def api_key_headers(test_user_creds) -> dict:
    """带 API Key 的请求头。"""
    return {"Authorization": f"Bearer {test_user_creds['api_key']}"}


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """固定测试文件执行顺序：httpx.ASGITransport（pytest session loop）文件先跑，
    TestClient（线程 portal loop）文件后跑。

    原因：test_mcp_server.py 在 pytest session loop 上跑 httpx+fastmcp；
    test_search.py / test_admin_unlimited.py 用同步 TestClient（anyio portal 独立线程 loop）。
    两者共用 db/base.py 模块级 engine 单例与 Redis cache 单例——asyncpg / redis.asyncio
    连接 protocol 绑定首次使用它的 loop。实测：session-loop 文件先跑、TestClient 文件
    后跑全绿（MCP teardown dispose 还原，TestClient 夹具再 rebind 适配自己 portal loop）；
    反序（TestClient 先、session-loop 后）会在 MCP setup 的 Redis close 阶段触发
    "Future attached to a different loop"。此处强制 session-loop 优先，保证全量 `pytest` 稳定。
    """
    import os.path

    def _file_key(it) -> str:
        # 归一化为 tests/<name>.py 形式（与 order 表的键一致）
        rel = os.path.relpath(str(it.module.__file__), os.getcwd())
        return rel.replace(os.sep, "/")

    order = {
        "tests/test_mcp_server.py": 0,
        "tests/test_search.py": 1,
        "tests/test_admin_unlimited.py": 2,
        "tests/test_auth.py": 3,
        "tests/test_admin_monitor.py": 4,
    }
    items.sort(key=lambda it: order.get(_file_key(it), 99))
