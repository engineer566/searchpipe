"""鉴权路由 —— /auth/* 端点。

登录方式：
  - POST /auth/register        邮箱+密码注册（注册即送免费额度）
  - POST /auth/login           邮箱+密码登录 → access+refresh
  - POST /auth/refresh         refresh token 换新 access
  - POST /auth/forgot-password 忘记密码 → 发重置邮件（防枚举，统一话术）
  - POST /auth/reset-password  一次性 token + 新密码 → 重置
  - GET  /auth/oauth/github    跳转 GitHub 授权
  - GET  /auth/oauth/github/callback   GitHub 回调 → 建/绑 OAuthAccount → 签发 JWT
  - GET  /auth/oauth/google    跳转 Google 授权
  - GET  /auth/oauth/google/callback   Google 回调 → 建/绑 OAuthAccount → 签发 JWT
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import OAuthAccount, User, UserRole, UserStatus
from ..db.session import get_db
from .dependencies import get_current_user
from .jwt_handler import create_access_token, create_refresh_token, decode_token
from .oauth import GitHubOAuth, GoogleOAuth
from .email_verification import (
    consume_verification_token,
    send_verification_email,
)
from .password import hash_password, verify_password
from .password_reset import consume_reset_token, request_password_reset

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


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class UserInfo(BaseModel):
    id: str
    email: str | None = None
    phone: str | None = None
    role: str
    email_verified: bool = False


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
        remark="Free tier credits",
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


def _normalize_email(email: str) -> str:
    """邮箱规范化：去空格 + 小写，防大小写/空格变体造成重复账号。"""
    return email.strip().lower()


# ---------- 邮箱+密码 ----------


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """邮箱+密码注册。注册即签发 token 并赠送免费额度，同时发送邮箱验证邮件。"""
    email = _normalize_email(req.email)
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already registered")

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        role=UserRole.USER.value,
        status=UserStatus.ACTIVE.value,
        email_verified=False,  # 新注册用户需要验证邮箱
    )
    db.add(user)
    try:
        await db.flush()
        await _grant_free_credits(db, user)
        await _touch_login(db, user)
        await db.commit()
    except IntegrityError as e:
        # 并发重复注册竞态：唯一索引兜底
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already registered") from e

    # 发送验证邮件（异步，不阻塞响应；失败不影响注册）
    await send_verification_email(db, email)

    return _issue_tokens(user)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """邮箱+密码登录。"""
    user = (
        await db.execute(select(User).where(User.email == _normalize_email(req.email)))
    ).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been disabled")
    await _touch_login(db, user)
    await db.commit()
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """refresh token → 新 access + refresh。"""
    payload = decode_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    user = await db.get(User, payload["sub"])
    if not user or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return _issue_tokens(user)


# ---------- 忘记密码 / 重置密码 ----------


@router.post("/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """忘记密码：存在则发重置邮件。无论邮箱是否注册都返回同一话术（防枚举）。"""
    await request_password_reset(db, req.email)
    return {"detail": "If this email is registered, a reset link has been sent (valid for 1 hour)"}


@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """一次性 token + 新密码 → 重置。token 无效/过期/复用均 400。"""
    user_id = await consume_reset_token(req.token)
    if not user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or expired; please request a new one")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or expired; please request a new one")
    user.password_hash = hash_password(req.new_password)
    await db.commit()
    return {"detail": "Password has been reset; please log in with your new password"}


# ---------- OAuth（GitHub / Google）----------


async def _oauth_callback(
    provider: str,
    oauth: GitHubOAuth | GoogleOAuth,
    code: str,
    state: str,
    db: AsyncSession,
) -> RedirectResponse:
    """OAuth 回调通用流程：验 state → 换用户信息 → 建/绑账号 → 签发 JWT → 302 回 dashboard。"""
    from ..utils.cache import get_cache

    cache = get_cache()
    if not await cache.exists(f"oauth:state:{provider}:{state}"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state")
    await cache.delete(f"oauth:state:{provider}:{state}")

    try:
        oauth_user = await oauth.fetch_user(code)
    except Exception as e:  # noqa: BLE001
        logger.error("%s OAuth 换取用户信息失败: %s", provider, e)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"{provider.capitalize()} authorization failed"
        ) from e

    user = await _get_or_create_user_by_oauth(
        db, provider, oauth_user.provider_uid, oauth_user.email, oauth_user.name
    )
    await _touch_login(db, user)
    await db.commit()

    token = create_access_token(str(user.id))
    redirect_base = get_settings().oauth_redirect_base.rstrip("/")
    return RedirectResponse(url=f"{redirect_base}/dashboard/auth?token={token}")


@router.get("/oauth/github")
async def oauth_github() -> RedirectResponse:
    """跳转到 GitHub 授权页。state 防 CSRF，存 Redis。"""
    gh = GitHubOAuth()
    if not gh.enabled:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "GitHub OAuth is not configured")
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
    return await _oauth_callback("github", GitHubOAuth(), code, state, db)


@router.get("/oauth/google")
async def oauth_google() -> RedirectResponse:
    """跳转到 Google 授权页。state 防 CSRF，存 Redis。"""
    gg = GoogleOAuth()
    if not gg.enabled:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Google OAuth is not configured")
    state = secrets.token_urlsafe(16)
    from ..utils.cache import get_cache

    await get_cache().set(f"oauth:state:google:{state}", "1", ttl=600)
    return RedirectResponse(url=gg.authorize_url(state))


@router.get("/oauth/google/callback")
async def oauth_google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Google 回调 → 换 token → 建/绑账号 → 签发 JWT → 302 带 token 回 dashboard。"""
    return await _oauth_callback("google", GoogleOAuth(), code, state, db)


# ---------- 邮箱验证 ----------


@router.get("/verify-email")
async def verify_email(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """验证邮箱链接。成功 → 302 到登录页带提示；失败 → 302 到登录页带错误提示。"""
    user_id = await consume_verification_token(token)
    if not user_id:
        return RedirectResponse(
            url="/dashboard/login?verify_error=1",
            status_code=status.HTTP_302_FOUND,
        )

    user = await db.get(User, user_id)
    if not user:
        return RedirectResponse(
            url="/dashboard/login?verify_error=1",
            status_code=status.HTTP_302_FOUND,
        )

    if user.email_verified:
        # 已验证，直接跳转
        return RedirectResponse(
            url="/dashboard/login?verify_already=1",
            status_code=status.HTTP_302_FOUND,
        )

    user.email_verified = True

    # 邮箱验证通过即自动生成默认 API Key（history/20260912.txt #4）：
    # 用户验证后无需再去控制台手动建 Key，直接复制一句话配置即可接入 MCP。
    # 惰性导入避免 auth ↔ api_keys 模块级循环依赖。
    from ..api_keys.service import ensure_default_key

    await ensure_default_key(db, user.id)
    await db.commit()

    return RedirectResponse(
        url="/dashboard/login?verify_success=1",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/resend-verification")
async def resend_verification(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """重新发送验证邮件（需登录）。"""
    from .email_verification import resend_verification_email

    if user.email_verified:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email is already verified")

    sent = await resend_verification_email(db, str(user.id))
    if not sent:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Sending too frequently; please try again later")

    return {"detail": "Verification email resent; please check your inbox"}


# ---------- 当前用户 ----------


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user)) -> UserInfo:
    """返回当前登录用户信息。"""
    return UserInfo(
        id=str(user.id),
        email=user.email,
        phone=user.phone,
        role=user.role,
        email_verified=user.email_verified,
    )
