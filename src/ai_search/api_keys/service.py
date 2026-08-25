"""API Key 管理服务。

格式：sp- + 32 字符随机。明文仅创建时返回一次；存储 key_prefix(前8位) + key_hash(argon2)。
验证：按 prefix 粗筛候选 → argon2 verify 命中（dependencies._resolve_api_key 已实现，此处仅管理）。
"""

import logging
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.password import hash_password
from ..db.models import ApiKey

logger = logging.getLogger(__name__)

KEY_PREFIX = "sp-"
KEY_RANDOM_LEN = 32
PREFIX_DISPLAY_LEN = 8  # 明文展示前 8 位（含 sp-）


def _generate_raw_key() -> str:
    """生成 sp- + 32 随机字符。"""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(KEY_RANDOM_LEN))


async def create_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
) -> tuple[ApiKey, str]:
    """创建 API Key。返回 (ApiKey 记录, 明文 key)。明文仅此一次返回。"""
    raw = _generate_raw_key()
    api_key = ApiKey(
        user_id=user_id,
        name=name,
        key_prefix=raw[:PREFIX_DISPLAY_LEN],
        key_hash=hash_password(raw),
    )
    db.add(api_key)
    await db.flush()
    logger.info("创建 API Key user=%s name=%s prefix=%s", user_id, name, api_key.key_prefix)
    return api_key, raw


async def list_keys(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[ApiKey]:
    """列出用户所有未吊销 + 已吊销 key（列表展示用，不含 hash）。"""
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def revoke_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    """吊销 key（软删除）。返回是否成功（key 不存在或不属于该用户返回 False）。"""
    api_key = await db.get(ApiKey, key_id)
    if not api_key or api_key.user_id != user_id:
        return False
    if api_key.revoked_at is not None:
        return True  # 已吊销，幂等
    api_key.revoked_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("吊销 API Key user=%s key_id=%s", user_id, key_id)
    return True


async def delete_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    """物理删除 key（通常用 revoke；此为彻底清理）。"""
    api_key = await db.get(ApiKey, key_id)
    if not api_key or api_key.user_id != user_id:
        return False
    await db.delete(api_key)
    await db.flush()
    return True
