"""套餐与订单模型。

- Plan：预定义充值套餐（credits + price_cents），is_active 控制是否上架。
- Order：充值订单，status 流转 pending → paid → (failed/refunded)。
  provider_order_id 存支付方返回的订单号，用于回调对账；幂等靠 status 判定。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class Plan(Base, PkMixin, TimestampMixin):
    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)  # CNY 分
    period: Mapped[str | None] = mapped_column(String(16))  # once/month/year，None=一次性
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")


class Order(Base, PkMixin, TimestampMixin):
    __tablename__ = "orders"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plans.id"))
    credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING.value, server_default="pending"
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
