"""Creem 支付（MoR 代收平台）—— 出海默认收款方。

文档：https://docs.creem.io
- 下单：POST {api_base}/v1/checkouts（x-api-key 头）
  body: product_id + request_id(业务订单号) + metadata + customer.email + success_url
  自定义金额充值：用配置的「$1/单位」按量 product，units=美元数（总价=单价×units）
- 响应：{"id": "ch_...", "checkout_url": "https://checkout.creem.io/ch_..."}
- webhook：creem-signature 头 = HMAC-SHA256(raw_body, webhook_secret) hex
  事件：checkout.completed（含 request_id/order/subscription）
        subscription.paid（含 period 日期，首期+续期都发，官方建议用它激活访问）
        subscription.canceled
- Customer Portal：POST /v1/customers/billing-portal {customer_id} → customer_portal_link
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from ..config import get_settings
from .provider import PaymentEvent, PaymentProvider, WebhookVerificationError

if TYPE_CHECKING:
    from ..db.models import Plan

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class CreemProvider(PaymentProvider):
    """Creem（托管收银台 + HMAC-SHA256 webhook）。"""

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.creem_api_key
        self.webhook_secret = s.creem_webhook_secret
        self.api_base = s.creem_api_base.rstrip("/")
        self.credit_product_id = s.creem_credit_product_id  # $1/单位按量 product

    @property
    def name(self) -> str:
        return "creem"

    async def create_checkout(
        self,
        *,
        order_no: str,
        kind: str,
        amount_cents: int,
        subject: str,
        plan: "Plan | None" = None,
        customer_email: str | None = None,
        success_url: str | None = None,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("Creem API key not configured")
        if plan is not None:
            product_id = (plan.provider_products or {}).get(self.name, "")
            if not product_id:
                raise RuntimeError(
                    f"Plan {plan.name} has no Creem product mapping (provider_products)"
                )
            units = None
        else:
            # 自定义金额充值：$1/单位 product × units=美元数
            if not self.credit_product_id:
                raise RuntimeError("Creem credit product (CREEM_CREDIT_PRODUCT_ID) not configured")
            if amount_cents % 100 != 0:
                raise ValueError("Custom recharge amount must be a whole number of dollars")
            product_id = self.credit_product_id
            units = amount_cents // 100

        payload: dict = {
            "product_id": product_id,
            "request_id": order_no,
            "metadata": {"order_no": order_no, "kind": kind},
        }
        if units:
            payload["units"] = units
        if success_url:
            payload["success_url"] = success_url
        if customer_email:
            payload["customer"] = {"email": customer_email}

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.api_base}/v1/checkouts",
                json=payload,
                headers={"x-api-key": self.api_key},
            )
        if resp.status_code >= 400:
            logger.error("Creem create checkout failed: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"Creem checkout creation failed ({resp.status_code})")
        data = resp.json()
        url = data.get("checkout_url")
        if not url:
            raise RuntimeError(f"Creem checkout response missing checkout_url: {data}")
        return url

    def verify_webhook(self, *, headers: dict, raw_body: bytes) -> PaymentEvent:
        if not self.webhook_secret:
            raise WebhookVerificationError("Creem webhook secret not configured")
        sig = headers.get("creem-signature", "")
        expected = hmac.new(
            self.webhook_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not sig or not hmac.compare_digest(sig, expected):
            raise WebhookVerificationError("Invalid creem-signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise WebhookVerificationError(f"Invalid JSON body: {e}") from e

        event_id = payload.get("id") or ""
        event_type = payload.get("eventType") or ""
        obj = payload.get("object") or {}
        if not event_id:
            raise WebhookVerificationError("Webhook payload missing event id")

        if event_type == "checkout.completed":
            order_no = obj.get("request_id") or (obj.get("metadata") or {}).get("order_no")
            sub = obj.get("subscription") or None
            customer = obj.get("customer") or {}
            order = obj.get("order") or {}
            product = obj.get("product") or {}
            if sub:
                # 订阅收银台完成：只绑订阅，积分由 subscription.paid 发放
                return PaymentEvent(
                    type="subscription_checkout",
                    event_id=event_id,
                    order_no=order_no,
                    provider_subscription_id=sub.get("id"),
                    provider_customer_id=customer.get("id"),
                    provider_product_id=product.get("id"),
                    amount_cents=order.get("amount"),
                    raw=payload,
                )
            return PaymentEvent(
                type="one_time_paid",
                event_id=event_id,
                order_no=order_no,
                provider_customer_id=customer.get("id"),
                provider_product_id=product.get("id"),
                amount_cents=order.get("amount"),
                raw=payload,
            )

        if event_type == "subscription.paid":
            # 首期 + 续期都会触发（官方建议用它激活访问）
            product = obj.get("product") or {}
            return PaymentEvent(
                type="subscription_paid",
                event_id=event_id,
                provider_subscription_id=obj.get("id"),
                provider_product_id=product.get("id"),
                period_start=_parse_dt(obj.get("current_period_start_date")),
                period_end=_parse_dt(obj.get("current_period_end_date")),
                raw=payload,
            )

        if event_type == "subscription.canceled":
            return PaymentEvent(
                type="subscription_canceled",
                event_id=event_id,
                provider_subscription_id=obj.get("id"),
                raw=payload,
            )

        # subscription.active / subscription.update / refund.created 等：同步类事件，忽略
        return PaymentEvent(type="ignored", event_id=event_id, raw=payload)

    async def customer_portal_url(self, provider_customer_id: str) -> str | None:
        if not self.api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.api_base}/v1/customers/billing-portal",
                    json={"customer_id": provider_customer_id},
                    headers={"x-api-key": self.api_key},
                )
            if resp.status_code >= 400:
                logger.error("Creem portal failed: %s %s", resp.status_code, resp.text)
                return None
            return resp.json().get("customer_portal_link")
        except Exception:  # noqa: BLE001
            logger.exception("Creem portal link generation failed")
            return None
