"""前端 Dashboard —— Jinja2 服务端渲染 + itsdangerous 签名 session cookie。

浏览器登录态：session cookie（签发/校验逻辑在 auth/session.py，与 API 鉴权兜底共享）。
程序调用走 JWT/API Key（不经此层）。

路由：
- GET /                          营销首页（site_router，公开）
- GET /dashboard/auth?token=xxx    OAuth/JWT 登录后落地：把 token 换成 session cookie → 302 到 /dashboard
- GET /dashboard/login             登录页（手机+短信、GitHub、邮箱密码）
- POST /dashboard/login            邮箱密码登录 → 设 cookie → 302 /dashboard
- POST /dashboard/login/sms        手机+短信登录 → 设 cookie → 302 /dashboard
- POST /dashboard/sms/send         发送短信验证码
- GET /dashboard/logout            清 cookie → 302 /dashboard/login
- GET /dashboard                   主面板（余额/用量/快速搜索）
- GET /dashboard/api-keys          API Key 管理
- GET /dashboard/usage             用量统计
- GET /dashboard/billing           充值/流水
- GET /dashboard/docs              开发文档
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from ..auth.jwt_handler import decode_token
from ..auth.password import verify_password
from ..auth.session import (
    clear_session_cookie,
    read_session_cookie,
    set_session_cookie,
)
from ..auth.sms import send_code, verify_code
from ..config import get_settings
from ..db.models import User, UserRole, UserStatus
from ..db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
site_router = APIRouter(tags=["site"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


async def _user_from_session(request: Request, db: AsyncSession) -> User | None:
    """从 session cookie 解出当前用户。无效/过期返回 None。"""
    user_id = read_session_cookie(request)
    if not user_id:
        return None
    user = await db.get(User, user_id)
    if not user or user.status != UserStatus.ACTIVE.value:
        return None
    return user


# ---------- 营销首页（公开） ----------


@site_router.get("/")
async def landing(request: Request) -> object:
    """营销首页：产品定位 + 特性 + API 示例 + 定价锚点。"""
    return templates.TemplateResponse(request, "landing.html", {})


# ---------- 登录入口 ----------


@router.get("/auth")
async def auth_landing(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """OAuth/JWT 登录落地：token（JWT access）→ 校验 → 设 session cookie → /dashboard。"""
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 无效")
    user = await db.get(User, payload["sub"])
    if not user or user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(resp, str(user.id))
    return resp


@router.get("/login")
async def login_page(request: Request) -> object:
    """登录页（手机+短信 / 邮箱密码 / GitHub OAuth）。"""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "github_oauth_enabled": bool(
                settings.oauth_github_client_id and settings.oauth_github_client_secret
            ),
            "wechat_enabled": False,
        },
    )


@router.post("/login")
async def login_submit(
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """邮箱密码登录 → 设 cookie → /dashboard。"""
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    if user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用")
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(resp, str(user.id))
    return resp


@router.post("/sms/send")
async def dashboard_sms_send(phone: str = Form(...)) -> dict:
    """Dashboard 内发送短信验证码。"""
    try:
        await send_code(phone)
    except ValueError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e)) from e
    return {"msg": "验证码已发送"}


@router.post("/login/sms")
async def login_sms(
    phone: str = Form(...),
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """手机+短信登录 → 设 cookie → /dashboard。首次自动建号。"""
    if not await verify_code(phone, code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "验证码错误或已失效")
    user = (
        await db.execute(select(User).where(User.phone == phone))
    ).scalar_one_or_none()
    if not user:
        user = User(
            phone=phone,
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
        )
        db.add(user)
        await db.flush()
        # 首次建号发免费额度
        from ..billing.service import grant_credits

        await grant_credits(
            db, user_id=user.id, amount=get_settings().free_tier_credits,
            tx_type="grant", remark="内测免费额度",
        )
    elif user.status != UserStatus.ACTIVE.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用")
    await db.commit()
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(resp, str(user.id))
    return resp


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(resp)
    return resp


# ---------- 页面 ----------


@router.get("")
@router.get("/")
async def dashboard_home(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    """主面板：余额 + 用量曲线 + 快速搜索。"""
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    from ..billing.service import get_balance

    balance = await get_balance(db, user.id)
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "balance": balance, "active": "home"}
    )


@router.get("/api-keys")
async def dashboard_api_keys(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    from ..api_keys.service import list_keys

    keys = await list_keys(db, user.id)
    return templates.TemplateResponse(
        request, "api_keys.html", {"user": user, "keys": keys, "active": "keys"}
    )


@router.get("/usage")
async def dashboard_usage(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    """用量页：图表与明细由浏览器 JS 调 /usage、/usage/logs 拉取。"""
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "usage.html", {"user": user, "active": "usage"}
    )


@router.get("/billing")
async def dashboard_billing(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    from ..billing.service import get_balance, list_transactions

    balance = await get_balance(db, user.id)
    txs, total = await list_transactions(db, user.id, page=1, size=50)
    return templates.TemplateResponse(
        request,
        "billing.html",
        {"user": user, "balance": balance, "transactions": txs, "active": "billing"},
    )


@router.get("/docs")
async def dashboard_docs(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    """开发文档页：认证、端点、示例、错误码。"""
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "docs.html", {"user": user, "active": "docs"}
    )
