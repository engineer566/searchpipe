"""支付渠道测试 —— 充值（固定档/自定义）+ 订阅（订阅/续订/升级/拒绝降级）+ 回调幂等。

策略与 conftest 一致：真实 Postgres + Redis，全部经 TestClient → ASGI app
内部 task 访问 DB，不直连。支付提供方用 FakeProvider 替换（monkeypatch 模块
单例 _provider），不下真单；回调用 sign=ok 模拟验签通过。

种子套餐（迁移 b71f2c3d9e50 固定 UUID）：
- 充值：…0001=¥10/350 …0002=¥20/700 …0003=¥50/1800 …0004=¥100/5000
- 订阅：…0001=档1 ¥9.99/1000 …0002=档2 ¥24.99/3000 …0003=档3 ¥49.99/10000
"""

import re
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

    def available_channels(self) -> list[str]:
        return ["alipay", "wechat"]


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
    """API 注册 + 完成邮箱验证 → 返回认证头（2026-09-12 需求 7：下单需已验证）。"""
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


def test_catalog_public(client, fake_provider):
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


def test_recharge_fixed_plan(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
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


def test_recharge_custom_amount(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", amount_cents=500)
    assert order["credits"] == "166.67"  # ¥5 ÷ 0.03
    _pay(client, fake_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(1166.67)


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


def test_pay_channel_wechat(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
    order = _create_order(
        client, headers, kind="recharge", plan_id=PLAN_R10, pay_channel="wechat"
    )
    assert "channel=wechat" in order["pay_url"]


# ---------- 单渠道商户（仅微信）----------


class WechatOnlyProvider(FakeProvider):
    """模拟只开通微信渠道的商户（如当前虎皮椒商户）。"""

    def available_channels(self) -> list[str]:
        return ["wechat"]


@pytest.fixture()
def wechat_only_provider(monkeypatch):
    p = WechatOnlyProvider()
    monkeypatch.setattr("ai_search.payments.service._provider", p)
    return p


def test_wechat_only_catalog(client, wechat_only_provider):
    resp = client.get("/payments/catalog")
    assert resp.status_code == 200, resp.text
    assert resp.json()["pay_channels"] == ["wechat"]


def test_wechat_only_default_channel(client, wechat_only_provider, sent_mails):
    """不传 pay_channel 时自动落到已配置的微信渠道。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", plan_id=PLAN_R10)
    assert "channel=wechat" in order["pay_url"]
    assert order["pay_channel"] == "wechat"  # 响应携带实际渠道（供前端显示）
    _pay(client, wechat_only_provider, order)
    assert _balance(client, headers)["balance"] == pytest.approx(1350.0)


def test_wechat_only_rejects_alipay(client, wechat_only_provider, sent_mails):
    """未开通的渠道下单返回 400，且提示可用渠道。"""
    headers = _register(client, sent_mails)
    resp = client.post(
        "/payments/orders",
        json={"kind": "recharge", "plan_id": PLAN_R10, "pay_channel": "alipay"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "暂未开通" in resp.json()["detail"]
    assert "微信" in resp.json()["detail"]


def test_xunhupay_available_channels_by_credentials(monkeypatch):
    """虎皮椒 provider 按已配置凭证判定渠道：仅微信凭证 → ["wechat"]。"""
    from types import SimpleNamespace

    from ai_search.payments import xunhupay

    fake_settings = SimpleNamespace(
        xunhupay_notify_url="http://example.com/callback",
        xunhupay_appid="",
        xunhupay_appsecret="",
        xunhupay_appid_alipay="",
        xunhupay_appsecret_alipay="",
        xunhupay_appid_wechat="201906187427",
        xunhupay_appsecret_wechat="s3cret",
    )
    monkeypatch.setattr(xunhupay, "get_settings", lambda: fake_settings)
    provider = xunhupay.XunHuPayProvider()
    assert provider.available_channels() == ["wechat"]


# ---------- 订阅 ----------


def test_subscribe_flow(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
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


def test_renew_flow(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
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


def test_upgrade_flow(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
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


def test_subscription_rules_rejected(client, fake_provider, sent_mails):
    headers = _register(client, sent_mails)
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


# ---------- 邮箱验证门禁（2026-09-12 需求 7）----------


def test_order_requires_email_verified(client, fake_provider):
    """邮箱未验证的用户不允许下单（充值/订阅/续订/升级均 403）。"""
    email = f"pay-unverified-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/auth/register", json={"email": email, "password": _PW})
    assert resp.status_code == 201, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    for body in (
        {"kind": "recharge", "plan_id": PLAN_R10},
        {"kind": "subscribe", "plan_id": PLAN_SUB1},
        {"kind": "renew", "plan_id": PLAN_SUB1},
        {"kind": "upgrade", "plan_id": PLAN_SUB2},
    ):
        resp = client.post("/payments/orders", json=body, headers=headers)
        assert resp.status_code == 403, (body, resp.text)
        assert "验证邮箱" in resp.json()["detail"]


def test_order_ok_after_email_verified(client, fake_provider, sent_mails):
    """同一用户完成邮箱验证后即可正常下单。"""
    headers = _register(client, sent_mails)
    order = _create_order(client, headers, kind="recharge", plan_id=PLAN_R10)
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
    assert "充值前请先完成邮箱验证" in page.text
    assert "重发验证邮件" in page.text
