"""计费路由 —— /billing/* 。

- GET /billing/balance        当前余额
- GET /billing/transactions   分页流水
- GET /billing/plans          充值套餐列表
全部 Depends(get_current_user)。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import CreditTransaction, Plan, User
from ..db.session import get_db
from .service import get_balance, list_transactions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class BalanceResponse(BaseModel):
    balance: int
    free_tier_credits: int
    unlimited: bool = False  # admin/owner 免扣费


class TxItem(BaseModel):
    id: int
    delta: int
    type: str
    balance_after: int
    remark: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class TxListResponse(BaseModel):
    items: list[TxItem]
    total: int
    page: int
    size: int


class PlanItem(BaseModel):
    id: str
    name: str
    credits: int
    price_cents: int
    price_yuan: float
    period: str | None

    class Config:
        from_attributes = True


@router.get("/balance", response_model=BalanceResponse)
async def balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    from ..config import get_settings

    return BalanceResponse(
        balance=await get_balance(db, user.id),
        free_tier_credits=get_settings().free_tier_credits,
        unlimited=user.role in ("owner", "admin"),
    )


@router.get("/transactions", response_model=TxListResponse)
async def transactions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TxListResponse:
    items, total = await list_transactions(db, user.id, page=page, size=size)
    return TxListResponse(
        items=[TxItem.model_validate(it) for it in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/plans", response_model=list[PlanItem])
async def plans(db: AsyncSession = Depends(get_db)) -> list[PlanItem]:
    """上架的充值套餐列表。"""
    stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PlanItem(
            id=str(p.id),
            name=p.name,
            credits=p.credits,
            price_cents=p.price_cents,
            price_yuan=p.price_cents / 100,
            period=p.period,
        )
        for p in rows
    ]
