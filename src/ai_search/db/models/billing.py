"""套餐与订单模型。

- Plan：套餐。kind 区分 recharge（充值档）/ subscription（包月订阅档）。
  订阅档用 level（1/2/3）做升级比较；original_price_cents 为标价（划线价），
  price_cents 为当前实际售价（限时折扣期=折扣价）。
- Order：订单，kind 区分 recharge/subscribe/renew/upgrade 四种业务；
  pay_channel 记录支付渠道（alipay/wechat）；自定义充值单 plan_id 为 NULL。
  status 流转 pending → paid → (failed/refunded)。
  provider_order_id 存支付方订单号用于回调对账；幂等靠 status 判定。
- credits 为 NUMERIC(20,2)：自定义充值按 ¥0.03=1 积分换算可产生 2 位小数积分。
"""

import enum
import uuid
from decimal import Decimal
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class PlanKind(str, enum.Enum):
    RECHARGE = "recharge"        # 充值档（积分永久有效）
    SUBSCRIPTION = "subscription"  # 包月订阅档（积分 30 天有效）


class OrderKind(str, enum.Enum):
    RECHARGE = "recharge"    # 充值（固定档或自定义金额）
    SUBSCRIBE = "subscribe"  # 首次订阅
    RENEW = "renew"          # 续订（积分下一周期生效）
    UPGRADE = "upgrade"      # 升级（付新档当前售价全额）


class PayChannel(str, enum.Enum):
    ALIPAY = "alipay"
    WECHAT = "wechat"


class Plan(Base, PkMixin, TimestampMixin):
    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PlanKind.RECHARGE.value,
        server_default=PlanKind.RECHARGE.value,
    )
    level: Mapped[int | None] = mapped_column()  # 订阅档位 1/2/3；充值档为 NULL
    credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 当前实际售价（分）
    original_price_cents: Mapped[int | None] = mapped_column(BigInteger)  # 标价（划线价）
    period: Mapped[str | None] = mapped_column(String(16))  # month=包月；None=一次性
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")


class Order(Base, PkMixin, TimestampMixin):
    __tablename__ = "orders"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderKind.RECHARGE.value,
        server_default=OrderKind.RECHARGE.value,
    )
    pay_channel: Mapped[str | None] = mapped_column(String(16))  # alipay/wechat
    credits: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING.value, server_default="pending"
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
