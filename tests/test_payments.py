"""支付链路测试 —— 充值（固定档/自定义）+ 订阅（订阅/续期/升级/拒绝降级）+ webhook 幂等。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB，不直连。支付提供方用 FakeProvider 替换（monkeypatch 模块
单例 _provider），不下真单；付款通过 POST /payments/webhooks/fakepay 注入
归一化事件完成（FakeProvider.verify_webhook 直接吃 JSON body）。

种子套餐（迁移 g1a2b3c4d5e6 固定 UUID）：
- 充值：…0001=$5/1000 …0002=$10/2100 …0003=$20/4400
- 订阅：…0001=Starter $4.99/1200 …0002=Pro $9.99/3000 …0003=Max $19.99/10000
"""

import json
import re
import uuid

import pytest

from ai_search.payments.provider import PaymentEvent, WebhookVerificationError

_PW = "test-pass-1234"

PLAN_R5 = "33333333-0000-0000-0000-000000000001"   # $5 = 1000
PLAN_SUB1 = "44444444-0000-0000-0000-000000000001"  # Starter $4.99 = 1200
PLAN_SUB2 = "44444444-0000-0000-0000-000000000002"  # Pro $9.99 = 3000


class FakeProvider:
    """假支付提供方：记录订单号并返回假收银台链接；webhook 直接解析 JSON body。"""

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "fakepay"

    async def create_checkout(
        self, *, order_no, kind, amount_cents, subject, plan=None,
        customer_email=None, success_url=None,
    ) -> str:
        self.orders[order_no] = {"amount_cents": amount_cents, "kind": kind}
        return f"https://pay.example.com/{order_no}"

    def verify_webhook(self, *, headers: dict, raw_body: bytes) -> PaymentEvent:
        data = json.loads(raw_body)
        if data.pop("sign", "ok") != "ok":
            raise WebhookVerificationError("bad sign")
        return PaymentEvent(
            type=data["type"],
            event_id=data["event_id"],
            order_no=data.get("order_no"),
            provider_subscription_id=data.get("provider_subscription_id"),
            provider_customer_id=data.get("provider_customer_id"),
            provider_product_id=data.get("provider_product_id"),
            amount_cents=data.get("amount_cents"),
            raw=data,
        )

    async def customer_portal_url(self, provider_customer_id: str) -> str | None:
        return f"https://portal.example.com/{provider_customer_id}"

    def available_channels(self) -> list[str]:
        return ["card", "paypal"]


@pytest.fixture()
def fake_provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr("ai_search.payments.service._provider", p)
    return p


@pytest.fixture()
def sent_mails(monkeypatch) -> list:
    """捕获邮件（注册验证链接里的 token 要靠它取）。"""
    from ai_search.utils import mailer

    out: list[dict] = []

    async def fake_send(to: str, subject: str, text: str) -> bool:
        out.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    return out


def _register(client, sent_mails) -> dict:
    """API 注册 + 完成邮箱验证 → 返回认证头（下单需已验证邮箱）。"""
    email = f"pay-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    m = re.search(r"token=([A-Za-z0-9_\-]+)", sent_mails[-1]["text"])
    assert m, f"验证邮件里没有 token：{sent_mails[-1]}"
    resp = client.get(f"/auth/verify-email?token={m.group(1)}", follow_redirects=False)
    assert resp.status_code == 302
    return headers


def _create_order(client, headers: dict, **body) -> dict:
    resp = client.post("/payments/orders", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _order_no(order: dict) -> str:
    return order["pay_url"].rsplit("/", 1)[-1].split("?")[0]


def _webhook(client, provider: str, event: dict) -> None:
    """注入一条 webhook 事件并断言处理成功。"""
    resp = client.post(f"/payments/webhooks/{provider}", content=json.dumps(event))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True}


