"""支付路由 —— /payments/* 。

- GET  /payments/packages    充值套餐列表（同 /billing/plans，此为语义别名）
- POST /payments/orders      选套餐 → 创建订单 → 返回支付链接
- POST /payments/callback    虎皮椒异步回调（验签 + 幂等 + 发积分）
- GET  /payments/orders/{id} 查订单状态（前端轮询）
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import Order, Plan, User
from ..db.session import get_db
from .service import create_order_from_plan, get_order, get_provider, handle_callback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


class PackageItem(BaseModel):
    id: str
    name: str
    credits: int
    price_yuan: float


class CreateOrderRequest(BaseModel):
    plan_id: str


class CreateOrderResponse(BaseModel):
    order_id: str
    pay_url: str
    amount_cents: int
    credits: int
    status: str


class OrderStatusResponse(BaseModel):
    order_id: str
    status: str
    credits: int
    amount_cents: int
    paid_at: datetime | None
    created_at: datetime


@router.get("/packages", response_model=list[PackageItem])
async def packages(db: AsyncSession = Depends(get_db)) -> list[PackageItem]:
    stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PackageItem(
            id=str(p.id),
            name=p.name,
            credits=p.credits,
            price_yuan=p.price_cents / 100,
        )
        for p in rows
    ]


@router.post("/orders", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    req: CreateOrderRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreateOrderResponse:
    try:
        order, pay_url = await create_order_from_plan(db, user.id, req.plan_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    await db.commit()
    return CreateOrderResponse(
        order_id=str(order.id),
        pay_url=pay_url,
        amount_cents=order.amount_cents,
        credits=order.credits,
        status=order.status,
    )


@router.post("/callback", response_class=PlainTextResponse)
async def callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> str:
    """虎皮椒异步回调。成功返回 "success"（虎皮椒要求）。"""
    # 虎皮椒可能 GET 或 POST 回调，参数兼容
    if request.method == "POST":
        try:
            params = dict(await request.form())
        except Exception:  # noqa: BLE001
            params = dict(request.query_params)
    else:
        params = dict(request.query_params)

    try:
        ok = await handle_callback(db, params)
    except Exception as e:  # noqa: BLE001
        logger.exception("支付回调处理异常: %s", e)
        ok = False

    if ok:
        await db.commit()
        return "success"
    await db.rollback()
    return "fail"


@router.get("/orders/{order_id}", response_model=OrderStatusResponse)
async def order_status(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrderStatusResponse:
    order = await get_order(db, user.id, order_id)
    if not order:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订单不存在")
    return OrderStatusResponse(
        order_id=str(order.id),
        status=order.status,
        credits=order.credits,
        amount_cents=order.amount_cents,
        paid_at=order.paid_at,
        created_at=order.created_at,
    )
