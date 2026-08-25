"""协议无关鉴权核 —— 纯 DB，不依赖 FastAPI。

resolve_api_key / resolve_jwt 是鉴权的唯一真相源：
- HTTP 依赖层（dependencies.py）调用它们，捕获 AuthError 转 HTTPException(401)。
- MCP tool（mcp_server.py）调用它们，捕获 AuthError 转 ToolError。

AuthContext / API_KEY_PREFIX 定义在此处，供 HTTP 依赖层与 MCP 共享，
避免 dependencies ↔ core 循环导入。
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiKey, User
from .errors import AuthError
from .jwt_handler import decode_token
from .password import verify_password

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "sp-"


class AuthContext:
    """统一鉴权上下文：user 必有，api_key 可空（JWT 登录时为 None）。"""

    def __init__(self, user: User, api_key: ApiKey | None = None) -> None:
        self.user = user
        self.api_key = api_key

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def api_key_id(self) -> uuid.UUID | None:
        return self.api_key.id if self.api_key else None


async def resolve_api_key(raw: str, db: AsyncSession) -> AuthContext:
    """按 prefix 粗筛 → argon2 verify 命中 → 更新 last_used_at → 返回 AuthContext。

    失败抛 AuthError（HTTP 401 / MCP tool error）。
    """
    prefix = raw[:8]  # sp-xxx 前 8 位作粗筛键
    stmt = select(ApiKey).where(
        ApiKey.key_prefix == prefix, ApiKey.revoked_at.is_(None)
    )
    result = await db.execute(stmt)
    for key in result.scalars().all():
        if verify_password(raw, key.key_hash):
            key.last_used_at = datetime.now(timezone.utc)
            user = await db.get(User, key.user_id)
            if not user or user.status != "active":
                raise AuthError("用户不存在或已停用")
            return AuthContext(user=user, api_key=key)
    raise AuthError("API Key 无效或已吊销")


async def resolve_jwt(token: str, db: AsyncSession) -> AuthContext:
    """JWT 解析 → 查 User → 返回 AuthContext（api_key=None）。

    失败抛 AuthError。
    """
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise AuthError("认证凭据无效或已过期")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AuthError("认证凭据无效") from None
    user = await db.get(User, user_id)
    if not user or user.status != "active":
        raise AuthError("用户不存在或已停用")
    return AuthContext(user=user)


__all__ = [
    "API_KEY_PREFIX",
    "AuthContext",
    "AuthError",
    "resolve_api_key",
    "resolve_jwt",
]