def _pay(client, fake: FakeProvider, order: dict) -> None:
    """模拟一次性付款成功（充值档）。"""
    order_no = _order_no(order)
    assert order_no in fake.orders
    _webhook(client, "fakepay", {
        "type": "one_time_paid", "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "order_no": order_no, "amount_cents": order["amount_cents"],
    })


def _activate_subscription(client, order: dict, sub_id: str | None = None) -> str:
    """模拟订阅激活（Dodo 风格：激活即绑订阅 + 发首期积分）。返回订阅 id。"""
    sub_id = sub_id or f"sub_fake_{uuid.uuid4().hex[:8]}"
    _webhook(client, "fakepay", {
        "type": "subscription_activated",
        "event_id": f"evt-{uuid.uuid4().hex[:12]}",
        "order_no": _order_no(order),
        "provider_subscription_id": sub_id,
        "provider_customer_id": "cust_fake_1",
    })
    return sub_id


def _balance(client, headers: dict) -> dict:
    resp = client.get("/billing/balance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 目录 ----------


def test_catalog_public(client, fake_provider):
    resp = client.get("/payments/catalog")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["recharge_plans"]) == 3
    assert len(data["subscription_plans"]) == 3
    levels = [p["level"] for p in data["subscription_plans"]]
    assert levels == [1, 2, 3]
    # 限时折扣：现价低于标价
    for p in data["subscription_plans"]:
        assert p["price"] < p["original_price"]
    assert data["credit_price_rate"] == "0.005"
    assert data["max_recharge_amount"] == 500
    assert data["currency"] == "USD"
    assert set(data["pay_channels"]) == {"card", "paypal"}
    assert data["subscription"] is None  # 未登录


# ---------- 充值 ----------


