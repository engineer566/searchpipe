"""支付提供方抽象 —— 虎皮椒过渡，后续换官方商户只换实现。

PaymentProvider：
- create_order(order_no, amount_cents, subject) → 支付链接/二维码 URL
- verify_callback(params) → bool（验签）
- available_channels() → 已配置可用的支付渠道（默认双渠道，子类按凭证覆盖）
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class PaymentProvider(ABC):
    """支付提供方接口。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def create_order(
        self, order_no: str, amount_cents: int, subject: str, pay_channel: str = "alipay"
    ) -> str:
        """创建支付订单，返回支付页/二维码 URL。pay_channel: alipay/wechat。"""
        ...

    @abstractmethod
    def verify_callback(self, params: dict) -> bool:
        """校验异步回调签名。"""
        ...

    def available_channels(self) -> list[str]:
        """已配置可用的支付渠道（alipay/wechat）。默认双渠道，子类按凭证覆盖。"""
        return ["alipay", "wechat"]
