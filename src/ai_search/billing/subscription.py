"""包月订阅领域逻辑 —— 订阅/续订/升级的到账处理。

规则（与服务条款一致）：
- 订阅积分自到账起 30 天有效（固定 30 天）。
- 续订：手动发起，积分批次从下一周期起点生效（不累积到当前周期），
  订阅 period_end 顺延 30 天，可链式叠加。
- 升级：付新档位当前实际售价全额；新档位积分从升级时刻起算 30 天；
  原档位未用完的限时积分延期至与新档位同期；不支持降级。
- 不支持自动续订。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CreditLot,
    CreditTxType,
    Order,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from .service import grant_credits

logger = logging.getLogger(__name__)

SUBSCRIPTION_PERIOD = timedelta(days=30)  # 「一个月」按固定 30 天计


async def get_active_subscription(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> Subscription | None:
    """取当前有效订阅；周期已结束的惰性标记 expired 并返回 None。"""
    now = now or datetime.now(timezone.utc)
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.status == SubscriptionStatus.ACTIVE.value,
    )
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if sub and sub.current_period_end <= now:
        sub.status = SubscriptionStatus.EXPIRED.value
        await db.flush()
        return None
    return sub


async def fulfill_subscribe(db: AsyncSession, order: Order, plan: Plan) -> None:
    """首次订阅到账：建订阅（now → now+30d）+ 限时积分批次。"""
    now = datetime.now(timezone.utc)
    db.add(
        Subscription(
            user_id=order.user_id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE.value,
            current_period_start=now,
            current_period_end=now + SUBSCRIPTION_PERIOD,
        )
    )
    await grant_credits(
        db,
        user_id=order.user_id,
        amount=Decimal(order.credits),
        tx_type=CreditTxType.RECHARGE.value,
        remark=f"订阅 {plan.name}（订单 {order.provider_order_id}）",
        lot_source="subscribe",
        lot_order_id=order.id,
        lot_effective_at=now,
        lot_expires_at=now + SUBSCRIPTION_PERIOD,
    )
    logger.info("订阅到账 user=%s plan=%s", order.user_id, plan.name)


async def fulfill_renew(
    db: AsyncSession, order: Order, plan: Plan, sub: Subscription
) -> None:
    """续订到账：积分批次下一周期生效（原 period_end → +30d），period_end 顺延。"""
    old_end = sub.current_period_end
    new_end = old_end + SUBSCRIPTION_PERIOD
    sub.current_period_end = new_end
    await grant_credits(
        db,
        user_id=order.user_id,
        amount=Decimal(order.credits),
        tx_type=CreditTxType.RECHARGE.value,
        remark=f"续订 {plan.name}（订单 {order.provider_order_id}）",
        lot_source="renew",
        lot_order_id=order.id,
        lot_effective_at=old_end,
        lot_expires_at=new_end,
    )
    logger.info(
        "续订到账 user=%s plan=%s 生效=%s", order.user_id, plan.name, old_end
    )


async def fulfill_upgrade(
    db: AsyncSession, order: Order, plan: Plan, sub: Subscription
) -> None:
    """升级到账：新档位批次 now → now+30d；原档位未用完限时积分延期至同期。

    已排队但未生效的续订批次（effective_at > now）不延期，仍按原时刻生效。
    """
    now = datetime.now(timezone.utc)
    new_expiry = now + SUBSCRIPTION_PERIOD

    # 原档位未用完的限时积分延期至与新档位同期
    stmt = select(CreditLot).where(
        CreditLot.user_id == order.user_id,
        CreditLot.remaining > 0,
        CreditLot.expires_at.is_not(None),
        CreditLot.expires_at > now,
        CreditLot.effective_at <= now,
    )
    lots = list((await db.execute(stmt)).scalars().all())
    for lot in lots:
        if lot.expires_at < new_expiry:
            lot.expires_at = new_expiry

    # 订阅切到新档位，周期重置为 now → now+30d
    sub.plan_id = plan.id
    sub.current_period_start = now
    sub.current_period_end = new_expiry

    await grant_credits(
        db,
        user_id=order.user_id,
        amount=Decimal(order.credits),
        tx_type=CreditTxType.RECHARGE.value,
        remark=f"升级 {plan.name}（订单 {order.provider_order_id}）",
        lot_source="upgrade",
        lot_order_id=order.id,
        lot_effective_at=now,
        lot_expires_at=new_expiry,
    )
    logger.info(
        "升级到账 user=%s plan=%s 延期批次=%d", order.user_id, plan.name, len(lots)
    )
