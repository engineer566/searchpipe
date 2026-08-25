"""支付订单服务 —— 创建订单、幂等回调发放积分。

关键：handle_callback 幂等——已 paid 的订单直接返回成功，不重复发积分。
fulfill_order 在同一事务内完成：order.status=paid + grant_credits(ref=order.id)。
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import grant_credits
from ..config import get_settings
from ..db.models import Order, OrderStatus, Plan
from .provider import PaymentProvider
from .xunhupay import XunHuPayProvider

logger = logging.getLogger(__name__)

_provider: PaymentProvider | None = None


def get_provider() -> PaymentProvider:
    """按配置返回支付提供方单例。"""
    global _provider
    if _provider is None:
        s = get_settings()
        if s.payment_provider == "xunhupay":
            _provider = XunHuPayProvider()
        else:
            raise RuntimeError(f"未知支付提供方: {s.payment_provider}")
    return _provider


def _gen_order_no() -> str:
    """生成业务订单号（支付方要求唯一）。"""
    return f"ORD{uuid.uuid4().hex[:20].upper()}"


async def create_order_from_plan(
    db: AsyncSession,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> tuple[Order, str]:
    """按套餐创建订单。返回 (Order, 支付链接)。"""
    plan = await db.get(Plan, plan_id)
    if not plan or not plan.is_active:
        raise ValueError("套餐不存在或已下架")

    order_no = _gen_order_no()
    order = Order(
        user_id=user_id,
        plan_id=plan.id,
        credits=plan.credits,
        amount_cents=plan.price_cents,
        status=OrderStatus.PENDING.value,
        provider=get_provider().name,
        provider_order_id=order_no,
    )
    db.add(order)
    await db.flush()

    pay_url = await get_provider().create_order(
        order_no=order_no,
        amount_cents=plan.price_cents,
        subject=f"SearchPipe 充值 {plan.name}",
    )
    return order, pay_url


async def get_order(
    db: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID
) -> Order | None:
    order = await db.get(Order, order_id)
    if order and order.user_id != user_id:
        return None
    return order


async def fulfill_order(db: AsyncSession, order: Order) -> None:
    """订单支付成功发放逻辑（同事务）：标 paid + 发积分。

    幂等：调用前应检查 status；此处也做二次保护。
    """
    if order.status == OrderStatus.PAID.value:
        return  # 已支付，幂等
    order.status = OrderStatus.PAID.value
    order.paid_at = datetime.now(timezone.utc)
    await db.flush()
    await grant_credits(
        db,
        user_id=order.user_id,
        amount=order.credits,
        tx_type="recharge",
        ref_order_id=order.id if isinstance(order.id, int) else None,
        remark=f"充值订单 {order.provider_order_id}",
    )
    logger.info("订单完成发放 order=%s credits=%d", order.id, order.credits)


async def handle_callback(db: AsyncSession, params: dict) -> bool:
    """处理支付方异步回调。验签 + 幂等 + 发积分。返回是否处理成功。

    幂等：已 paid 的订单直接返回 True（不重复发积分）。
    """
    provider = get_provider()
    if not provider.verify_callback(params):
        logger.warning("支付回调验签失败: %s", params)
        return False

    order_no = params.get("out_trade_id") or params.get("trade_order_id") or params.get("out_trade_no")
    if not order_no:
        logger.warning("支付回调缺少订单号: %s", params)
        return False

    stmt = select(Order).where(Order.provider_order_id == order_no)
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        logger.warning("支付回调订单不存在: %s", order_no)
        return False

    if order.status == OrderStatus.PAID.value:
        logger.info("支付回调重复，订单已 paid: %s", order_no)
        return True  # 幂等

    await fulfill_order(db, order)
    return True
