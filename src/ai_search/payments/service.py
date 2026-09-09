"""支付订单服务 —— 四类下单（充值/订阅/续订/升级）+ 幂等回调发放。

下单规则：
- recharge：固定充值档（plan_id）或自定义金额（amount_cents，0 < 金额 ≤
  MAX_RECHARGE，积分 = 金额 ÷ credit_yuan_rate，精确到 0.01）。充值积分永久有效。
- subscribe：仅无有效订阅的用户可下单订阅档。
- renew：仅有有效订阅的用户可续订当前档位（积分下一周期生效）。
- upgrade：目标订阅档 level 必须高于当前档（付新档当前售价全额，不支持降级）。

关键：handle_callback 幂等——已 paid 的订单直接返回成功，不重复发积分。
fulfill_order 在同一事务内完成：order.status=paid + 按 kind 发放。
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import grant_credits
from ..billing.subscription import (
    fulfill_renew,
    fulfill_subscribe,
    fulfill_upgrade,
    get_active_subscription,
)
from ..config import get_settings
from ..db.models import (
    CreditTxType,
    Order,
    OrderKind,
    OrderStatus,
    PayChannel,
    Plan,
    PlanKind,
)
from .provider import PaymentProvider
from .xunhupay import XunHuPayProvider

logger = logging.getLogger(__name__)

_provider: PaymentProvider | None = None

CENT = Decimal("0.01")


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


def custom_amount_to_credits(amount_cents: int) -> Decimal:
    """自定义充值金额（分）→ 积分。严格按汇率换算，精确到 0.01。"""
    rate = Decimal(get_settings().credit_yuan_rate)
    yuan = Decimal(amount_cents) / 100
    return (yuan / rate).quantize(CENT, rounding=ROUND_HALF_UP)


def max_recharge_cents() -> int:
    return get_settings().max_recharge_yuan * 100


async def create_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str,
    plan_id: uuid.UUID | None = None,
    amount_cents: int | None = None,
    pay_channel: str = PayChannel.ALIPAY.value,
) -> tuple[Order, str]:
    """统一下单入口。按 kind 校验规则并生成订单。返回 (Order, 支付链接)。"""
    if pay_channel not in (PayChannel.ALIPAY.value, PayChannel.WECHAT.value):
        raise ValueError("支付渠道不支持，请选择支付宝或微信支付")

    plan: Plan | None = None
    subject = ""
    credits: Decimal
    price_cents: int

    if kind == OrderKind.RECHARGE.value:
        if plan_id is not None:
            plan = await db.get(Plan, plan_id)
            if not plan or not plan.is_active or plan.kind != PlanKind.RECHARGE.value:
                raise ValueError("充值套餐不存在或已下架")
            credits = Decimal(plan.credits)
            price_cents = plan.price_cents
            subject = f"SearchPipe 充值 {plan.name}"
        else:
            if amount_cents is None or amount_cents <= 0:
                raise ValueError("请输入充值金额")
            if amount_cents > max_recharge_cents():
                raise ValueError(
                    f"单笔充值金额不能超过 ¥{get_settings().max_recharge_yuan}"
                )
            credits = custom_amount_to_credits(amount_cents)
            price_cents = amount_cents
            subject = f"SearchPipe 充值 ¥{amount_cents / 100:.2f}"

    elif kind in (
        OrderKind.SUBSCRIBE.value,
        OrderKind.RENEW.value,
        OrderKind.UPGRADE.value,
    ):
        if plan_id is None:
            raise ValueError("请选择订阅套餐")
        plan = await db.get(Plan, plan_id)
        if not plan or not plan.is_active or plan.kind != PlanKind.SUBSCRIPTION.value:
            raise ValueError("订阅套餐不存在或已下架")

        sub = await get_active_subscription(db, user_id)
        if kind == OrderKind.SUBSCRIBE.value:
            if sub is not None:
                raise ValueError("您已有有效订阅，请使用续订或升级")
            subject = f"SearchPipe 订阅 {plan.name}"
        else:
            if sub is None:
                raise ValueError("您当前没有有效订阅，请先订阅")
            current_plan = await db.get(Plan, sub.plan_id)
            if kind == OrderKind.RENEW.value:
                if plan.id != sub.plan_id:
                    raise ValueError("续订请选择当前档位套餐；变更档位请使用升级")
                subject = f"SearchPipe 续订 {plan.name}"
            else:  # upgrade
                if (plan.level or 0) <= (current_plan.level or 0 if current_plan else 0):
                    raise ValueError("升级档位必须高于当前档位（不支持降级）")
                subject = f"SearchPipe 升级 {plan.name}"
        credits = Decimal(plan.credits)
        price_cents = plan.price_cents  # 付当前实际售价全额

    else:
        raise ValueError(f"不支持的订单类型: {kind}")

    order_no = _gen_order_no()
    order = Order(
        user_id=user_id,
        plan_id=plan.id if plan else None,
        kind=kind,
        pay_channel=pay_channel,
        credits=credits,
        amount_cents=price_cents,
        status=OrderStatus.PENDING.value,
        provider=get_provider().name,
        provider_order_id=order_no,
    )
    db.add(order)
    await db.flush()

    pay_url = await get_provider().create_order(
        order_no=order_no,
        amount_cents=price_cents,
        subject=subject,
        pay_channel=pay_channel,
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
    """订单支付成功发放逻辑（同事务）：标 paid + 按 kind 发积分。

    幂等：调用前应检查 status；此处也做二次保护。
    """
    if order.status == OrderStatus.PAID.value:
        return  # 已支付，幂等
    order.status = OrderStatus.PAID.value
    order.paid_at = datetime.now(timezone.utc)
    await db.flush()

    if order.kind == OrderKind.RECHARGE.value:
        # 充值积分永久有效
        await grant_credits(
            db,
            user_id=order.user_id,
            amount=Decimal(order.credits),
            tx_type=CreditTxType.RECHARGE.value,
            remark=f"充值订单 {order.provider_order_id}",
            lot_source="recharge",
            lot_order_id=order.id,
        )
    else:
        plan = await db.get(Plan, order.plan_id) if order.plan_id else None
        if not plan:
            raise RuntimeError(f"订单 {order.id} 关联套餐不存在")
        if order.kind == OrderKind.SUBSCRIBE.value:
            await fulfill_subscribe(db, order, plan)
        elif order.kind == OrderKind.RENEW.value:
            sub = await get_active_subscription(db, order.user_id)
            if sub is None:
                # 支付回调到达时订阅刚好过期：按新订阅处理，保证积分不丢
                await fulfill_subscribe(db, order, plan)
            else:
                await fulfill_renew(db, order, plan, sub)
        elif order.kind == OrderKind.UPGRADE.value:
            sub = await get_active_subscription(db, order.user_id)
            if sub is None:
                await fulfill_subscribe(db, order, plan)
            else:
                await fulfill_upgrade(db, order, plan, sub)
        else:
            raise RuntimeError(f"未知订单类型: {order.kind}")
    logger.info("订单完成发放 order=%s kind=%s credits=%s", order.id, order.kind, order.credits)


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
