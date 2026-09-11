"""API Key 管理路由 —— /api-keys/* 。

- POST   /api-keys               创建（返回明文 key）
- GET    /api-keys               列表（prefix/name/last_used/is_default，不含明文）
- GET    /api-keys/reveal        当前用户默认 Key 的明文 + MCP 链接 + 一句话配置
- GET    /api-keys/{id}/reveal   指定 Key 的明文 + MCP 链接 + 一句话配置
- DELETE /api-keys/{id}          吊销（默认 Key 被吊销时自动提升下一把）
全部 Depends(get_current_user)。

安全说明（2026-09-12 需求 #4：Key 平时隐藏、点击可查看）：
- 明文只能经这两个 reveal 端点取得，且仅限控制台登录态（session cookie / JWT）；
  带 sp- API Key 的请求会被 get_current_user 拒绝，即「Key 不能用来读 Key」。
- 越权访问（非本人 / 已吊销 / 不存在）统一 404，不泄漏 Key 归属。
- 上线前创建的老 Key 没有 key_cipher 密文，返回 409 提示重建。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent_setup import build_agent_prompt, mcp_url
from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..db.models import ApiKey, User
from ..db.session import get_db
from .service import (
    create_key,
    ensure_default_key,
    get_valid_key,
    key_plaintext,
    list_keys,
    revoke_key,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Key 名称，便于识别")


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # 明文（可随时经 reveal 端点再次查看）
    key_prefix: str
    is_default: bool
    created_at: datetime


class KeyItem(BaseModel):
    id: str
    name: str
    key_prefix: str
    is_default: bool
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class KeySecretResponse(BaseModel):
    """Key 明文包：明文 + 现成 MCP 链接 + 一句话配置（都含 Key，仅登录态可读）。"""

    id: str
    name: str
    key: str
    key_prefix: str
    is_default: bool
    mcp_url: str
    agent_prompt: str


def _secret_response(api_key: ApiKey, plain: str) -> KeySecretResponse:
    """组装明文包（mcp_url / agent_prompt 由 agent_setup 统一渲染）。"""
    base_url = get_settings().app_base_url.rstrip("/")
    return KeySecretResponse(
        id=str(api_key.id),
        name=api_key.name,
        key=plain,
        key_prefix=api_key.key_prefix,
        is_default=api_key.is_default,
        mcp_url=mcp_url(base_url, plain),
        agent_prompt=build_agent_prompt(base_url, plain),
    )


async def _reveal(api_key: ApiKey) -> KeySecretResponse:
    """解密并组装；老 Key 无密文 → 409 提示重建。"""
    plain = key_plaintext(api_key)
    if plain is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该 Key 创建于明文可查看上线前，无法查看明文；请吊销后重新创建一把",
        )
    return _secret_response(api_key, plain)


@router.post("", response_model=CreateKeyResponse, status_code=status.HTTP_201_CREATED)
async def create(
    req: CreateKeyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CreateKeyResponse:
    """创建 API Key。明文同时以密文留存，之后可在控制台点击查看。"""
    api_key, raw = await create_key(db, user.id, req.name)
    await db.commit()
    return CreateKeyResponse(
        id=str(api_key.id),
        name=api_key.name,
        key=raw,
        key_prefix=api_key.key_prefix,
        is_default=api_key.is_default,
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
            is_default=k.is_default,
            last_used_at=k.last_used_at,
            revoked_at=k.revoked_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.get("/reveal", response_model=KeySecretResponse)
async def reveal_default(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KeySecretResponse:
    """当前用户默认 Key 的明文（没有则自动生成一把默认 Key）。

    控制台「一句话配置」复制按钮与概览页 MCP 命令复制按钮都调它，页面本身
    不渲染明文（避免 Key 出现在 HTML 源码里）。
    """
    api_key = await ensure_default_key(db, user.id)
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "暂无可用 API Key")
    await db.commit()
    return await _reveal(api_key)


@router.get("/{key_id}/reveal", response_model=KeySecretResponse)
async def reveal_one(
    key_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> KeySecretResponse:
    """指定 Key 的明文（MCP 配置卡片里选择其它 Key 时使用）。"""
    api_key = await get_valid_key(db, user.id, key_id)
    if api_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API Key 不存在或已吊销")
    return await _reveal(api_key)


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
