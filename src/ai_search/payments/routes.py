"""支付路由 —— /payments/* 。

- GET  /payments/catalog                购买目录：充值档 + 订阅档 + 自定义汇率/上限 + 当前订阅状态
- POST /payments/orders                 下单（kind=recharge/subscribe/upgrade）→ 返回托管收银台 URL
- POST /payments/webhooks/{provider}    平台 webhook（Creem/Dodo：raw body + 签名校验 + 幂等发积分）
- GET  /payments/orders/{id}            查订单状态（前端轮询）
- GET  /payments/packages               充值套餐列表（兼容旧前端，语义同 /billing/plans）

国内版虎皮椒回调 /payments/callback 已随 archive/china-2026-09 封存删除。
"""

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.core import (
    API_KEY_PREFIX,
    EmailNotVerifiedError,
    require_email_verified,
    resolve_jwt,
)
from ..auth.dependencies import get_current_user
from ..auth.errors import AuthError
from ..auth.session import read_session_cookie
from ..billing.subscription import get_active_subscription
from ..config import get_settings
from ..db.models import Order, OrderKind, Plan, PlanKind, User
from ..db.session import get_db
from .provider import WebhookVerificationError
from .service import create_order, get_order, get_provider, handle_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


class PackageItem(BaseModel):
    id: str
    name: str
    credits: int
    price: float  # 美元（price_cents / 100）


class CreateOrderRequest(BaseModel):
    kind: str = OrderKind.RECHARGE.value  # recharge/subscribe/renew/upgrade
    plan_id: str | None = None
    amount_cents: int | None = None       # 自定义充值金额（美分）
    pay_channel: str | None = None        # card/paypal；留空用支付方首个已声明渠道


class CreateOrderResponse(BaseModel):
    order_id: str
    pay_url: str
    amount_cents: int
    credits: str  # NUMERIC(20,2) 序列化为字符串，保精度
    status: str
    pay_channel: str  # 实际渠道（请求留空时为服务端选中的默认渠道）


class OrderStatusResponse(BaseModel):
    order_id: str
    status: str
    credits: str
    amount_cents: int
    kind: str
    pay_channel: str | None
    paid_at: datetime | None
    created_at: datetime


class PlanInfo(BaseModel):
    id: str
    name: str
    kind: str
    level: int | None
    credits: int
    price_cents: int
    price: float                    # 现价（美元）
    original_price: float | None    # 标价（划线价）；限时折扣期高于 price
    period: str | None


class SubscriptionInfo(BaseModel):
    plan_id: str
    plan_name: str
    level: int | None
    period_start: datetime
    period_end: datetime
    status: str = "active"


class CatalogResponse(BaseModel):
    recharge_plans: list[PlanInfo]
    subscription_plans: list[PlanInfo]
    credit_price_rate: str       # $0.005 = 1 积分
    max_recharge_amount: int     # 自定义充值上限（美元）
    currency: str                # 全站结算货币，如 "USD"
    pay_channels: list[str]      # 已声明渠道，如 ["card", "paypal"]
    subscription: SubscriptionInfo | None  # 当前有效订阅（未登录/无订阅为 None）


def _plan_info(p: Plan) -> PlanInfo:
    return PlanInfo(
        id=str(p.id),
        name=p.name,
        kind=p.kind,
        level=p.level,
        credits=p.credits,
        price_cents=p.price_cents,
        price=p.price_cents / 100,
        original_price=(
            p.original_price_cents / 100 if p.original_price_cents else None
        ),
        period=p.period,
    )


