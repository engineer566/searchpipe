"""密码重置 token —— Redis 一次性 token（TTL 1h）+ 每邮箱发信冷却（60s）。

设计：
- token 为 secrets.token_urlsafe(32)，存 Redis：pwdreset:token:<token> → user_id。
  消费即删除（一次性、可撤销），无需 DB 迁移。与 OAuth state 暂存同模式。
- 冷却键 pwdreset:cooldown:<email>（TTL 60s）防刷屏发信；冷却期内静默跳过。
- request_password_reset 对「邮箱不存在 / 冷却中 / 发信失败」一律静默，
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

TOKEN_TTL = 3600      # 重置链接有效期 1 小时
COOLDOWN_TTL = 60     # 同一邮箱 60 秒内只发一封

_TOKEN_PREFIX = "pwdreset:token:"
_COOLDOWN_PREFIX = "pwdreset:cooldown:"


async def create_reset_token(user_id: str) -> str:
    """生成一次性重置 token 并存 Redis。"""
    token = secrets.token_urlsafe(32)
    await get_cache().set(f"{_TOKEN_PREFIX}{token}", user_id, ttl=TOKEN_TTL)
    return token


async def peek_reset_token(token: str) -> str | None:
    """校验 token（不消费）。GET 重置页预检用。"""
    return await get_cache().get(f"{_TOKEN_PREFIX}{token}")


async def consume_reset_token(token: str) -> str | None:
    """校验并消费 token（一次性）。成功返回 user_id，无效/过期返回 None。"""
    cache = get_cache()
    user_id = await cache.get(f"{_TOKEN_PREFIX}{token}")
    if user_id:
        await cache.delete(f"{_TOKEN_PREFIX}{token}")
    return user_id


async def request_password_reset(db: AsyncSession, email: str) -> bool:
    """邮箱已注册且不在冷却期 → 发重置邮件，返回 True；其余情况静默返回 False。

    调用方（API / Dashboard）无论返回值都向用户展示同一话术（防枚举）。
    """
    email = email.strip().lower()
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if not user:
        logger.info("密码重置请求：邮箱未注册，静默跳过 → %s", email)
        return False

    cache = get_cache()
    if await cache.exists(f"{_COOLDOWN_PREFIX}{email}"):
        logger.info("密码重置请求：冷却期内，跳过发信 → %s", email)
        return False

    token = await create_reset_token(str(user.id))
    await cache.set(f"{_COOLDOWN_PREFIX}{email}", "1", ttl=COOLDOWN_TTL)

    link = (
        f"{get_settings().app_base_url.rstrip('/')}"
        f"/dashboard/reset-password?token={token}"
    )
    text = (
        "Hello,\n\n"
        "We received a request to reset your SearchPipe password. Please click the link below "
        "to set a new password within 1 hour:\n\n"
        f"{link}\n\n"
        "If you did not request this, please ignore this email and your password will remain unchanged.\n\n"
        "-- SearchPipe"
    )
    sent = await mailer.send_mail(email, "SearchPipe Password Reset", text)
    return sent
