"""管理后台路由 —— /admin/* 。

全部 Depends(get_current_admin)（role ∈ {owner, admin}）。
- GET   /admin/users            用户列表
- PATCH /admin/users/{id}       改 role/status
- GET   /admin/credits          全平台积分概览
- POST  /admin/credits/grant    手动发放额度
- GET   /admin/orders           订单总览
- GET   /admin/stats            概览统计
- GET   /admin/feedback         工单列表（可按 status 过滤）
- POST  /admin/feedback/{id}/close 关闭工单
- GET   /admin/monitor          运营监控（SSR 页面）
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from ..auth.dependencies import get_current_admin
from ..billing.service import grant_credits
from ..db.models import CreditAccount, CreditTransaction, FeedbackTicket, Order, OrderStatus, TicketStatus, UsageLog, User
from ..db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "dashboard" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------- Schemas ----------


class AdminUserItem(BaseModel):
    id: str
    email: str | None
    phone: str | None
    role: str
    status: str
    created_at: datetime
    last_login_at: datetime | None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem]
    total: int
    page: int
    size: int


class UpdateUserRequest(BaseModel):
    role: str | None = None  # owner/admin/member/user
    status: str | None = None  # active/suspended


class GrantCreditsRequest(BaseModel):
    user_id: str
    amount: int
    remark: str | None = "管理员发放"


class GrantCreditsResponse(BaseModel):
    user_id: str
    balance: int


class AdminOrderItem(BaseModel):
    id: str
    user_id: str
    credits: int
    amount_cents: int
    status: str
    provider: str
    provider_order_id: str | None
    paid_at: datetime | None
    created_at: datetime


class AdminStatsResponse(BaseModel):
    user_count: int
    active_user_count: int
    total_balance: int
    total_searches: int
    total_revenue_cents: int
    paid_order_count: int


class AdminFeedbackItem(BaseModel):
    id: str
    user_id: str
    category: str
    subject: str
    content: str
    status: str
    created_at: datetime
    updated_at: datetime


class AdminFeedbackListResponse(BaseModel):
    items: list[AdminFeedbackItem]
    total: int
    page: int
    size: int


# ---------- 用户管理 ----------


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    stmt = (
        select(User)
        .order_by(User.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    users = (await db.execute(stmt)).scalars().all()
    return AdminUserListResponse(
        items=[
            AdminUserItem(
                id=str(u.id),
                email=u.email,
                phone=u.phone,
                role=u.role,
                status=u.status,
                created_at=u.created_at,
                last_login_at=u.last_login_at,
            )
            for u in users
        ],
        total=total,
        page=page,
        size=size,
    )


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if req.role is not None:
        if req.role not in ("owner", "admin", "member", "user"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法 role")
        user.role = req.role
    if req.status is not None:
        if req.status not in ("active", "suspended"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法 status")
        user.status = req.status
    await db.commit()
    return {"msg": "已更新", "role": user.role, "status": user.status}


# ---------- 积分管理 ----------


@router.get("/credits")
async def credits_overview(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """全平台积分总余额 + 账户数。"""
    total = (
        await db.execute(select(func.coalesce(func.sum(CreditAccount.balance), 0)))
    ).scalar_one()
    count = (await db.execute(select(func.count()).select_from(CreditAccount))).scalar_one()
    return {"total_balance": int(total), "account_count": count}


@router.post("/credits/grant", response_model=GrantCreditsResponse)
async def grant_credits_admin(
    req: GrantCreditsRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> GrantCreditsResponse:
    """管理员手动发放额度。"""
    if req.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "发放金额必须为正")
    user = await db.get(User, req.user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    balance = await grant_credits(
        db, user_id=user.id, amount=req.amount, tx_type="grant", remark=req.remark
    )
    await db.commit()
    return GrantCreditsResponse(user_id=str(user.id), balance=balance)


# ---------- 订单 ----------


@router.get("/orders", response_model=list[AdminOrderItem])
async def list_orders(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminOrderItem]:
    stmt = (
        select(Order)
        .order_by(Order.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    orders = (await db.execute(stmt)).scalars().all()
    return [
        AdminOrderItem(
            id=str(o.id),
            user_id=str(o.user_id),
            credits=o.credits,
            amount_cents=o.amount_cents,
            status=o.status,
            provider=o.provider,
            provider_order_id=o.provider_order_id,
            paid_at=o.paid_at,
            created_at=o.created_at,
        )
        for o in orders
    ]


# ---------- 统计 ----------


@router.get("/stats", response_model=AdminStatsResponse)
async def stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminStatsResponse:
    """概览统计：用户数、总积分、总搜索、收入。"""
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    active_user_count = (
        await db.execute(select(func.count()).select_from(User).where(User.status == "active"))
    ).scalar_one()
    total_balance = (
        await db.execute(select(func.coalesce(func.sum(CreditAccount.balance), 0)))
    ).scalar_one()
    total_searches = (
        await db.execute(select(func.count()).select_from(UsageLog))
    ).scalar_one()
    paid_orders = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.amount_cents), 0))
            .where(Order.status == OrderStatus.PAID.value)
        )
    ).one()
    return AdminStatsResponse(
        user_count=user_count,
        active_user_count=active_user_count,
        total_balance=int(total_balance),
        total_searches=total_searches,
        total_revenue_cents=int(paid_orders[1]),
        paid_order_count=paid_orders[0],
    )


# ---------- 工单管理 ----------


@router.get("/feedback", response_model=AdminFeedbackListResponse)
async def list_feedback(
    ticket_status: str | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminFeedbackListResponse:
    """列全部工单，可按 status 过滤（open/closed）。"""
    if ticket_status is not None and ticket_status not in ("open", "closed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status 必须是 open 或 closed")

    base_stmt = select(FeedbackTicket)
    if ticket_status:
        base_stmt = base_stmt.where(FeedbackTicket.status == ticket_status)

    total = (
        await db.execute(select(func.count()).select_from(base_stmt.subquery()))
    ).scalar_one()

    stmt = (
        base_stmt.order_by(desc(FeedbackTicket.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return AdminFeedbackListResponse(
        items=[
            AdminFeedbackItem(
                id=str(r.id),
                user_id=str(r.user_id),
                category=r.category,
                subject=r.subject,
                content=r.content,
                status=r.status,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )


@router.post("/feedback/{ticket_id}/close")
async def close_feedback(
    ticket_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """关闭工单。"""
    ticket = await db.get(FeedbackTicket, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "工单不存在")
    if ticket.status == TicketStatus.CLOSED.value:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "工单已关闭")
    ticket.status = TicketStatus.CLOSED.value
    await db.commit()
    return {"msg": "工单已关闭", "id": ticket_id}


# ---------- 监控 ----------


@router.get("/monitor")
async def monitor_page(
    request: Request,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> object:
    """运营监控 SSR 页面：聚合关键指标与近 24h 趋势。"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ago = now - timedelta(hours=24)

    # 用户指标
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    users_today = (
        await db.execute(
            select(func.count()).select_from(User).where(User.created_at >= today_start)
        )
    ).scalar_one()
    active_24h = (
        await db.execute(
            select(func.count(func.distinct(UsageLog.user_id))).where(UsageLog.created_at >= day_ago)
        )
    ).scalar_one()

    # 请求指标（今日 + 近 24h 按小时）
    searches_today = (
        await db.execute(
            select(func.count()).select_from(UsageLog).where(UsageLog.created_at >= today_start)
        )
    ).scalar_one()
    searches_ok_today = (
        await db.execute(
            select(func.count())
            .select_from(UsageLog)
            .where(UsageLog.created_at >= today_start, UsageLog.status == "ok")
        )
    ).scalar_one()
    searches_err_today = searches_today - searches_ok_today

    # 近 24h 按小时趋势
    hour_trunc = func.date_trunc("hour", UsageLog.created_at).label("bucket")
    hourly_rows = (
        await db.execute(
            select(
                hour_trunc,
                func.count().label("cnt"),
            )
            .where(UsageLog.created_at >= day_ago)
            .group_by(hour_trunc)
            .order_by(hour_trunc)
        )
    ).all()
    hourly = [{"bucket": r.bucket.isoformat() if r.bucket else None, "count": r.cnt} for r in hourly_rows]

    # 积分指标
    credits_consumed_today = (
        await db.execute(
            select(func.coalesce(func.sum(UsageLog.credits_consumed), 0))
            .where(UsageLog.created_at >= today_start)
        )
    ).scalar_one()
    orders_today = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.amount_cents), 0))
            .where(Order.created_at >= today_start, Order.status == OrderStatus.PAID.value)
        )
    ).one()

    # 系统：最近用量日志 / 错误
    recent_logs_stmt = (
        select(UsageLog)
        .order_by(UsageLog.created_at.desc())
        .limit(20)
    )
    recent_logs = (await db.execute(recent_logs_stmt)).scalars().all()

    recent_errors_stmt = (
        select(UsageLog)
        .where(UsageLog.status == "error")
        .order_by(UsageLog.created_at.desc())
        .limit(10)
    )
    recent_errors = (await db.execute(recent_errors_stmt)).scalars().all()

    return templates.TemplateResponse(
        request,
        "admin_monitor.html",
        {
            "user": admin,
            "active": "monitor",
            "metrics": {
                "user_count": user_count,
                "users_today": users_today,
                "active_24h": active_24h,
                "searches_today": searches_today,
                "searches_ok_today": searches_ok_today,
                "searches_err_today": searches_err_today,
                "credits_consumed_today": int(credits_consumed_today),
                "orders_today_count": orders_today[0],
                "orders_today_amount_cents": int(orders_today[1]),
                "hourly": hourly,
            },
            "recent_logs": recent_logs,
            "recent_errors": recent_errors,
            "now": now,
        },
    )
