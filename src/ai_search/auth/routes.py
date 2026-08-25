"""鉴权路由 —— /auth/* 端点。

登录方式：
  - POST /auth/register        邮箱+密码注册（注册即送免费额度）
  - POST /auth/login           邮箱+密码登录 → access+refresh
  - POST /auth/refresh         refresh token 换新 access
  - GET  /auth/oauth/github    跳转 GitHub 授权
  - GET  /auth/oauth/github/callback   GitHub 回调 → 建/绑 OAuthAccount → 签发 JWT
  - GET  /auth/oauth/wechat    微信扫码（stub，501）
  - GET  /auth/oauth/wechat/callback   微信回调（stub，501）
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import OAuthAccount, User, UserRole, UserStatus
from ..db.session import get_db
from .dependencies import get_current_user
from .jwt_handler import create_access_token, create_refresh_token, decode_token
from .oauth import GitHubOAuth, WeChatOAuth
from .password import hash_password, verify_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- Schemas ----------


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: str
    email: str | None = None
    phone: str | None = None
    role: str


# ---------- 辅助 ----------


def _issue_tokens(user: User) -> TokenResponse:
    """为用户签发 access + refresh。"""
    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


async def _touch_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)


# OAuth 登录会自动建号并发放免费额度；
# 为避免循环导入（billing.service 依赖鉴权层），这里用惰性导入发放额度。
async def _grant_free_credits(db: AsyncSession, user: User) -> None:
    from ..billing.service import grant_credits  # noqa: WPS433 (惰性导入防循环)

    settings = get_settings()
    await grant_credits(
        db,
        user_id=user.id,
        amount=settings.free_tier_credits,
        tx_type="grant",
        remark="内测免费额度",
    )


async def _get_or_create_user_by_oauth(
    db: AsyncSession, provider: str, provider_uid: str, email: str | None, name: str | None
) -> User:
    """OAuth 账号 → 绑定已有 User 或新建。返回 User。"""
    stmt = select(OAuthAccount).where(
        OAuthAccount.provider == provider, OAuthAccount.provider_uid == provider_uid
    )
    oauth_acc = (await db.execute(stmt)).scalar_one_or_none()
    if oauth_acc:
        # 已绑定 → 直接取用户
        return await db.get(User, oauth_acc.user_id)

    # 未绑定：优先按 email 匹配已有账号，否则新建
    user: User | None = None
    if email:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        user = User(
            email=email or None,
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
        )
        db.add(user)
        await db.flush()
        await _grant_free_credits(db, user)

    db.add(
        OAuthAccount(
            user_id=user.id,
            provider=provider,
            provider_uid=provider_uid,
        )
    )
    await db.flush()
    return user


# ---------- 邮箱+密码 ----------


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """邮箱+密码注册。注册即签发 token 并赠送免费额度。"""
    existing = (
        await db.execute(select(User).where(User.email == req.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "该邮箱已注册")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        role=UserRole.USER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    await db.flush()
    await _grant_free_credits(db, user)
    await _touch_login(db, user)
    await db.commit()
    return _issue_tokens(user)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """邮箱+密码登录。"""
    user = (
        await db.execute(select(User).where(User.email == req.email))
    ).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    if user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用")
    await _touch_login(db, user)
    await db.commit()
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """refresh token → 新 access + refresh。"""
    payload = decode_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token 无效或已过期")
    user = await db.get(User, payload["sub"])
    if not user or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return _issue_tokens(user)


# ---------- GitHub OAuth ----------


@router.get("/oauth/github")
async def oauth_github() -> RedirectResponse:
    """跳转到 GitHub 授权页。state 防 CSRF，存 Redis。"""
    gh = GitHubOAuth()
    if not gh.enabled:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "GitHub OAuth 未配置")
    state = secrets.token_urlsafe(16)
    # state 暂存 Redis 供回调校验（TTL 10min）
    from ..utils.cache import get_cache

    await get_cache().set(f"oauth:state:github:{state}", "1", ttl=600)
    return RedirectResponse(url=gh.authorize_url(state))


@router.get("/oauth/github/callback")
async def oauth_github_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """GitHub 回调 → 换 token → 建/绑账号 → 签发 JWT → 302 带 token 回 dashboard。"""
    from ..utils.cache import get_cache

    cache = get_cache()
    if not await cache.exists(f"oauth:state:github:{state}"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state 校验失败")
    await cache.delete(f"oauth:state:github:{state}")

    gh = GitHubOAuth()
    try:
        oauth_user = await gh.fetch_user(code)
    except Exception as e:  # noqa: BLE001
        logger.error("GitHub OAuth 换取用户信息失败: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "GitHub 授权失败") from e

    user = await _get_or_create_user_by_oauth(
        db, "github", oauth_user.provider_uid, oauth_user.email, oauth_user.name
    )
    await _touch_login(db, user)
    await db.commit()

    token = create_access_token(str(user.id))
    redirect_base = get_settings().oauth_redirect_base.rstrip("/")
    return RedirectResponse(url=f"{redirect_base}/dashboard/auth?token={token}")


# ---------- 微信 OAuth（stub）----------


@router.get("/oauth/wechat")
async def oauth_wechat() -> dict:
    """微信扫码登录 —— 待个体工商户后接微信开放平台。"""
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "微信登录待个体工商户后接入微信开放平台")


@router.get("/oauth/wechat/callback")
async def oauth_wechat_callback() -> dict:
    """微信回调 —— stub。"""
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "微信登录待个体工商户后接入微信开放平台")


# ---------- 当前用户 ----------


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user)) -> UserInfo:
    """返回当前登录用户信息。"""
    return UserInfo(id=str(user.id), email=user.email, phone=user.phone, role=user.role)
