"""API Key 管理服务。

格式：sp- + 32 字符随机。存储三层：
- key_prefix(前 8 位)：列表展示 + 鉴权粗筛；
- key_hash(argon2)：唯一鉴权凭据；
- key_cipher(Fernet 密文)：仅供「点击查看明文 / 生成 MCP 链接」（2026-09-12 #4）。

默认 Key：邮箱验证通过即自动生成一把（is_default=True），控制台 MCP 配置与
一句话配置默认使用它；用户手动创建第一把 Key 时也会自动成为默认。
验证：按 prefix 粗筛候选 → argon2 verify 命中（auth/core.resolve_api_key 已实现）。
"""

import logging
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.password import hash_password
from ..db.models import ApiKey
from .crypto import decrypt_key, encrypt_key

logger = logging.getLogger(__name__)

KEY_PREFIX = "sp-"
KEY_RANDOM_LEN = 32
PREFIX_DISPLAY_LEN = 8  # 明文展示前 8 位（含 sp-）
DEFAULT_KEY_NAME = "默认 Key"  # 邮箱验证后自动生成的 Key 名称


def _generate_raw_key() -> str:
    """生成 sp- + 32 随机字符。"""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(KEY_RANDOM_LEN))


async def create_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
) -> tuple[ApiKey, str]:
    """创建 API Key。返回 (ApiKey 记录, 明文 key)。

    明文除返回值外另存 key_cipher 密文（可再次查看）；用户当前没有任何有效 Key
    时，这把 Key 自动成为默认 Key。
    """
    raw = _generate_raw_key()
    is_first = not await list_valid_keys(db, user_id)
    api_key = ApiKey(
        user_id=user_id,
        name=name,
        key_prefix=raw[:PREFIX_DISPLAY_LEN],
        key_hash=hash_password(raw),
        key_cipher=encrypt_key(raw),
        is_default=is_first,
    )
    db.add(api_key)
    await db.flush()
    logger.info(
        "创建 API Key user=%s name=%s prefix=%s default=%s",
        user_id,
        name,
        api_key.key_prefix,
        is_first,
    )
    return api_key, raw


async def list_keys(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[ApiKey]:
    """列出用户所有 key（含已吊销，列表展示用，不含 hash/密文）。"""
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_valid_keys(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[ApiKey]:
    """列出用户未吊销的 key（默认 Key 优先，其余按创建时间倒序）。"""
    stmt = (
        select(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.is_default.desc(), ApiKey.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_valid_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    key_id: uuid.UUID | str,
) -> ApiKey | None:
    """取属于该用户且未吊销的 Key；不存在/越权/已吊销/ID 非法返回 None。"""
    if isinstance(key_id, str):
        try:
            key_id = uuid.UUID(key_id)
        except (ValueError, AttributeError):
            return None
    api_key = await db.get(ApiKey, key_id)
    if not api_key or api_key.user_id != user_id or api_key.revoked_at is not None:
        return None
    return api_key


async def get_default_key(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> ApiKey | None:
    """取默认 Key：先找 is_default 标记，再回落到最新有效 Key（旧数据兜底）。"""
    valid = await list_valid_keys(db, user_id)
    for key in valid:
        if key.is_default:
            return key
    return valid[0] if valid else None


async def ensure_default_key(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> ApiKey | None:
    """保证用户有一把默认 Key（幂等）。

    - 无有效 Key → 新建「默认 Key」并标记默认（邮箱验证通过后调用，用户无需
      先去控制台手动创建就能一句话配置 MCP）；
    - 有有效 Key 但无默认标记（本次上线前创建的老 Key）→ 把最新一把提升为默认。
    返回默认 Key；用户不存在等异常情况返回 None。
    """
    default_key = await get_default_key(db, user_id)
    if default_key is None:
        api_key, _ = await create_key(db, user_id, DEFAULT_KEY_NAME)
        await db.flush()
        logger.info("自动生成默认 API Key user=%s prefix=%s", user_id, api_key.key_prefix)
        return api_key
    if not default_key.is_default:
        default_key.is_default = True
        await db.flush()
        logger.info("提升既有 API Key 为默认 user=%s prefix=%s", user_id, default_key.key_prefix)
    return default_key


def key_plaintext(api_key: ApiKey) -> str | None:
    """取 Key 明文（解密 key_cipher）；老 Key 无密文或解密失败返回 None。"""
    return decrypt_key(api_key.key_cipher)


async def promote_default_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    exclude_id: uuid.UUID | None = None,
) -> ApiKey | None:
    """把最新有效 Key 提升为默认（吊销默认 Key 后调用）；无候选返回 None。"""
    for key in await list_valid_keys(db, user_id):
        if exclude_id is not None and key.id == exclude_id:
            continue
        if not key.is_default:
            key.is_default = True
            await db.flush()
        return key
    return None


async def revoke_key(
    db: AsyncSession,
    user_id: uuid.UUID,
    key_id: uuid.UUID,
) -> bool:
    """吊销 key（软删除）。返回是否成功（key 不存在或不属于该用户返回 False）。

    吊销的是默认 Key 时，自动把剩下最新的一把提升为默认，保证控制台 MCP
    配置/一句话配置仍能拿到可用 Key。
    """
    api_key = await db.get(ApiKey, key_id)
    if not api_key or api_key.user_id != user_id:
        return False
    if api_key.revoked_at is not None:
        return True  # 已吊销，幂等
    api_key.revoked_at = datetime.now(timezone.utc)
    was_default = api_key.is_default
    api_key.is_default = False
    await db.flush()
    if was_default:
        await promote_default_key(db, user_id, exclude_id=api_key.id)
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
