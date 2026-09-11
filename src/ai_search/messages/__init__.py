"""站内信路由 —— 用户侧 API。

- GET  /messages                 列我的站内信（分页，附未读数）
- GET  /messages/unread-count    未读数（导航角标轻量轮询用）
- POST /messages/{id}/read       标记已读（越权读他人消息返回 404，不暴露存在性）

管理端发送在 admin/routes.py（/admin/messages）。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import SiteMessage, User
from ..db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


# ---------- Schemas ----------


class MessageItem(BaseModel):
    id: str
    title: str
    content: str
    kind: str
    read: bool
    read_at: str | None
    created_at: str


class MessageListResponse(BaseModel):
    items: list[MessageItem]
    total: int
    unread_count: int
    page: int
    size: int


# ---------- Helpers ----------


def _to_item(m: SiteMessage) -> MessageItem:
    return MessageItem(
        id=str(m.id),
        title=m.title,
        content=m.content,
        kind=m.kind,
        read=m.read_at is not None,
        read_at=m.read_at.isoformat() if m.read_at else None,
        created_at=m.created_at.isoformat(),
    )


async def _unread_count(db: AsyncSession, user: User) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(SiteMessage)
            .where(SiteMessage.user_id == user.id, SiteMessage.read_at.is_(None))
        )
    ).scalar_one()


# ---------- Endpoints ----------


@router.get("", response_model=MessageListResponse)
async def list_my_messages(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> MessageListResponse:
    """列当前用户的站内信（按创建时间倒序），附未读数。"""
    total = (
        await db.execute(
            select(func.count()).select_from(SiteMessage).where(SiteMessage.user_id == user.id)
        )
    ).scalar_one()

    stmt = (
        select(SiteMessage)
        .where(SiteMessage.user_id == user.id)
        .order_by(desc(SiteMessage.created_at))
        .offset((page - 1) * size)
        .limit(size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return MessageListResponse(
        items=[_to_item(m) for m in rows],
        total=total,
        unread_count=await _unread_count(db, user),
        page=page,
        size=size,
    )


@router.get("/unread-count")
async def unread_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """未读数（控制台导航角标轮询用）。"""
    return {"unread": await _unread_count(db, user)}


@router.post("/{message_id}/read")
async def mark_message_read(
    message_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """标记已读。他人消息一律 404（不暴露存在性）。重复标记幂等。"""
    try:
        mid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found") from None
    msg = await db.get(SiteMessage, mid)
    if not msg or msg.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if msg.read_at is None:
        msg.read_at = datetime.now(timezone.utc)
        await db.commit()
    return {"msg": "Marked as read", "id": message_id}
