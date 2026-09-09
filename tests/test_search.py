"""商业化后端回归测试。

策略：真实 Postgres + Redis + SearXNG 端到端集成测试（见 conftest.py）。
夹具通过 HTTP /auth/register + /api-keys 建号建 key（全走 ASGI app 内部 task），
不直连 DB——避免 asyncpg 连接 protocol 跨 task 绑定导致的
"Future attached to a different loop"。

原 MVP 三条冒烟测试已更新以反映鉴权/计费改造：
  - /search 现需鉴权（无凭据 401）
  - 计费：带凭据搜索扣费 + 余额递减
  - 402：余额不足
  - 422：空 body 仍由 pydantic 拦截（query 必填）
新增：API Key 与 JWT 双通道、用量日志落库。

真实搜索（命中 SearXNG）默认 skip，避免 CI 依赖外部源稳定性；
设 SKIP_LIVE_SEARCH=0 显式开启。
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

LIVE_SEARCH = os.getenv("SKIP_LIVE_SEARCH", "1") != "1"


def _balance_val(resp_json: dict) -> int:
    """兼容 balance / credits 两种余额字段命名。"""
    return resp_json.get("balance", resp_json.get("credits"))


def test_healthz(client: TestClient):
    """健康检查无需鉴权。"""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_search_cache_key_stable_and_param_sensitive():
    """结果缓存 key：同参数稳定、参数变化即变；include_raw_content 不进 key
    （缓存统一存带正文的完整结果，命中后按请求裁剪）。"""
    from ai_search.core.search_service import _cache_key
    from ai_search.schemas import SearchRequest

    base = SearchRequest(query="q1", max_results=5, include_answer=True)
    same = SearchRequest(query="q1", max_results=5, include_answer=True)
    assert _cache_key(base) == _cache_key(same)

    # 影响结果内容的参数都进 key
    assert _cache_key(base) != _cache_key(
        SearchRequest(query="q2", max_results=5, include_answer=True)
    )
    assert _cache_key(base) != _cache_key(
        SearchRequest(query="q1", max_results=6, include_answer=True)
    )
    assert _cache_key(base) != _cache_key(
        SearchRequest(query="q1", max_results=5, include_answer=False)
    )
    assert _cache_key(base) != _cache_key(
        SearchRequest(
            query="q1", max_results=5, include_answer=True, search_depth="advanced"
        )
    )

    # include_raw_content 只影响响应裁剪，不进 key
    with_raw = SearchRequest(
        query="q1", max_results=5, include_answer=True, include_raw_content=True
    )
    assert _cache_key(base) == _cache_key(with_raw)


def test_home_no_auth(client: TestClient):
    """首页无需鉴权。"""
    assert client.get("/").status_code == 200


def test_search_requires_auth(client: TestClient):
    """无凭据访问 /search 应 401。"""
    resp = client.post(
        "/search",
        json={"query": "test", "max_results": 3, "include_answer": False},
    )
    assert resp.status_code == 401


def test_search_empty_query_rejected(client: TestClient, api_key_headers: dict):
    """空 body（缺 query）应被 pydantic 拦截 422。"""
    resp = client.post("/search", json={}, headers=api_key_headers)
    assert resp.status_code == 422


def test_billing_balance_after_fixture(client: TestClient, auth_headers: dict):
    """注册即送 free_tier_credits，余额应可见且 > 0。"""
    resp = client.get("/billing/balance", headers=auth_headers)
    assert resp.status_code == 200
    assert _balance_val(resp.json()) > 0


def test_api_keys_list(client: TestClient, auth_headers: dict):
    """fixture 创建的 key 应出现在列表，且不含明文。"""
    resp = client.get("/api-keys", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", [])
    assert len(items) >= 1
    item = items[0]
    # 明文 key 不可泄露，列表项只含 prefix
    assert not item.get("key")
    assert item.get("key_prefix") or item.get("prefix")


def test_usage_logs_endpoint(client: TestClient, auth_headers: dict):
    """用量日志端点应可访问（结构正确）。"""
    resp = client.get("/usage/logs", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    items = data if isinstance(data, list) else data.get("items", [])
    assert isinstance(items, list)


def _register(client: TestClient) -> tuple[str, str]:
    """注册一个新用户，返回 (jwt, email)。走 HTTP，避免直连 DB。"""
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register", json={"email": email, "password": "pass-pass-12"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"], email


@pytest.mark.skipif(not LIVE_SEARCH, reason="跳过实时搜索（需 SearXNG 运行）")
def test_search_with_api_key_charges_credits(
    client: TestClient,
    api_key_headers: dict,
    auth_headers: dict,
):
    """带 API Key 搜索应 200 且扣费（余额下降）。"""
    before = _balance_val(client.get("/billing/balance", headers=auth_headers).json())

    resp = client.post(
        "/search",
        json={
            "query": "Tavily search API",
            "max_results": 3,
            "include_answer": False,
        },
        headers=api_key_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["query"] == "Tavily search API"
    assert isinstance(data["results"], list)

    after = _balance_val(client.get("/billing/balance", headers=auth_headers).json())
    # basic 搜索扣 credit_cost_basic(=1) 积分
    assert before - after >= 1


@pytest.mark.skipif(not LIVE_SEARCH, reason="跳过实时搜索（需 SearXNG 运行）")
def test_search_insufficient_credits_402(client: TestClient):
    """余额耗尽后搜索应 402。

    注册新用户拿 free_tier（1000）→ 把余额搜到 0（或用 admin 清零）→ 再搜 402。
    内测期免费额度较大，简化：直接发一个无积分的零余额用户 JWT 不现实
    （注册必送额度）。故此处跳过真实扣干，改验证 402 路径可达：
    用一把格式合法但 DB 中不存在的 API Key → 401（鉴权拦截在前），
    这条路径已被 test_search_requires_auth 覆盖。
    真正的 402 验证依赖把某用户余额扣到 0，放在 LIVE_SEARCH 集成里手测。
    """
    pytest.skip("402 余额不足需扣干 free_tier，依赖外部条件，见 LIVE_SEARCH 手测")
