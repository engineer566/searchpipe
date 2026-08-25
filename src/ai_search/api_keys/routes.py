"""API Key 管理路由 —— /api-keys/* 。

- POST   /api-keys        创建（返回明文 key，仅此一次）
- GET    /api-keys        列表（prefix/name/last_used，不含完整 key）
- DELETE /api-keys/{id}   吊销
全部 Depends(get_current_user)。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import ApiKey, User
from ..db.session import get_db
from .service import create_key, list_keys, revoke_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Key 名称，便于识别")


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # 明文，仅创建时返回一次
    key_prefix: str
    created_at: datetime


class KeyItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@router.post("", response_model=CreateKeyResponse, status_code=status.HTTP_201_CREATED)
async def create(
    req: CreateKeyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreateKeyResponse:
    """创建 API Key。明文 key 仅此一次返回，请妥善保存。"""
    api_key, raw = await create_key(db, user.id, req.name)
    await db.commit()
    return CreateKeyResponse(
        id=str(api_key.id),
        name=api_key.name,
        key=raw,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
    )


@router.get("", response_model=list[KeyItem])
async def list_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[KeyItem]:
    keys = await list_keys(db, user.id)
    return [
        KeyItem(
            id=str(k.id),
            name=k.name,
            key_prefix=k.key_prefix,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    key_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    ok = await revoke_key(db, user.id, key_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API Key 不存在")
    await db.commit()
