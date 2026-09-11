"""反馈工单路由 —— 用户侧 API。

- POST /feedback   提交工单（JWT/session 鉴权 + 频率限制 + 输入校验）
- GET  /feedback   列自己的工单（分页）
"""

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import FeedbackTicket, TicketCategory, TicketStatus, User
from ..db.session import get_db
from ..utils.cache import get_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])

# 频率限制：每用户每 30 秒最多提交 1 次
_FEEDBACK_COOLDOWN_SEC = 30


# ---------- Schemas ----------


class CreateFeedbackRequest(BaseModel):
    category: str = Field(default="other")
    subject: str = Field(..., max_length=255)
    content: str = Field(..., max_length=5000)

    @field_validator("category")
    @classmethod
    def _check_category(cls, v: str) -> str:
        allowed = {e.value for e in TicketCategory}
        if v not in allowed:
            raise ValueError(f"Invalid category, allowed values: {', '.join(sorted(allowed))}")
        return v

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("subject must not be empty")
        if len(v) > 255:
            raise ValueError("subject must be at most 255 characters")
        return v

    @field_validator("content")
    @classmethod
    def _check_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content must not be empty")
        if len(v) > 5000:
            raise ValueError("content must be at most 5000 characters")
        return v


class FeedbackItem(BaseModel):
    id: str
    category: str
    subject: str
    content: str
    status: str
    created_at: str
    updated_at: str


class FeedbackListResponse(BaseModel):
    items: list[FeedbackItem]
    total: int
    page: int
    size: int


# ---------- Helpers ----------


async def _check_feedback_rate_limit(user_id: uuid.UUID) -> None:
    """Redis 简单冷却：每用户 30 秒内只能提交一次工单。"""
    cache = get_cache()
    key = f"feedback:cooldown:{user_id}"
    exists = await cache.exists(key)
    if exists:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many submissions, please try again in {_FEEDBACK_COOLDOWN_SEC} seconds",
        )
    await cache.set(key, "1", ttl=_FEEDBACK_COOLDOWN_SEC)


# ---------- Endpoints ----------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    request: Request,
    req: CreateFeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """提交工单。频率限制 30 秒/次。"""
    await _check_feedback_rate_limit(user.id)

    ticket = FeedbackTicket(
        user_id=user.id,
        category=req.category,
        subject=req.subject,
        content=req.content,
        status=TicketStatus.OPEN.value,
    )
    db.add(ticket)
    await db.commit()
    return {"id": str(ticket.id), "msg": "Ticket submitted"}


@router.get("", response_model=FeedbackListResponse)
async def list_my_feedback(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> FeedbackListResponse:
    """列当前用户的工单列表（按创建时间倒序）。"""
    total = (
        await db.execute(
            select(func.count()).select_from(FeedbackTicket).where(FeedbackTicket.user_id == user.id)
        )
    ).scalar_one()

    stmt = (
        select(FeedbackTicket)
        .where(FeedbackTicket.user_id == user.id)
        .order_by(desc(FeedbackTicket.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return FeedbackListResponse(
        items=[
            FeedbackItem(
                id=str(r.id),
                category=r.category,
                subject=r.subject,
                content=r.content,
                status=r.status,
                created_at=r.created_at.isoformat(),
                updated_at=r.updated_at.isoformat(),
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )
