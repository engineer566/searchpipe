"""鉴权依赖 —— FastAPI 依赖注入核心。

- get_current_user：JWT 解析 → 查 User；无 Bearer 头时兜底读浏览器 session cookie。
- require_api_key：Authorization: Bearer sp-xxx 或 X-API-Key 头（程序调用）。
- get_current_user_or_api_key：三者皆可，统一返回 AuthContext。
- get_current_admin：role ∈ {owner, admin}。

AuthContext 打包 user + api_key（可空），供计费/用量/限流复用。

协议无关的鉴权核在 auth.core（resolve_api_key / resolve_jwt），HTTP 层
在此捕获 AuthError 转 HTTPException(401)，MCP 层转 ToolError。
"""

import logging

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User
from ..db.session import get_db
from .core import API_KEY_PREFIX, AuthContext, resolve_api_key, resolve_jwt
from .errors import AuthError
from .session import read_session_cookie

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _extract_token(
    credentials: HTTPAuthorizationCredentials | None,
    authorization: str | None,
) -> str | None:
    """从 HTTPBearer 或原始 Authorization 头取 Bearer token。"""
    if credentials:
        return credentials.credentials
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _resolve_api_key(
    raw: str, db: AsyncSession
) -> AuthContext:
    """HTTP 包装：调 core.resolve_api_key，AuthError → HTTPException(401)。"""
    try:
        return await resolve_api_key(raw, db)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e


async def _resolve_jwt(token: str, db: AsyncSession) -> AuthContext:
    """HTTP 包装：调 core.resolve_jwt，AuthError → HTTPException(401)。"""
    try:
        return await resolve_jwt(token, db)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e


async def _resolve_cookie(request: Request, db: AsyncSession) -> User | None:
    """浏览器 session cookie 兜底：有效返回 User，无效/过期返回 None。"""
    user_id = read_session_cookie(request)
    if not user_id:
        return None
    user = await db.get(User, user_id)
    if not user or user.status != "active":
        return None
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """JWT 鉴权 → 返回 User；无 Bearer 头时兜底 session cookie。无/错凭据抛 401。"""
    token = _extract_token(credentials, authorization)
    if token and not token.startswith(API_KEY_PREFIX):
        ctx = await _resolve_jwt(token, db)
        return ctx.user
    user = await _resolve_cookie(request, db)
    if user:
        return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No authentication credentials provided")


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """API Key 鉴权 → 返回 AuthContext（含 user + api_key）。"""
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    if not raw or not raw.startswith(API_KEY_PREFIX):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No valid API key provided")
    return await _resolve_api_key(raw, db)


async def get_current_user_or_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """三通道：先试 JWT，再试 API Key，最后兜底 session cookie。统一返回 AuthContext。

    /search 端点用此依赖：程序带 JWT/sp- key，控制台浏览器带 cookie。
    """
    token = _extract_token(credentials, authorization)
    # JWT 通道（token 非 sp- 前缀视为 JWT）
    if token and not token.startswith(API_KEY_PREFIX):
        return await _resolve_jwt(token, db)
    # API Key 通道
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    if raw and raw.startswith(API_KEY_PREFIX):
        return await _resolve_api_key(raw, db)
    # 浏览器 session cookie 兜底
    user = await _resolve_cookie(request, db)
    if user:
        return AuthContext(user=user)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No valid authentication provided (JWT or API key)")


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """管理员鉴权：role ∈ {owner, admin}，否则 403。"""
    if user.role not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator privileges required")
    return user
