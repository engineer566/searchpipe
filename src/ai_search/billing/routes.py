"""计费路由 —— /billing/* 。

- GET /billing/balance        当前余额（含永久/限时明细与最近到期时间）
- GET /billing/transactions   分页流水
- GET /billing/plans          套餐列表（充值档 + 订阅档）
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
from .service import get_balance_detail, list_transactions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class BalanceResponse(BaseModel):
    balance: float           # 总余额（2 位小数，含未生效的续订积分）
    permanent: float         # 永久积分（充值/赠送，不过期）
    expiring: float          # 限时积分（订阅，30 天有效）
    upcoming: float = 0.0    # 已入账未生效（续订排队，下一周期起可用）
    next_expiry: datetime | None  # 最近一笔限时积分到期时间
    free_tier_credits: int
    unlimited: bool = False  # admin/owner 免扣费


class TxItem(BaseModel):
    id: int
    delta: float
    type: str
    balance_after: float
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
    kind: str
    level: int | None
    credits: int
    price_cents: int
    price: float
    original_price: float | None
    period: str | None


@router.get("/balance", response_model=BalanceResponse)
async def balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BalanceResponse:
    from ..config import get_settings

    detail = await get_balance_detail(db, user.id)
    return BalanceResponse(
        balance=float(detail["balance"]),
        permanent=float(detail["permanent"]),
        expiring=float(detail["expiring"]),
        upcoming=float(detail["upcoming"]),
        next_expiry=detail["next_expiry"],
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
    """上架套餐列表（充值档 + 订阅档）。完整购买目录见 /payments/catalog。"""
    stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_cents)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        PlanItem(
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
        for p in rows
    ]
