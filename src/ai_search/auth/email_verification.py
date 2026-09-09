"""邮箱验证 token —— Redis 一次性 token（TTL 24h）+ 每邮箱发信冷却（60s）。

设计：
- token 为 secrets.token_urlsafe(32)，存 Redis：verify:token:<token> → user_id。
  消费即删除（一次性），无需 DB 迁移。与密码重置 token 同模式。
- 冷却键 verify:cooldown:<email>（TTL 60s）防刷屏发信；冷却期内静默跳过。
- send_verification_email 对「邮箱不存在 / 已验证 / 冷却中 / 发信失败」一律静默，
  由调用方返回统一话术，防邮箱枚举。

供两处使用：API（auth/routes.py）与 Dashboard 页面（dashboard/routes.py）。
"""

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import User
from ..utils import mailer
from ..utils.cache import get_cache

logger = logging.getLogger(__name__)

TOKEN_TTL = 86400     # 验证链接有效期 24 小时
COOLDOWN_TTL = 60     # 同一邮箱 60 秒内只发一封

_TOKEN_PREFIX = "verify:token:"
_COOLDOWN_PREFIX = "verify:cooldown:"


async def create_verification_token(user_id: str) -> str:
    """生成一次性验证 token 并存 Redis。"""
    token = secrets.token_urlsafe(32)
    await get_cache().set(f"{_TOKEN_PREFIX}{token}", user_id, ttl=TOKEN_TTL)
    return token


async def consume_verification_token(token: str) -> str | None:
    """校验并消费 token（一次性）。成功返回 user_id，无效/过期返回 None。"""
    cache = get_cache()
    user_id = await cache.get(f"{_TOKEN_PREFIX}{token}")
    if user_id:
        await cache.delete(f"{_TOKEN_PREFIX}{token}")
    return user_id


async def send_verification_email(db: AsyncSession, email: str) -> bool:
    """邮箱已注册且未验证且不在冷却期 → 发验证邮件，返回 True；其余情况静默返回 False。

    调用方（API / Dashboard）无论返回值都向用户展示同一话术（防枚举）。
    """
    email = email.strip().lower()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if not user:
        logger.info("邮箱验证请求：邮箱未注册，静默跳过 → %s", email)
        return False

    if user.email_verified:
        logger.info("邮箱验证请求：邮箱已验证，静默跳过 → %s", email)
        return False

    cache = get_cache()
    if await cache.exists(f"{_COOLDOWN_PREFIX}{email}"):
        logger.info("邮箱验证请求：冷却期内，跳过发信 → %s", email)
        return False

    token = await create_verification_token(str(user.id))
    await cache.set(f"{_COOLDOWN_PREFIX}{email}", "1", ttl=COOLDOWN_TTL)

    link = (
        f"{get_settings().app_base_url.rstrip('/')}"
        f"/auth/verify-email?token={token}"
    )
    text = (
        "你好，\n\n"
        "欢迎注册 SearchPipe！请点击以下链接验证你的邮箱地址：\n\n"
        f"{link}\n\n"
        "链接 24 小时内有效。如果这不是你的操作，请忽略本邮件。\n\n"
        "—— SearchPipe"
    )
    sent = await mailer.send_mail(email, "SearchPipe 邮箱验证", text)
    return sent


async def resend_verification_email(db: AsyncSession, user_id: str) -> bool:
    """重新发送验证邮件（用于已登录用户手动重发）。"""
    user = await db.get(User, user_id)
    if not user or not user.email or user.email_verified:
        return False

    cache = get_cache()
    if await cache.exists(f"{_COOLDOWN_PREFIX}{user.email}"):
        logger.info("重发验证邮件：冷却期内，跳过 → %s", user.email)
        return False

    token = await create_verification_token(str(user.id))
    await cache.set(f"{_COOLDOWN_PREFIX}{user.email}", "1", ttl=COOLDOWN_TTL)

    link = (
        f"{get_settings().app_base_url.rstrip('/')}"
        f"/auth/verify-email?token={token}"
    )
    text = (
        "你好，\n\n"
        "你请求重新发送 SearchPipe 邮箱验证链接。请点击以下链接验证你的邮箱地址：\n\n"
        f"{link}\n\n"
        "链接 24 小时内有效。如果这不是你的操作，请忽略本邮件。\n\n"
        "—— SearchPipe"
    )
    sent = await mailer.send_mail(user.email, "SearchPipe 邮箱验证（重发）", text)
    return sent
