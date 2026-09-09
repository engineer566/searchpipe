"""支付渠道测试 —— 充值（固定档/自定义）+ 订阅（订阅/续订/升级/拒绝降级）+ 回调幂等。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB，不直连。支付提供方用 FakeProvider 替换（monkeypatch 模块
单例 _provider），不下真单；回调用 sign=ok 模拟验签通过。

种子套餐（迁移 b71f2c3d9e50 固定 UUID）：
- 充值：…0001=¥10/350 …0002=¥20/700 …0003=¥50/1800 …0004=¥100/5000
- 订阅：…0001=档1 ¥9.99/1000 …0002=档2 ¥24.99/3000 …0003=档3 ¥49.99/10000
"""

import uuid

import pytest

_PW = "test-pass-1234"

PLAN_R10 = "11111111-0000-0000-0000-000000000001"  # ¥10 = 350
PLAN_SUB1 = "22222222-0000-0000-0000-000000000001"  # 档1 ¥9.99 = 1000
PLAN_SUB2 = "22222222-0000-0000-0000-000000000002"  # 档2 ¥24.99 = 3000


class FakeProvider:
    """假支付提供方：记录订单号并返回假支付链接；sign=ok 视为验签通过。"""

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "fakepay"

    async def create_order(
        self, order_no: str, amount_cents: int, subject: str, pay_channel: str = "alipay"
    ) -> str:
        self.orders[order_no] = {"amount_cents": amount_cents, "channel": pay_channel}
        return f"https://pay.example.com/{order_no}?channel={pay_channel}"

    def verify_callback(self, params: dict) -> bool:
        return params.get("sign") == "ok"


@pytest.fixture()
def fake_provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr("ai_search.payments.service._provider", p)
    return p