async def _optional_user(request: Request, db: AsyncSession) -> User | None:
    """可选登录态识别（catalog 公开访问）：JWT → session cookie，失败返回 None。"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    if token and not token.startswith(API_KEY_PREFIX):
        try:
            return (await resolve_jwt(token, db)).user
        except AuthError:
            return None
    user_id = read_session_cookie(request)
    if user_id:
        user = await db.get(User, user_id)
        if user and user.status == "active":
            return user
    return None


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CatalogResponse:
    """购买目录。登录用户附带当前订阅状态（供前端决定显示订阅/管理入口）。"""
    stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents)
    rows = (await db.execute(stmt)).scalars().all()
    recharge = [_plan_info(p) for p in rows if p.kind == PlanKind.RECHARGE.value]
    subs = sorted(
        (_plan_info(p) for p in rows if p.kind == PlanKind.SUBSCRIPTION.value),
        key=lambda p: p.level or 0,
    )

    subscription: SubscriptionInfo | None = None
    user = await _optional_user(request, db)
    if user:
        sub = await get_active_subscription(db, user.id)
        if sub:
            plan = await db.get(Plan, sub.plan_id)
            subscription = SubscriptionInfo(
                plan_id=str(sub.plan_id),
                plan_name=plan.name if plan else "",
                level=plan.level if plan else None,
                period_start=sub.current_period_start,
                period_end=sub.current_period_end,
                status=sub.status,
            )

    s = get_settings()
    return CatalogResponse(
        recharge_plans=recharge,
        subscription_plans=subs,
        credit_price_rate=s.credit_price_rate,
        max_recharge_amount=s.max_recharge_usd,
        currency=s.currency,
        pay_channels=get_provider().available_channels(),
        subscription=subscription,
    )


@router.get("/packages", response_model=list[PackageItem])
async def packages(db: AsyncSession = Depends(get_db)) -> list[PackageItem]:
    """充值套餐列表（兼容旧接口）。"""
    stmt = (
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.kind == PlanKind.RECHARGE.value)
        .order_by(Plan.price_cents)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PackageItem(
            id=str(p.id),
            name=p.name,
            credits=p.credits,
            price=p.price_cents / 100,
        )
        for p in rows
    ]


@router.post("/orders", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order_route(
    req: CreateOrderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreateOrderResponse:
    # 邮箱未验证不允许下单（避免财务纠纷；admin/owner 豁免）
    try:
        require_email_verified(user)
    except EmailNotVerifiedError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    try:
        order, pay_url = await create_order(
            db,
            user.id,
            kind=req.kind,
            plan_id=req.plan_id,
            amount_cents=req.amount_cents,
            pay_channel=req.pay_channel,
            customer_email=user.email,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    await db.commit()
    return CreateOrderResponse(
        order_id=str(order.id),
        pay_url=pay_url,
        amount_cents=order.amount_cents,
        credits=str(Decimal(order.credits).quantize(Decimal("0.01"))),
        status=order.status,
        pay_channel=order.pay_channel,
    )


@router.post("/webhooks/{provider}")
async def payment_webhook(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """支付平台 webhook（Creem/Dodo）。验签失败 400；暂时性处理失败 500（平台会重试）。"""
    if provider not in ("creem", "dodo"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown payment provider")
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        ok = await handle_webhook(db, provider, headers=headers, raw_body=raw_body)
    except WebhookVerificationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("webhook 处理异常: %s", e)
        await db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Webhook processing failed") from e

    if ok:
        await db.commit()
        return {"received": True}
    await db.rollback()
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Webhook not processable yet")


@router.get("/portal")
async def customer_portal(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """生成当前用户订阅的 Customer Portal 链接（取消/改档/换支付方式）。"""
    from ..db.models import Subscription
    from .service import get_provider_by_name

    stmt = select(Subscription).where(
        Subscription.user_id == user.id, Subscription.status == "active"
    )
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if not sub or not sub.provider_customer_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active subscription found")
    provider = get_provider_by_name(sub.provider) if sub.provider else get_provider()
    url = await provider.customer_portal_url(sub.provider_customer_id)
    if not url:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Failed to generate customer portal link"
        )
    return {"portal_url": url}


@router.get("/orders/{order_id}", response_model=OrderStatusResponse)
async def order_status(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderStatusResponse:
    order = await get_order(db, user.id, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return OrderStatusResponse(
        order_id=str(order.id),
        status=order.status,
        credits=str(Decimal(order.credits).quantize(Decimal("0.01"))),
        amount_cents=order.amount_cents,
        kind=order.kind,
        pay_channel=order.pay_channel,
        paid_at=order.paid_at,
        created_at=order.created_at,
    )
