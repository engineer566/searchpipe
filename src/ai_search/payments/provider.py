"""支付提供方抽象 —— MoR 代收平台（Creem / Dodo Payments）。

海外支付商全是「托管收银台 redirect + JSON webhook 签名验证」模式：
- create_checkout(...) → 托管收银台 URL（用户跳转付款）
- verify_webhook(headers, raw_body) → 归一化 PaymentEvent（验签失败抛 WebhookVerificationError）

webhook 事件归一化为五种语义类型：
- one_time_paid         一次性付款成功（充值）→ 发积分
- subscription_checkout 订阅收银台完成（Creem checkout.completed）→ 绑订阅，不发积分
                        （Creem 的积分统一由 subscription.paid 发放，官方建议如此）
- subscription_activated 订阅激活（Dodo subscription.active）→ 绑订阅 + 发首期积分
- subscription_paid     订阅周期扣款成功（Creem subscription.paid / Dodo subscription.renewed）
                        → 首期或续期发积分，按 event_id 幂等
- subscription_canceled 订阅取消 → 本地标记 canceled（已发积分不回收）
- ignored               与本系统无关的事件（如 Dodo 订阅单的 payment.succeeded）→ 直接 ack

国内版虎皮椒实现已在 archive/china-2026-09 tag 封存。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..db.models import Plan

logger = logging.getLogger(__name__)


class WebhookVerificationError(Exception):
    """webhook 验签失败或载荷不合法。"""


@dataclass
class PaymentEvent:
    """归一化后的支付平台 webhook 事件。"""

    type: str  # one_time_paid / subscription_checkout / subscription_activated
               # / subscription_paid / subscription_canceled / ignored
    event_id: str                                # 平台事件唯一 id（幂等键）
    order_no: str | None = None                  # 下单时塞入的业务订单号
    provider_subscription_id: str | None = None
    provider_customer_id: str | None = None
    provider_product_id: str | None = None       # 续费时反查本地 Plan
    amount_cents: int | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    """支付提供方接口。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
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
        """创建托管收银台会话，返回收银台 URL。

        plan 非空时用 plan.provider_products[name] 定位平台 product；
        plan 为空（自定义金额充值）时用配置的「$1/单位」按量 product × units=美元数。
        """
        ...

    @abstractmethod
    def verify_webhook(self, *, headers: dict, raw_body: bytes) -> PaymentEvent:
        """验签 + 解析 webhook，返回归一化事件。失败抛 WebhookVerificationError。"""
        ...

    @abstractmethod
    async def customer_portal_url(self, provider_customer_id: str) -> str | None:
        """生成 Customer Portal 链接（取消/改档/换支付方式）。失败返回 None。"""
        ...

    def available_channels(self) -> list[str]:
        """收银台可用支付渠道（实际展示由平台决定，此处仅作声明）。"""
        return ["card", "paypal"]
