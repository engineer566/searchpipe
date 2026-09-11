"""支付订单服务 —— 下单（充值/订阅/升级）+ webhook 事件驱动到账。

下单规则：
- recharge：固定充值档（plan_id）或自定义金额（amount_cents，0 < 金额 ≤
  MAX_RECHARGE_USD，积分 = 金额 ÷ credit_price_rate，精确到 0.01）。充值积分永久有效。
- subscribe：仅无有效订阅的用户可下单订阅档；创建托管收银台会话，
  周期扣款由平台托管（原生自动续订），续期由 webhook 事件驱动。
- upgrade：目标订阅档 level 必须高于当前档（付新档当前售价全额，不支持降级）。

webhook 到账（handle_webhook，按归一化事件类型分发）：
- one_time_paid：充值订单发积分（幂等：已 paid 直接成功）。
- subscription_checkout（Creem）：绑定本地订阅（order 保持 pending，
  积分等 subscription_paid 发放）。
- subscription_activated（Dodo）：绑订阅 + fulfill_subscribe 发首期积分。
- subscription_paid：首期（存在 pending subscribe 订单 → fulfill）或续期
  （以 event_id 建 renew 订单，天然幂等）→ fulfill_subscription_payment。
- subscription_canceled：本地订阅标 canceled（已发积分不回收，当期到期自然失效）。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing.service import grant_credits
from ..billing.subscription import (
    SUBSCRIPTION_PERIOD,
    fulfill_renew,
    fulfill_subscribe,
    fulfill_subscription_payment,
    fulfill_upgrade,
    get_active_subscription,
)
from ..config import get_settings
from ..db.models import (
    CreditTxType,
    Order,
    OrderKind,
    OrderStatus,
    Plan,
    PlanKind,
    Subscription,
    SubscriptionStatus,
)
from .provider import PaymentEvent, PaymentProvider

logger = logging.getLogger(__name__)

_provider: PaymentProvider | None = None

CENT = Decimal("0.01")


def get_provider() -> PaymentProvider:
    """按配置返回支付提供方单例。"""
    global _provider
    if _provider is None:
        s = get_settings()
        if s.payment_provider == "creem":
            from .creem import CreemProvider

            _provider = CreemProvider()
        elif s.payment_provider == "dodo":
            from .dodo import DodoProvider

            _provider = DodoProvider()
        else:
            raise RuntimeError(f"未知支付提供方: {s.payment_provider}")
    return _provider


def get_provider_by_name(name: str) -> PaymentProvider:
    """webhook 路由按路径里的 provider 名取实例（与当前激活配置无关）。

    避免切换 PAYMENT_PROVIDER 期间旧平台的在途 webhook 无法验签。
    """
    if name == "creem":
        from .creem import CreemProvider

        return CreemProvider()
    if name == "dodo":
        from .dodo import DodoProvider

        return DodoProvider()
    raise RuntimeError(f"未知支付提供方: {name}")


def _gen_order_no() -> str:
    """生成业务订单号（支付方要求唯一）。"""
    return f"ORD{uuid.uuid4().hex[:20].upper()}"


def custom_amount_to_credits(amount_cents: int) -> Decimal:
    """自定义充值金额（分）→ 积分。严格按汇率换算，精确到 0.01。"""
    rate = Decimal(get_settings().credit_price_rate)
    dollars = Decimal(amount_cents) / 100
    return (dollars / rate).quantize(CENT, rounding=ROUND_HALF_UP)


def max_recharge_cents() -> int:
    return get_settings().max_recharge_usd * 100


async def create_order(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str,
    plan_id: uuid.UUID | None = None,
    amount_cents: int | None = None,
    pay_channel: str | None = None,
    customer_email: str | None = None,
) -> tuple[Order, str]:
    """统一下单入口。按 kind 校验规则并生成订单。返回 (Order, 收银台 URL)。

    pay_channel 留空时用支付方首个已声明渠道；托管收银台实际可用渠道由平台决定。
    """
    provider = get_provider()
    channels = provider.available_channels()
    if pay_channel is None:
        if not channels:
            raise ValueError("Payments are not available yet, please contact support")
        pay_channel = channels[0]
    elif channels and pay_channel not in channels:
        avail = ", ".join(channels)
        raise ValueError(f"Unsupported payment channel, please use: {avail}")

    plan: Plan | None = None
    subject = ""
    credits: Decimal
    price_cents: int

    if kind == OrderKind.RECHARGE.value:
        if plan_id is not None:
            plan = await db.get(Plan, plan_id)
            if not plan or not plan.is_active or plan.kind != PlanKind.RECHARGE.value:
                raise ValueError("Recharge plan not found or no longer available")
            credits = Decimal(plan.credits)
            price_cents = plan.price_cents
            subject = f"SearchPipe {plan.name}"
        else:
            if amount_cents is None or amount_cents <= 0:
                raise ValueError("Please enter a recharge amount")
            if amount_cents > max_recharge_cents():
                raise ValueError(
                    f"Single recharge cannot exceed ${get_settings().max_recharge_usd}"
                )
            credits = custom_amount_to_credits(amount_cents)
            price_cents = amount_cents
            subject = f"SearchPipe credits recharge ${amount_cents / 100:.2f}"

    elif kind in (
        OrderKind.SUBSCRIBE.value,
        OrderKind.RENEW.value,
        OrderKind.UPGRADE.value,
    ):
        if plan_id is None:
            raise ValueError("Please choose a subscription plan")
        plan = await db.get(Plan, plan_id)
        if not plan or not plan.is_active or plan.kind != PlanKind.SUBSCRIPTION.value:
            raise ValueError("Subscription plan not found or no longer available")

        sub = await get_active_subscription(db, user_id)
        if kind == OrderKind.SUBSCRIBE.value:
            if sub is not None:
                raise ValueError(
                    "You already have an active subscription; manage it from the billing portal"
                )
            subject = f"SearchPipe subscription {plan.name}"
        else:
            if sub is None:
                raise ValueError("No active subscription; please subscribe first")
            current_plan = await db.get(Plan, sub.plan_id)
            if kind == OrderKind.RENEW.value:
                if plan.id != sub.plan_id:
                    raise ValueError("Renewal must use your current plan")
                subject = f"SearchPipe renewal {plan.name}"
            else:  # upgrade
                if (plan.level or 0) <= (current_plan.level or 0 if current_plan else 0):
                    raise ValueError("Upgrade plan must be higher than your current plan")
                subject = f"SearchPipe upgrade {plan.name}"
        credits = Decimal(plan.credits)
        price_cents = plan.price_cents  # 付当前实际售价全额

    else:
        raise ValueError(f"Unsupported order kind: {kind}")

    order_no = _gen_order_no()
    order = Order(
        user_id=user_id,
        plan_id=plan.id if plan else None,
        kind=kind,
        pay_channel=pay_channel,
        credits=credits,
        amount_cents=price_cents,
        status=OrderStatus.PENDING.value,
        provider=provider.name,
        provider_order_id=order_no,
    )
    db.add(order)
    await db.flush()

    s = get_settings()
    pay_url = await provider.create_checkout(
        order_no=order_no,
        kind=kind,
        amount_cents=price_cents,
        subject=subject,
        plan=plan,
        customer_email=customer_email,
        success_url=f"{s.app_base_url.rstrip('/')}/dashboard/billing?payment=success",
    )
    return order, pay_url


async def get_order(
    db: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID
) -> Order | None:
    order = await db.get(Order, order_id)
    if order and order.user_id != user_id:
        return None
    return order


async def fulfill_order(
    db: AsyncSession,
    order: Order,
    *,
    sub: Subscription | None = None,
    provider_subscription_id: str | None = None,
    provider_customer_id: str | None = None,
) -> None:
    """订单支付成功发放逻辑（同事务）：标 paid + 按 kind 发放。

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
            remark=f"Recharge order {order.provider_order_id}",
            lot_source="recharge",
            lot_order_id=order.id,
        )
    else:
        plan = await db.get(Plan, order.plan_id) if order.plan_id else None
        if not plan:
            raise RuntimeError(f"订单 {order.id} 关联套餐不存在")
        if order.kind == OrderKind.SUBSCRIBE.value:
            await fulfill_subscribe(
                db,
                order,
                plan,
                sub=sub,
                provider=order.provider,
                provider_subscription_id=provider_subscription_id,
                provider_customer_id=provider_customer_id,
            )
        elif order.kind == OrderKind.RENEW.value:
            sub = sub or await get_active_subscription(db, order.user_id)
            if sub is None:
                # 支付回调到达时订阅刚好过期：按新订阅处理，保证积分不丢
                await fulfill_subscribe(db, order, plan)
            else:
                await fulfill_renew(db, order, plan, sub)
        elif order.kind == OrderKind.UPGRADE.value:
            sub = sub or await get_active_subscription(db, order.user_id)
            if sub is None:
                await fulfill_subscribe(db, order, plan)
            else:
                await fulfill_upgrade(db, order, plan, sub)
        else:
            raise RuntimeError(f"未知订单类型: {order.kind}")
    logger.info("订单完成发放 order=%s kind=%s credits=%s", order.id, order.kind, order.credits)