def test_recharge_fixed_plan(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(
        client, headers, kind="recharge", plan_id=PLAN_R5, pay_channel="card"
    )
    assert order["credits"] == "1000.00"
    assert order["amount_cents"] == 500
    _pay(client, fake_provider, order)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(2000.0)  # 免费 1000 + 充值 1000
    assert bal["permanent"] == pytest.approx(2000.0)
    assert bal["expiring"] == pytest.approx(0.0)

    # 订单状态
    resp = client.get(f"/payments/orders/{order['order_id']}", headers=headers)
    assert resp.json()["status"] == "paid"

    # webhook 幂等：重复到账事件不重复发积分（订单已 paid 短路）
    _pay(client, fake_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(2000.0)


def test_recharge_custom_amount(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", amount_cents=500)
    assert order["credits"] == "1000.00"  # $5 ÷ $0.005
    _pay(client, fake_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(2000.0)


def test_recharge_custom_validation(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    # 未给金额
    assert client.post(
        "/payments/orders", json={"kind": "recharge"}, headers=headers
    ).status_code == 400
    # 0 / 负数
    assert client.post(
        "/payments/orders", json={"kind": "recharge", "amount_cents": 0}, headers=headers
    ).status_code == 400
    # 超过上限 $500
    resp = client.post(
        "/payments/orders", json={"kind": "recharge", "amount_cents": 50001}, headers=headers
    )
    assert resp.status_code == 400
    assert "500" in resp.json()["detail"]
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


def test_pay_channel_paypal(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(
        client, headers, kind="recharge", plan_id=PLAN_R5, pay_channel="paypal"
    )
    assert order["pay_channel"] == "paypal"


# ---------- 单渠道声明 ----------


class CardOnlyProvider(FakeProvider):
    """只声明 card 渠道的支付方。"""

    def available_channels(self) -> list[str]:
        return ["card"]


@pytest.fixture()
def card_only_provider(monkeypatch):
    p = CardOnlyProvider()
    monkeypatch.setattr("ai_search.payments.service._provider", p)
    return p


def test_card_only_catalog(client, card_only_provider):
    resp = client.get("/payments/catalog")
    assert resp.status_code == 200, resp.text
    assert resp.json()["pay_channels"] == ["card"]


def test_card_only_default_channel(client, card_only_provider, sent_mails):
    """不传 pay_channel 时自动落到支付方首个已声明渠道。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", plan_id=PLAN_R5)
    assert order["pay_channel"] == "card"  # 响应携带实际渠道（供前端显示）
    _pay(client, card_only_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(2000.0)


def test_card_only_rejects_paypal(client, card_only_provider, sent_mails):
    """未声明的渠道下单返回 400，且提示可用渠道。"""
    headers = _register(client, sent_mails)
    resp = client.post(
        "/payments/orders",
        json={"kind": "recharge", "plan_id": PLAN_R5, "pay_channel": "paypal"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "card" in resp.json()["detail"]


# ---------- 订阅（原生自动续订）----------


def test_subscribe_flow(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(
        client, headers, kind="subscribe", plan_id=PLAN_SUB1, pay_channel="card"
    )
    assert order["amount_cents"] == 499  # 限时折扣价
    assert order["credits"] == "1200.00"
    _activate_subscription(client, order)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(2200.0)   # 1000 永久 + 1200 限时
    assert bal["permanent"] == pytest.approx(1000.0)
    assert bal["expiring"] == pytest.approx(1200.0)
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


def test_renewal_via_webhook(client, fake_provider, sent_mails):
    """平台自动续期：subscription_paid 事件 → 续期积分入账 + 周期顺延；事件幂等。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    sub_id = _activate_subscription(client, order)
    sub1 = client.get("/payments/catalog", headers=headers).json()["subscription"]

    event = {
        "type": "subscription_paid",
        "event_id": f"evt-renew-{uuid.uuid4().hex[:8]}",
        "provider_subscription_id": sub_id,
        "amount_cents": 499,
    }
    _webhook(client, "fakepay", event)

    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(3200.0)
    # 续期积分下一周期才生效：expiring 不变，upcoming +1200
    assert bal["expiring"] == pytest.approx(1200.0)
    assert bal["upcoming"] == pytest.approx(1200.0)

    # 周期顺延 30 天
    from datetime import datetime

    sub2 = client.get("/payments/catalog", headers=headers).json()["subscription"]
    end1 = datetime.fromisoformat(sub1["period_end"])
    end2 = datetime.fromisoformat(sub2["period_end"])
    assert (end2 - end1).days == 30

    # 事件幂等：同一 event_id 重放不重复发积分
    _webhook(client, "fakepay", event)
    assert _balance(client, headers)["balance"] == pytest.approx(3200.0)


def test_upgrade_flow(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    _activate_subscription(client, order)

    # 升级到 Pro：付新档当前售价全额
    up = _create_order(client, headers, kind="upgrade", plan_id=PLAN_SUB2)
    assert up["amount_cents"] == 999
    assert up["credits"] == "3000.00"
    _pay(client, fake_provider, up)

    bal = _balance(client, headers)
    # 原档 1200 未用完积分延期至与新档（3000）同期 → 限时余额 4200
    assert bal["expiring"] == pytest.approx(4200.0)
    sub = client.get("/payments/catalog", headers=headers).json()["subscription"]
    assert sub["level"] == 2


def test_subscription_rules_rejected(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    # 无订阅时续订/升级被拒
    assert client.post(
        "/payments/orders", json={"kind": "renew", "plan_id": PLAN_SUB1}, headers=headers
    ).status_code == 400
    assert client.post(
        "/payments/orders", json={"kind": "upgrade", "plan_id": PLAN_SUB1}, headers=headers
    ).status_code == 400

    # 订阅 Pro 后：降级（upgrade 到 Starter）被拒
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB2)
    _activate_subscription(client, order)
    resp = client.post(
        "/payments/orders", json={"kind": "upgrade", "plan_id": PLAN_SUB1}, headers=headers
    )
    assert resp.status_code == 400
    assert "higher" in resp.json()["detail"]


def test_subscription_cancel_via_webhook(client, fake_provider, sent_mails):
    """平台取消订阅 → 本地标记 canceled，catalog 不再返回有效订阅。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    sub_id = _activate_subscription(client, order)
    assert client.get("/payments/catalog", headers=headers).json()["subscription"]

    _webhook(client, "fakepay", {
        "type": "subscription_canceled",
        "event_id": f"evt-cancel-{uuid.uuid4().hex[:8]}",
        "provider_subscription_id": sub_id,
    })
    assert client.get("/payments/catalog", headers=headers).json()["subscription"] is None


def test_customer_portal(client, fake_provider, sent_mails):
    """有订阅且绑定平台 customer id 时可生成 Customer Portal 链接。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    _activate_subscription(client, order)
    resp = client.get("/payments/portal", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["portal_url"] == "https://portal.example.com/cust_fake_1"


# ---------- Creem 两段式：checkout 绑订阅 + subscription.paid 发积分 ----------


def test_creem_style_subscribe_flow(client, fake_provider, sent_mails):
    """Creem 风格：checkout.completed 只绑订阅，首期积分由 subscription.paid 发放。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="subscribe", plan_id=PLAN_SUB1)
    order_no = _order_no(order)

    sub_id = f"sub_ck_{uuid.uuid4().hex[:8]}"
    _webhook(client, "fakepay", {
        "type": "subscription_checkout", "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "order_no": order_no, "provider_subscription_id": sub_id,
        "provider_customer_id": "cust_ck_1",
    })
    # 绑订阅但尚未发积分
    assert _balance(client, headers)["balance"] == pytest.approx(1000.0)

    _webhook(client, "fakepay", {
        "type": "subscription_paid", "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "provider_subscription_id": sub_id,
    })
    assert _balance(client, headers)["balance"] == pytest.approx(2200.0)

    # 下一周期续期
    _webhook(client, "fakepay", {
        "type": "subscription_paid", "event_id": f"evt-{uuid.uuid4().hex[:8]}",
        "provider_subscription_id": sub_id,
    })
    bal = _balance(client, headers)
    assert bal["balance"] == pytest.approx(3200.0)
    assert bal["upcoming"] == pytest.approx(1200.0)


def test_webhook_bad_signature_rejected(client, fake_provider, sent_mails):
    """验签失败的 webhook 返回 400。"""
    resp = client.post(
        "/payments/webhooks/fakepay",
        content=json.dumps({
            "type": "one_time_paid", "event_id": "evt-x", "order_no": "ORDX",
            "sign": "bad",
        }),
    )
    assert resp.status_code == 400


# ---------- 邮箱验证门禁 ----------


def test_order_requires_email_verified(client, fake_provider):
    """邮箱未验证的用户不允许下单（充值/订阅/续订/升级均 403）。"""
    email = f"pay-unverified-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    for body in (
        {"kind": "recharge", "plan_id": PLAN_R5},
        {"kind": "subscribe", "plan_id": PLAN_SUB1},
        {"kind": "renew", "plan_id": PLAN_SUB1},
        {"kind": "upgrade", "plan_id": PLAN_SUB2},
    ):
        resp = client.post("/payments/orders", json=body, headers=headers)
        assert resp.status_code == 403, (body, resp.text)


def test_order_ok_after_email_verified(client, fake_provider, sent_mails):
    """同一用户完成邮箱验证后即可正常下单。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", plan_id=PLAN_R5)
    assert order["status"] == "pending"


def test_billing_page_prompts_unverified_user(client):
    """未验证用户访问 /dashboard/billing：页面提示先验证邮箱，不放行购买流程。"""
    email = f"pay-dash-{uuid.uuid4().hex[:8]}@example.com"
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
    assert resp.status_code == 303
    page = client.get("/dashboard/billing")
    assert page.status_code == 200
    body = page.text.lower()
    assert "verify" in body and "email" in body
    assert "resend" in body
