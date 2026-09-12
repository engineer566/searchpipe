"""支付 provider 单元测试 —— Creem / Dodo 真实签名算法 + 收银台 payload。

不碰 DB：直接构造 provider 实例（monkeypatch 模块级 get_settings 注入测试密钥），
验 verify_webhook 的验签与事件映射，以及 create_checkout 真正发出的 JSON
（httpx.AsyncClient 被替换为捕获式假客户端）。链路级测试在 test_payments.py。
"""

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

from ai_search.payments.provider import WebhookVerificationError

# ---------- Creem ----------

_CREEM_SECRET = "creem-test-secret"


def _creem_provider(monkeypatch):
    from ai_search.payments import creem

    monkeypatch.setattr(
        creem,
        "get_settings",
        lambda: SimpleNamespace(
            creem_api_key="ck_test",
            creem_webhook_secret=_CREEM_SECRET,
            creem_api_base="https://test-api.creem.io",
            creem_credit_product_id="prod_credit",
        ),
    )
    return creem.CreemProvider()


def _creem_signed(body: dict, secret: str = _CREEM_SECRET) -> tuple[dict, bytes]:
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"creem-signature": sig}, raw


def test_creem_one_time_checkout_completed(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({
        "id": "evt_1",
        "eventType": "checkout.completed",
        "object": {
            "request_id": "ORD123",
            "order": {"id": "ord_1", "amount": 500, "currency": "USD"},
            "product": {"id": "prod_x"},
            "customer": {"id": "cust_1", "email": "a@b.c"},
        },
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "one_time_paid"
    assert ev.event_id == "evt_1"
    assert ev.order_no == "ORD123"
    assert ev.amount_cents == 500
    assert ev.provider_customer_id == "cust_1"


def test_creem_subscription_checkout_completed(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({
        "id": "evt_2",
        "eventType": "checkout.completed",
        "object": {
            "request_id": "ORD124",
            "order": {"id": "ord_2", "amount": 499},
            "product": {"id": "prod_sub"},
            "customer": {"id": "cust_2"},
            "subscription": {"id": "sub_2"},
        },
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_checkout"
    assert ev.order_no == "ORD124"
    assert ev.provider_subscription_id == "sub_2"


def test_creem_subscription_paid_has_period(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({
        "id": "evt_3",
        "eventType": "subscription.paid",
        "object": {
            "id": "sub_2",
            "product": {"id": "prod_sub"},
            "current_period_start_date": "2026-09-14T00:00:00.000Z",
            "current_period_end_date": "2026-10-14T00:00:00.000Z",
        },
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_paid"
    assert ev.provider_subscription_id == "sub_2"
    assert ev.provider_product_id == "prod_sub"
    assert ev.period_start is not None and ev.period_end is not None
    assert ev.period_start.day == 14


def test_creem_subscription_canceled(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({
        "id": "evt_4", "eventType": "subscription.canceled", "object": {"id": "sub_9"},
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_canceled"
    assert ev.provider_subscription_id == "sub_9"


def test_creem_unknown_event_ignored(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({"id": "evt_5", "eventType": "refund.created", "object": {}})
    assert p.verify_webhook(headers=headers, raw_body=raw).type == "ignored"


def test_creem_bad_signature_rejected(monkeypatch):
    p = _creem_provider(monkeypatch)
    headers, raw = _creem_signed({"id": "evt_6", "eventType": "checkout.completed", "object": {}},
                                 secret="wrong-secret")
    with pytest.raises(WebhookVerificationError):
        p.verify_webhook(headers=headers, raw_body=raw)


def test_creem_missing_signature_rejected(monkeypatch):
    p = _creem_provider(monkeypatch)
    _, raw = _creem_signed({"id": "evt_7", "eventType": "checkout.completed", "object": {}})
    with pytest.raises(WebhookVerificationError):
        p.verify_webhook(headers={}, raw_body=raw)


# ---------- Dodo（Standard Webhooks）----------

_DODO_KEY = b"dodo-test-key-32bytes-for-hmac!!"
_DODO_SECRET = "whsec_" + base64.b64encode(_DODO_KEY).decode()


def _dodo_provider(monkeypatch):
    from ai_search.payments import dodo

    monkeypatch.setattr(
        dodo,
        "get_settings",
        lambda: SimpleNamespace(
            dodo_api_key="dk_test",
            dodo_webhook_secret=_DODO_SECRET,
            dodo_api_base="https://test.dodopayments.com",
            dodo_credit_product_id="pdt_credit",
        ),
    )
    return dodo.DodoProvider()


def _dodo_signed(body: dict, key: bytes = _DODO_KEY, ts: int | None = None,
                 msg_id: str = "msg_1") -> tuple[dict, bytes]:
    raw = json.dumps(body).encode()
    ts = ts or int(time.time())
    signed = f"{msg_id}.{ts}.".encode() + raw
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": f"v1,{sig}",
    }, raw


def test_dodo_one_time_payment_succeeded(monkeypatch):
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({
        "type": "payment.succeeded",
        "data": {
            "payload_type": "Payment",
            "payment_id": "pay_1",
            "total_amount": 500,
            "product_id": "pdt_x",
            "customer": {"customer_id": "cust_1"},
            "metadata": {"order_no": "ORD200"},
        },
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "one_time_paid"
    assert ev.event_id == "msg_1"  # webhook-id 作为幂等键
    assert ev.order_no == "ORD200"
    assert ev.amount_cents == 500


def test_dodo_subscription_payment_ignored(monkeypatch):
    """订阅扣款的 payment.succeeded 带 subscription_id → 忽略（由 subscription 事件处理）。"""
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({
        "type": "payment.succeeded",
        "data": {
            "payload_type": "Payment",
            "payment_id": "pay_2",
            "subscription_id": "sub_1",
            "metadata": {"order_no": "ORD201"},
        },
    })
    assert p.verify_webhook(headers=headers, raw_body=raw).type == "ignored"


def test_dodo_subscription_active(monkeypatch):
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({
        "type": "subscription.active",
        "data": {
            "payload_type": "Subscription",
            "subscription_id": "sub_1",
            "product_id": "pdt_sub",
            "customer": {"customer_id": "cust_1"},
            "metadata": {"order_no": "ORD202"},
            "previous_billing_date": "2026-09-14T00:00:00Z",
            "next_billing_date": "2026-10-14T00:00:00Z",
        },
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_activated"
    assert ev.order_no == "ORD202"
    assert ev.provider_subscription_id == "sub_1"
    assert ev.period_end is not None


def test_dodo_subscription_renewed_and_cancelled(monkeypatch):
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({
        "type": "subscription.renewed",
        "data": {"subscription_id": "sub_1", "product_id": "pdt_sub",
                 "next_billing_date": "2026-11-14T00:00:00Z"},
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_paid"
    assert ev.provider_subscription_id == "sub_1"

    headers, raw = _dodo_signed({
        "type": "subscription.cancelled",
        "data": {"subscription_id": "sub_1"},
    })
    ev = p.verify_webhook(headers=headers, raw_body=raw)
    assert ev.type == "subscription_canceled"


def test_dodo_bad_signature_rejected(monkeypatch):
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({"type": "payment.succeeded", "data": {}},
                                key=b"wrong-key-wrong-key-wrong-key!!!")
    with pytest.raises(WebhookVerificationError):
        p.verify_webhook(headers=headers, raw_body=raw)


def test_dodo_stale_timestamp_rejected(monkeypatch):
    p = _dodo_provider(monkeypatch)
    headers, raw = _dodo_signed({"type": "payment.succeeded", "data": {}},
                                ts=int(time.time()) - 600)
    with pytest.raises(WebhookVerificationError):
        p.verify_webhook(headers=headers, raw_body=raw)


def test_dodo_missing_headers_rejected(monkeypatch):
    p = _dodo_provider(monkeypatch)
    _, raw = _dodo_signed({"type": "payment.succeeded", "data": {}})
    with pytest.raises(WebhookVerificationError):
        p.verify_webhook(headers={}, raw_body=raw)


# ---------- 收银台 payload（自定义充值按分计价）----------
#
# 历史 bug：自定义充值曾用「$1 product × units=美元数」，provider 里
# `amount_cents % 100 != 0` 会把 $3.50 这类金额直接拒掉（路由回 400），
# 而前端输入框允许 step=0.01。现改为按分传价（Creem custom_price /
# Dodo product_cart[].amount）。这些测试直接断言发出的 JSON。


class _FakeCheckoutResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"checkout_url": "https://checkout.example/ch_test"}


class _FakeAsyncClient:
    """捕获 create_checkout 发出的请求，不触网。"""

    def __init__(self, sink: dict):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self._sink.update({"url": url, "json": json, "headers": headers})
        return _FakeCheckoutResponse()


def _capture_request(monkeypatch, module, sink: dict) -> None:
    monkeypatch.setattr(
        module.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(sink)
    )


async def test_creem_custom_amount_uses_custom_price(monkeypatch):
    """$3.50 必须按分传 custom_price，且不再带 units。"""
    from ai_search.payments import creem

    p = _creem_provider(monkeypatch)
    sink: dict = {}
    _capture_request(monkeypatch, creem, sink)

    url = await p.create_checkout(
        order_no="ORDCENTS1",
        kind="recharge",
        amount_cents=350,
        subject="Custom recharge",
        customer_email="a@b.c",
        success_url="https://searchpipe.tech/dashboard/billing",
    )

    assert url == "https://checkout.example/ch_test"
    assert sink["json"]["custom_price"] == 350
    assert "units" not in sink["json"]
    assert sink["json"]["product_id"] == "prod_credit"
    assert sink["json"]["request_id"] == "ORDCENTS1"
    assert sink["headers"]["x-api-key"] == "ck_test"


async def test_creem_custom_amount_below_one_dollar_rejected(monkeypatch):
    """Creem custom_price 硬下限 100 分（$1）：本地拦下，不打 API。"""
    p = _creem_provider(monkeypatch)
    with pytest.raises(ValueError):
        await p.create_checkout(
            order_no="ORDCENTS2", kind="recharge", amount_cents=50, subject="Custom recharge"
        )


async def test_creem_plan_checkout_uses_mapped_product(monkeypatch):
    """固定档/订阅档走 provider_products 映射，不带 custom_price / units。"""
    from ai_search.payments import creem

    p = _creem_provider(monkeypatch)
    sink: dict = {}
    _capture_request(monkeypatch, creem, sink)
    plan = SimpleNamespace(name="Starter", provider_products={"creem": "prod_starter"})

    await p.create_checkout(
        order_no="ORDPLAN1", kind="subscribe", amount_cents=499, subject="s", plan=plan
    )

    assert sink["json"]["product_id"] == "prod_starter"
    assert "custom_price" not in sink["json"]
    assert "units" not in sink["json"]


async def test_dodo_custom_amount_uses_cart_amount(monkeypatch):
    """Dodo 动态定价：product_cart[].amount 按分（需 product 开启 PWYW）。"""
    from ai_search.payments import dodo

    p = _dodo_provider(monkeypatch)
    sink: dict = {}
    _capture_request(monkeypatch, dodo, sink)

    await p.create_checkout(
        order_no="ORDDODO1", kind="recharge", amount_cents=350, subject="Custom recharge"
    )

    item = sink["json"]["product_cart"][0]
    assert item["product_id"] == "pdt_credit"
    assert item["quantity"] == 1
    assert item["amount"] == 350
    assert sink["headers"]["Authorization"] == "Bearer dk_test"
