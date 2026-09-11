"""订阅模型。

- Subscription：用户包月订阅。同一用户至多一条 active（部分唯一索引
  ix_subscriptions_user_active 在迁移中创建）。
- current_period_start/end：当前周期（固定 30 天）。续订把 period_end 顺延 30 天
  （可链式叠加），续订积分批次 effective_at=原 period_end，不累积到当前周期。
- 升级：plan_id 切到新档位，周期重置为升级时刻起 30 天；不支持降级（服务层校验）。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    CANCELED = "canceled"   # 平台侧已取消（当期积分到期自然失效，不回收）
    PAST_DUE = "past_due"   # 平台侧扣款失败宽限期
    EXPIRED = "expired"


class Subscription(Base, PkMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SubscriptionStatus.ACTIVE.value,
        server_default=SubscriptionStatus.ACTIVE.value,
    )
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 原生自动续订：平台托管扣款，webhook 按这两个字段定位本地订阅
    provider: Mapped[str | None] = mapped_column(String(16))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128))