async def _find_sub_by_provider(
    db: AsyncSession, provider: str, provider_subscription_id: str
) -> Subscription | None:
    stmt = select(Subscription).where(
        Subscription.provider == provider,
        Subscription.provider_subscription_id == provider_subscription_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_pending_subscribe_order(
    db: AsyncSession, user_id: uuid.UUID
) -> Order | None:
    stmt = (
        select(Order)
        .where(
            Order.user_id == user_id,
            Order.kind == OrderKind.SUBSCRIBE.value,
            Order.status == OrderStatus.PENDING.value,
        )
        .order_by(Order.created_at.desc())
    )
    return (await db.execute(stmt)).scalars().first()


async def _event_processed(db: AsyncSession, event_id: str) -> bool:
    stmt = select(Order.id).where(Order.provider_order_id == event_id)
    return (await db.execute(stmt)).first() is not None


async def handle_webhook(
    db: AsyncSession, provider_name: str, *, headers: dict, raw_body: bytes
) -> bool:
    """处理支付平台 webhook。验签 + 幂等 + 发积分。返回是否处理成功。

    返回 False → 路由回 5xx，平台会按退避重试（Creem 5 次/6h，Dodo 指数退避），
    用于「事件乱序到达」等暂时性失败；验签失败由路由直接 400（不重试）。
    """
    from .provider import WebhookVerificationError

    try:
        provider = get_provider_by_name(provider_name)
    except RuntimeError:
        logger.warning("收到未知 provider 的 webhook: %s", provider_name)
        return False
    try:
        event = provider.verify_webhook(headers=headers, raw_body=raw_body)
    except WebhookVerificationError as e:
        logger.warning("webhook 验签失败 provider=%s: %s", provider_name, e)
        raise  # 路由层转 400

    logger.info("webhook 事件 provider=%s type=%s id=%s", provider_name, event.type, event.event_id)

    if event.type == "ignored":
        return True

    if event.type == "one_time_paid":
        if not event.order_no:
            logger.warning("one_time_paid 缺少订单号: %s", event.event_id)
            return True  # 无法定位也 ack，避免无限重试
        order = await _order_by_no(db, event.order_no)
        if not order:
            logger.warning("one_time_paid 订单不存在: %s", event.order_no)
            return True
        await fulfill_order(db, order)
        return True

    if event.type == "subscription_checkout":
        # Creem：订阅收银台完成 → 绑订阅，积分等 subscription.paid
        order = await _order_by_no(db, event.order_no) if event.order_no else None
        if not order:
            logger.warning("subscription_checkout 订单不存在: %s", event.order_no)
            return True
        sub = await _find_sub_by_provider(
            db, provider_name, event.provider_subscription_id or ""
        )
        if sub is None:
            now = datetime.now(timezone.utc)
            sub = Subscription(
                user_id=order.user_id,
                plan_id=order.plan_id,
                status=SubscriptionStatus.ACTIVE.value,
                # 占位周期，subscription.paid 到达后以平台事件周期为准
                current_period_start=now,
                current_period_end=now + SUBSCRIPTION_PERIOD,
                provider=provider_name,
                provider_subscription_id=event.provider_subscription_id,
                provider_customer_id=event.provider_customer_id,
            )
            db.add(sub)
            await db.flush()
        return True

    if event.type == "subscription_activated":
        # Dodo：订阅激活 → 绑订阅 + 发首期积分（订单幂等）
        order = await _order_by_no(db, event.order_no) if event.order_no else None
        if not order:
            logger.warning("subscription_activated 订单不存在: %s", event.order_no)
            return True
        sub = await _find_sub_by_provider(
            db, provider_name, event.provider_subscription_id or ""
        )
        await fulfill_order(
            db,
            order,
            sub=sub,
            provider_subscription_id=event.provider_subscription_id,
            provider_customer_id=event.provider_customer_id,
        )
        return True

    if event.type == "subscription_paid":
        if not event.provider_subscription_id:
            logger.warning("subscription_paid 缺少订阅 id: %s", event.event_id)
            return True
        if await _event_processed(db, event.event_id):
            logger.info("subscription_paid 重复事件，幂等跳过: %s", event.event_id)
            return True
        sub = await _find_sub_by_provider(
            db, provider_name, event.provider_subscription_id
        )
        if not sub:
            # 事件乱序（subscription.paid 先于 checkout.completed 到达）→ 5xx 等平台重试
            logger.warning("subscription_paid 本地订阅不存在，等待重试: %s", event.provider_subscription_id)
            return False
        # 首期：checkout.completed 只绑了订阅、subscribe 订单还 pending → 直接 fulfill
        pending = await _find_pending_subscribe_order(db, sub.user_id)
        if pending and pending.plan_id == sub.plan_id:
            await fulfill_order(db, pending, sub=sub)
            return True
        # 续期：以 event_id 建 renew 订单（幂等键）
        plan = await db.get(Plan, sub.plan_id)
        if not plan:
            logger.error("subscription_paid 订阅套餐不存在: %s", sub.plan_id)
            return False
        renew_order = Order(
            user_id=sub.user_id,
            plan_id=sub.plan_id,
            kind=OrderKind.RENEW.value,
            pay_channel=None,
            credits=Decimal(plan.credits),
            amount_cents=event.amount_cents or plan.price_cents,
            status=OrderStatus.PAID.value,
            provider=provider_name,
            provider_order_id=event.event_id,
            paid_at=datetime.now(timezone.utc),
        )
        db.add(renew_order)
        await db.flush()
        await fulfill_subscription_payment(
            db,
            renew_order,
            plan,
            sub,
            period_start=event.period_start,
            period_end=event.period_end,
        )
        return True

    if event.type == "subscription_canceled":
        sub = await _find_sub_by_provider(
            db, provider_name, event.provider_subscription_id or ""
        )
        if sub:
            sub.status = SubscriptionStatus.CANCELED.value
            await db.flush()
            logger.info("订阅已取消 sub=%s", sub.id)
        return True

    logger.warning("未知 webhook 事件类型: %s", event.type)
    return True


async def _order_by_no(db: AsyncSession, order_no: str) -> Order | None:
    stmt = select(Order).where(Order.provider_order_id == order_no)
    return (await db.execute(stmt)).scalar_one_or_none()
