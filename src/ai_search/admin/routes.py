"""管理后台路由 —— /admin/* 。

全部 Depends(get_current_admin)（role ∈ {owner, admin}）。
- GET   /admin/users            用户列表
- PATCH /admin/users/{id}       改 role/status
- GET   /admin/credits          全平台积分概览
- POST  /admin/credits/grant    手动发放额度
- GET   /admin/orders           订单总览
- GET   /admin/stats            概览统计
- GET   /admin/feedback         工单列表（可按 status 过滤；Accept: text/html 时渲染管理页）
- POST  /admin/feedback/{id}/close 关闭工单
- GET   /admin/messages         站内信 SSR 页（发送表单 + 已发批次已读统计；支持 ?to= 邮箱 / ?ticket= 工单预填）
- POST  /admin/messages         发送站内信（定向按邮箱 / 全局广播，一行一收件人）
- GET   /admin/monitor          运营监控（SSR 页面，含注册用户列表）
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from ..auth.dependencies import get_current_admin
from ..billing.service import grant_credits
from ..db.models import (
    CreditAccount,
    CreditTransaction,
    FeedbackTicket,
    MessageKind,
    Order,
    OrderStatus,
    SiteMessage,
    TicketStatus,
    UsageLog,
    User,
    UserStatus,
)
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
    balance: float


class AdminOrderItem(BaseModel):
    id: str
    user_id: str
    kind: str
    pay_channel: str | None
    credits: float
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
            kind=o.kind,
            pay_channel=o.pay_channel,
            credits=float(o.credits),
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
    request: Request,
    ticket_status: str | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> object:
    """列全部工单，可按 status 过滤（open/closed）。

    浏览器直接访问（Accept: text/html）时渲染工单管理 SSR 页
    （含「通知该用户」入口，跳转 /admin/messages 预填收件人）；
    程序调用（fetch/API）返回 JSON。
    """
    if ticket_status is not None and ticket_status not in ("open", "closed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status 必须是 open 或 closed")

    if "text/html" in request.headers.get("accept", ""):
        stmt = (
            select(FeedbackTicket, User.email)
            .join(User, FeedbackTicket.user_id == User.id)
            .order_by(desc(FeedbackTicket.created_at))
            .limit(100)
        )
        if ticket_status:
            stmt = stmt.where(FeedbackTicket.status == ticket_status)
        rows = (await db.execute(stmt)).all()
        return templates.TemplateResponse(
            request,
            "admin_feedback.html",
            {
                "user": admin,
                "active": "feedback",
                "tickets": [{"ticket": t, "email": email} for t, email in rows],
                "filter_status": ticket_status or "",
            },
        )

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


# ---------- 站内信 ----------


async def _render_admin_messages(
    request: Request,
    admin: User,
    db: AsyncSession,
    *,
    prefill_to: str = "",
    prefill_title: str = "",
    prefill_content: str = "",
    error: str | None = None,
    sent: int | None = None,
) -> object:
    """渲染站内信管理页：发送表单 + 已发批次（含已读统计）。"""
    batch_stmt = (
        select(
            SiteMessage.batch_id,
            SiteMessage.kind,
            SiteMessage.title,
            func.count().label("total"),
            func.count(SiteMessage.read_at).label("read_count"),
            func.min(SiteMessage.created_at).label("created_at"),
        )
        .group_by(SiteMessage.batch_id, SiteMessage.kind, SiteMessage.title)
        .order_by(desc(func.min(SiteMessage.created_at)))
        .limit(50)
    )
    batches = (await db.execute(batch_stmt)).all()
    return templates.TemplateResponse(
        request,
        "admin_messages.html",
        {
            "user": admin,
            "active": "messages",
            "batches": batches,
            "prefill_to": prefill_to,
            "prefill_title": prefill_title,
            "prefill_content": prefill_content,
            "error": error,
            "sent": sent,
        },
    )


@router.get("/messages")
async def admin_messages_page(
    request: Request,
    to: str = Query(""),
    ticket: str = Query(""),
    sent: int | None = Query(None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> object:
    """站内信管理页。支持 ?to=邮箱 预填收件人、?ticket=工单ID 预填回复文案。"""
    prefill_to, prefill_title, prefill_content = to.strip(), "", ""
    if ticket:
        try:
            ticket_id = uuid.UUID(ticket)
        except ValueError:
            ticket_id = None
        ticket_obj = await db.get(FeedbackTicket, ticket_id) if ticket_id else None
        if ticket_obj:
            ticket_user = await db.get(User, ticket_obj.user_id)
            if ticket_user and ticket_user.email and not prefill_to:
                prefill_to = ticket_user.email
            prefill_title = f"回复：工单「{ticket_obj.subject}」"
            prefill_content = (
                f"您好，关于您提交的工单「{ticket_obj.subject}」"
                f"（编号 {ticket_obj.id}）：\n\n"
            )
    return await _render_admin_messages(
        request,
        admin,
        db,
        prefill_to=prefill_to,
        prefill_title=prefill_title,
        prefill_content=prefill_content,
        sent=sent,
    )


@router.post("/messages")
async def admin_send_message(
    request: Request,
    target_type: str = Form(...),
    target_email: str = Form(""),
    title: str = Form(...),
    content: str = Form(...),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> object:
    """发送站内信：target_type=user 定向（按邮箱）/ broadcast 全局广播。

    广播为每个活跃用户写一行（MVP 用户量小，简单可靠）。失败重渲染表单页提示。
    """
    title, content = title.strip(), content.strip()
    target_email = target_email.strip().lower()

    async def _fail(msg: str) -> object:
        return await _render_admin_messages(
            request,
            admin,
            db,
            prefill_to=target_email,
            prefill_title=title,
            prefill_content=content,
            error=msg,
        )

    if target_type not in (MessageKind.USER, MessageKind.BROADCAST):
        return await _fail("非法的发送类型")
    if not title or len(title) > 255:
        return await _fail("标题不能为空且最长 255 字符")
    if not content or len(content) > 5000:
        return await _fail("内容不能为空且最长 5000 字符")

    if target_type == MessageKind.BROADCAST:
        recipients = (
            (await db.execute(select(User).where(User.status == UserStatus.ACTIVE.value)))
            .scalars()
            .all()
        )
        if not recipients:
            return await _fail("当前没有可接收的活跃用户")
    else:
        if not target_email:
            return await _fail("请填写收件人邮箱")
        recipient = (
            await db.execute(select(User).where(User.email == target_email))
        ).scalar_one_or_none()
        if not recipient:
            return await _fail(f"邮箱 {target_email} 未注册")
        recipients = [recipient]

    batch_id = uuid.uuid4()
    for u in recipients:
        db.add(
            SiteMessage(
                user_id=u.id,
                sender_id=admin.id,
                batch_id=batch_id,
                kind=target_type,
                title=title,
                content=content,
            )
        )
    await db.commit()
    return RedirectResponse(
        url=f"/admin/messages?sent={len(recipients)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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

    # 注册用户列表（最近 50 名，按注册时间倒序）
    registered_users_stmt = (
        select(User)
        .order_by(User.created_at.desc())
        .limit(50)
    )
    registered_users = (await db.execute(registered_users_stmt)).scalars().all()

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
            "registered_users": registered_users,
            "now": now,
        },
    )
