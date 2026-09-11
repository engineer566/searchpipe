"""Dodo Payments（MoR 代收平台）—— 备选收款方（身份证即可 KYC）。

文档：https://docs.dodopayments.com
- 下单：POST {api_base}/checkouts（Authorization: Bearer）
  body: product_cart[{product_id, quantity}] + customer.email + return_url + metadata
  自定义金额充值：用配置的「$1/单位」product，quantity=美元数
- 响应：{"session_id": "cks_...", "checkout_url": "https://checkout.dodopayments.com/..."}
- webhook：Standard Webhooks 规范（webhook-id / webhook-timestamp / webhook-signature 三头）
  签名串 "{id}.{timestamp}.{body}"，HMAC-SHA256 → base64；secret 去 whsec_ 前缀后 base64 解码；
  时间戳容差 ±5 分钟。webhook-id 天然幂等键（重投共享同一 id）。
  事件：payment.succeeded（一次性；订阅扣款也触发但带 subscription_id → 忽略）
        subscription.active（首次激活，含 metadata）/ subscription.renewed（续期）
        subscription.cancelled（英式拼写）
- Customer Portal：POST /customers/{customer_id}/customer-portal/session → 链接
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from ..config import get_settings
from .provider import PaymentEvent, PaymentProvider, WebhookVerificationError

if TYPE_CHECKING:
    from ..db.models import Plan

logger = logging.getLogger(__name__)

_TOLERANCE_SECONDS = 300  # Standard Webhooks 时间戳容差


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class DodoProvider(PaymentProvider):
    """Dodo Payments（托管收银台 + Standard Webhooks）。"""

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.dodo_api_key
        self.webhook_secret = s.dodo_webhook_secret
        self.api_base = s.dodo_api_base.rstrip("/")
        self.credit_product_id = s.dodo_credit_product_id  # $1/单位按量 product

    @property
    def name(self) -> str:
        return "dodo"

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
            raise RuntimeError("Dodo API key not configured")
        if plan is not None:
            product_id = (plan.provider_products or {}).get(self.name, "")
            if not product_id:
                raise RuntimeError(
                    f"Plan {plan.name} has no Dodo product mapping (provider_products)"
                )
            quantity = 1
        else:
            if not self.credit_product_id:
                raise RuntimeError("Dodo credit product (DODO_CREDIT_PRODUCT_ID) not configured")
            if amount_cents % 100 != 0:
                raise ValueError("Custom recharge amount must be a whole number of dollars")
            product_id = self.credit_product_id
            quantity = amount_cents // 100

        payload: dict = {
            "product_cart": [{"product_id": product_id, "quantity": quantity}],
            "metadata": {"order_no": order_no, "kind": kind},
        }
        if success_url:
            payload["return_url"] = success_url
        if customer_email:
            payload["customer"] = {"email": customer_email}

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self.api_base}/checkouts",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        if resp.status_code >= 400:
            logger.error("Dodo create checkout failed: %s %s", resp.status_code, resp.text)
            raise RuntimeError(f"Dodo checkout creation failed ({resp.status_code})")
        data = resp.json()
        url = data.get("checkout_url")
        if not url:
            raise RuntimeError(f"Dodo checkout response missing checkout_url: {data}")
        return url

    def _verify_signature(self, headers: dict, raw_body: bytes) -> str:
        """Standard Webhooks 验签。返回 webhook-id（幂等键）。"""
        if not self.webhook_secret:
            raise WebhookVerificationError("Dodo webhook secret not configured")
        msg_id = headers.get("webhook-id", "")
        timestamp = headers.get("webhook-timestamp", "")
        signature = headers.get("webhook-signature", "")
        if not (msg_id and timestamp and signature):
            raise WebhookVerificationError("Missing webhook-id/timestamp/signature headers")
        try:
            ts = int(timestamp)
        except ValueError as e:
            raise WebhookVerificationError("Invalid webhook-timestamp") from e
        if abs(time.time() - ts) > _TOLERANCE_SECONDS:
            raise WebhookVerificationError("Webhook timestamp outside tolerance")

        secret = self.webhook_secret
        if secret.startswith("whsec_"):
            secret = secret[len("whsec_"):]
        try:
            key = base64.b64decode(secret)
        except Exception as e:  # noqa: BLE001
            raise WebhookVerificationError(f"Invalid webhook secret encoding: {e}") from e

        signed = f"{msg_id}.{timestamp}.".encode() + raw_body
        expected = base64.b64encode(
            hmac.new(key, signed, hashlib.sha256).digest()
        ).decode()
        # 头里是空格分隔的 "v1,<sig>" 列表（支持密钥轮换）
        sigs = [
            part.split(",", 1)[1]
            for part in signature.split(" ")
            if part.startswith("v1,")
        ]
        if not any(hmac.compare_digest(s, expected) for s in sigs):
            raise WebhookVerificationError("Invalid webhook-signature")
        return msg_id

    def verify_webhook(self, *, headers: dict, raw_body: bytes) -> PaymentEvent:
        event_id = self._verify_signature(headers, raw_body)
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as e:
            raise WebhookVerificationError(f"Invalid JSON body: {e}") from e

        event_type = payload.get("type") or ""
        data = payload.get("data") or {}
        metadata = data.get("metadata") or {}
        order_no = metadata.get("order_no")
        customer = data.get("customer") or {}
        customer_id = customer.get("customer_id")

        if event_type == "payment.succeeded":
            if data.get("subscription_id"):
                # 订阅扣款也触发 payment.succeeded，订阅链路统一由
                # subscription.active / subscription.renewed 处理，避免重复发积分
                return PaymentEvent(type="ignored", event_id=event_id, raw=payload)
            return PaymentEvent(
                type="one_time_paid",
                event_id=event_id,
                order_no=order_no,
                provider_customer_id=customer_id,
                provider_product_id=data.get("product_id"),
                amount_cents=data.get("total_amount"),
                raw=payload,
            )

        if event_type == "subscription.active":
            return PaymentEvent(
                type="subscription_activated",
                event_id=event_id,
                order_no=order_no,
                provider_subscription_id=data.get("subscription_id"),
                provider_customer_id=customer_id,
                provider_product_id=data.get("product_id"),
                period_start=_parse_dt(data.get("previous_billing_date")),
                period_end=_parse_dt(data.get("next_billing_date")),
                raw=payload,
            )

        if event_type == "subscription.renewed":
            return PaymentEvent(
                type="subscription_paid",
                event_id=event_id,
                provider_subscription_id=data.get("subscription_id"),
                provider_customer_id=customer_id,
                provider_product_id=data.get("product_id"),
                period_start=_parse_dt(data.get("previous_billing_date")),
                period_end=_parse_dt(data.get("next_billing_date")),
                raw=payload,
            )

        if event_type == "subscription.cancelled":
            return PaymentEvent(
                type="subscription_canceled",
                event_id=event_id,
                provider_subscription_id=data.get("subscription_id"),
                raw=payload,
            )

        return PaymentEvent(type="ignored", event_id=event_id, raw=payload)

    async def customer_portal_url(self, provider_customer_id: str) -> str | None:
        if not self.api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{self.api_base}/customers/{provider_customer_id}/customer-portal/session",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code >= 400:
                logger.error("Dodo portal failed: %s %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            return data.get("link") or data.get("url")
        except Exception:  # noqa: BLE001
            logger.exception("Dodo portal link generation failed")
            return None