def _register(client) -> dict:
    email = f"pay-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_order(client, headers: dict, **body) -> dict:
    resp = client.post("/payments/orders", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pay(client, fake: FakeProvider, order: dict) -> None:
    """模拟支付成功回调（从 pay_url 解析订单号）。"""
    order_no = order["pay_url"].rsplit("/", 1)[-1].split("?")[0]
    assert order_no in fake.orders
    resp = client.post(
        "/payments/callback", data={"trade_order_id": order_no, "sign": "ok"}
    )
    assert resp.text == '"success"' or resp.text == "success", resp.text


def _balance(client, headers: dict) -> dict:
    resp = client.get("/billing/balance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 目录 ----------


def test_catalog_public(client):
    resp = client.get("/payments/catalog")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["recharge_plans"]) == 4
    assert len(data["subscription_plans"]) == 3
    levels = [p["level"] for p in data["subscription_plans"]]
    assert levels == [1, 2, 3]
    # 限时折扣：现价低于标价
    for p in data["subscription_plans"]:
        assert p["price_yuan"] < p["original_price_yuan"]
    assert data["credit_yuan_rate"] == "0.03"
    assert data["max_recharge_yuan"] == 100
    assert set(data["pay_channels"]) == {"alipay", "wechat"}
    assert data["subscription"] is None  # 未登录


# ---------- 充值 ----------


def test_recharge_fixed_plan(client, fake_provider):
    headers = _register(client)
    order = _create_order(
        client, headers, kind="recharge", plan_id=PLAN_R10, pay_channel="alipay"
    )
    assert order["credits"] == "350.00"
    assert order["amount_cents"] == 1000
    _pay(client, fake_provider, order)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(1350.0)  # 免费 1000 + 充值 350
    assert bal["permanent"] == pytest.approx(1350.0)
    assert bal["expiring"] == pytest.approx(0.0)

    # 订单状态
    resp = client.get(f"/payments/orders/{order['order_id']}", headers=headers)
    assert resp.json()["status"] == "paid"

    # 回调幂等：重复回调不重复发积分
    _pay(client, fake_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(1350.0)


def test_recharge_custom_amount(client, fake_provider):
    headers = _register(client)
    order = _create_order(client, headers, kind="recharge", amount_cents=500)
    assert order["credits"] == "166.67"  # ¥5 ÷ 0.03
    _pay(client, fake_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(1166.67)


def test_recharge_custom_validation(client, fake_provider):
    headers = _register(client)
    # 未给金额
    assert client.post(
        "/payments/orders", json={"kind": "recharge"}, headers=headers
    ).status_code == 400
    # 0 / 负数
    assert client.post(
        "/payments/orders", json={"kind": "recharge", "amount_cents": 0}, headers=headers
    ).status_code == 400
    # 超过上限 ¥100
    resp = client.post(
        "/payments/orders", json={"kind": "recharge", "amount_cents": 10001}, headers=headers
    )
    assert resp.status_code == 400
    assert "100" in resp.json()["detail"]
    # 不存在的套餐
    assert client.post(
        "/payments/orders",
        json={"kind": "recharge", "plan_id": str(uuid.uuid4())},
        headers=headers,
    ).status_code == 400
    # 非法渠道
    assert client.post(
        "/payments/orders",
        json={"kind": "recharge", "amount_cents": 100, "pay_channel": "foo"},
        headers=headers,
    ).status_code == 400


def test_pay_channel_wechat(client, fake_provider):
    headers = _register(client)
    order = _create_order(
        client, headers, kind="recharge", plan_id=PLAN_R10, pay_channel="wechat"
    )
    assert "channel=wechat" in order["pay_url"]


# ---------- 订阅 ----------


def test_subscribe_flow(client, fake_provider):
    headers = _register(client)
    order = _create_order(
        client, headers, kind="subscribe", plan_id=PLAN_SUB1, pay_channel="alipay"
    )
    assert order["amount_cents"] == 999  # 限时折扣价
    assert order["credits"] == "1000.00"
    _pay(client, fake_provider, order)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(2000.0)   # 1000 永久 + 1000 限时
    assert bal["permanent"] == pytest.approx(1000.0)
    assert bal["expiring"] == pytest.approx(1000.0)
    assert bal["next_expiry"] is not None

    # catalog 显示当前订阅（登录态）
    resp = client.get("/payments/catalog", headers=headers)
    sub = resp.json()["subscription"]
    assert sub is not None and sub["level"] == 1

    # 已有订阅不能再订阅
    resp = client.post(
        "/payments/orders",
        json={"kind": "subscribe", "plan_id": PLAN_SUB2},
        headers=headers,
    )
    assert resp.status_code == 400


def test_renew_flow(client, fake_provider):
    headers = _register(client)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    _pay(client, fake_provider, order)
    sub1 = client.get("/payments/catalog", headers=headers).json()["subscription"]

    # 续订同档位
    renew = _create_order(client, headers, kind="renew", plan_id=PLAN_SUB1)
    assert renew["amount_cents"] == 999
    _pay(client, fake_provider, renew)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(3000.0)
    # 续订积分下一周期才生效：expiring 不变，upcoming +1000
    assert bal["expiring"] == pytest.approx(1000.0)
    assert bal["upcoming"] == pytest.approx(1000.0)

    # 周期顺延 30 天
    from datetime import datetime

    sub2 = client.get("/payments/catalog", headers=headers).json()["subscription"]
    end1 = datetime.fromisoformat(sub1["period_end"])
    end2 = datetime.fromisoformat(sub2["period_end"])
    assert (end2 - end1).days == 30

    # 续订其他档位被拒
    resp = client.post(
        "/payments/orders", json={"kind": "renew", "plan_id": PLAN_SUB2}, headers=headers
    )
    assert resp.status_code == 400


def test_upgrade_flow(client, fake_provider):
    headers = _register(client)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    _pay(client, fake_provider, order)

    # 升级到档2：付新档当前售价全额
    up = _create_order(client, headers, kind="upgrade", plan_id=PLAN_SUB2)
    assert up["amount_cents"] == 2499
    assert up["credits"] == "3000.00"
    _pay(client, fake_provider, up)

    bal = _balance(client, headers)
    # 原档 1000 未用完积分延期至与新档（3000）同期 → 限时余额 4000
    assert bal["expiring"] == pytest.approx(4000.0)
    sub = client.get("/payments/catalog", headers=headers).json()["subscription"]
    assert sub["level"] == 2


def test_subscription_rules_rejected(client, fake_provider):
    headers = _register(client)
    # 无订阅时续订/升级被拒
    assert client.post(
        "/payments/orders", json={"kind": "renew", "plan_id": PLAN_SUB1}, headers=headers
    ).status_code == 400
    assert client.post(
        "/payments/orders", json={"kind": "upgrade", "plan_id": PLAN_SUB1}, headers=headers
    ).status_code == 400

    # 订阅档2 后：降级（upgrade 到档1）被拒
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB2)
    _pay(client, fake_provider, order)
    resp = client.post(
        "/payments/orders", json={"kind": "upgrade", "plan_id": PLAN_SUB1}, headers=headers
    )
    assert resp.status_code == 400
    assert "降级" in resp.json()["detail"]
