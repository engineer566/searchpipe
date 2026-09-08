"""前端 Dashboard —— Jinja2 服务端渲染 + itsdangerous 签名 session cookie。

浏览器登录态：session cookie（签发/校验逻辑在 auth/session.py，与 API 鉴权兜底共享）。
程序调用走 JWT/API Key（不经此层）。

路由：
- GET /                          营销首页（site_router，公开）
- GET /robots.txt                搜索引擎爬虫规则
- GET /sitemap.xml               站点地图
- GET /terms                     服务条款页（site_router，公开）
- GET /dashboard/auth?token=xxx    OAuth/JWT 登录后落地：把 token 换成 session cookie → 302 到 /dashboard
- GET /dashboard/login             登录页（仅邮箱密码；第三方登录前端不开放）
- POST /dashboard/login            邮箱密码登录 → 设 cookie → 302 /dashboard
- GET /dashboard/register          注册页
- POST /dashboard/register         邮箱注册 → 送免费额度 → 设 cookie → 302 /dashboard
- GET /dashboard/forgot-password   忘记密码页
- POST /dashboard/forgot-password  发重置邮件（统一话术防枚举）
- GET /dashboard/reset-password    重置密码页（预检 token）
- POST /dashboard/reset-password   校验 token 改密 → 302 登录页
- GET /dashboard/logout            清 cookie → 302 /dashboard/login
- GET /dashboard                   主面板（余额/用量/快速搜索）
- GET /dashboard/api-keys          API Key 管理
- GET /dashboard/usage             用量统计
- GET /dashboard/billing           充值/流水
- GET /dashboard/docs              开发文档
"""

import logging
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from ..auth.jwt_handler import decode_token
from ..auth.password import hash_password, verify_password
from ..auth.password_reset import (
    consume_reset_token,
    peek_reset_token,
    request_password_reset,
)
from ..auth.session import (
    clear_session_cookie,
    read_session_cookie,
    set_session_cookie,
)
from ..db.models import User, UserRole, UserStatus
from ..db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
site_router = APIRouter(tags=["site"])

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _get_base_url() -> str:
    """从配置读取对外基址。"""
    from ..config import get_settings

    return get_settings().app_base_url.rstrip("/")


def _render_with_base(
    request: Request, template: str, context: dict | None = None
) -> object:
    """渲染模板并注入 base_url（供 SEO canonical/og:url 使用）。"""
    ctx = dict(context or {})
    ctx.setdefault("base_url", _get_base_url())
    return templates.TemplateResponse(request, template, ctx)


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
    return _render_with_base(request, "landing.html", {})


# ---------- SEO ----------


@site_router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> str:
    """搜索引擎爬虫规则：允许收录公开页，禁止后台与 API。"""
    base = _get_base_url()
    return (
        f"User-agent: *\n"
        f"Allow: /\n"
        f"Disallow: /dashboard\n"
        f"Disallow: /admin\n"
        f"Disallow: /api\n"
        f"Disallow: /auth\n"
        f"Disallow: /mcp\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@site_router.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml() -> str:
    """站点地图：列出公开可索引的页面。"""
    base = _get_base_url()
    urls = [
        f"{base}/",
        f"{base}/dashboard/login",
        f"{base}/dashboard/register",
        f"{base}/dashboard/docs",
    ]
    url_entries = "\n".join(
        f"  <url>\n"
        f"    <loc>{url}</loc>\n"
        f"    <changefreq>weekly</changefreq>\n"
        f"    <priority>{'1.0' if url == base + '/' else '0.6'}</priority>\n"
        f"  </url>"
        for url in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{url_entries}\n"
        "</urlset>"
    )


@site_router.get("/terms")
async def terms_page(request: Request) -> object:
    """服务条款页（公开可访问）。"""
    return templates.TemplateResponse(request, "terms.html", {})


# ---------- 登录/注册入口 ----------


def _render_login(
    request: Request, error: str | None = None, email: str = "", info: str | None = None
) -> object:
    """渲染登录页（可带错误/提示与邮箱回填）。"""
    return templates.TemplateResponse(
        request, "login.html", {"error": error, "email": email, "info": info}
    )


def _render_register(request: Request, error: str | None = None, email: str = "") -> object:
    """渲染注册页（可带错误与邮箱回填）。"""
    return templates.TemplateResponse(
        request, "register.html", {"error": error, "email": email}
    )


def _validate_email(email: str) -> str | None:
    """校验邮箱格式，返回错误文案或 None。"""
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return "邮箱格式不正确"
    return None


def _validate_password(password: str, password_confirm: str | None = None) -> str | None:
    """校验密码强度（≥8 位）与确认密码一致性，返回错误文案或 None。"""
    if len(password) < 8:
        return "密码至少 8 位"
    if len(password) > 128:
        return "密码最长 128 位"
    if password_confirm is not None and password != password_confirm:
        return "两次输入的密码不一致"
    return None


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
async def login_page(request: Request, reset: int = 0) -> object:
    """登录页（仅邮箱密码）。?reset=1 时显示「密码已重置」提示。"""
    info = "密码已重置，请使用新密码登录" if reset else None
    return _render_login(request, info=info)


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    agree_terms: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> object:
    """邮箱密码登录 → 设 cookie → /dashboard。失败重渲染登录页并提示。

    控制台面向真实用户，显式区分「邮箱未注册」与「密码错误」；
    API 层（/auth/login）仍返回统一 401 防脚本枚举。
    """
    email = email.strip().lower()
    if agree_terms != "on":
        return _render_login(request, error="请先阅读并同意《服务条款》", email=email)
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if not user:
        return _render_login(request, error="该邮箱未注册，请先注册", email=email)
    if not verify_password(password, user.password_hash):
        return _render_login(request, error="密码错误，请重新输入", email=email)
    if user.status != UserStatus.ACTIVE.value:
        return _render_login(request, error="账号已被停用，请联系客服", email=email)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(resp, str(user.id))
    return resp


@router.get("/register")
async def register_page(request: Request) -> object:
    """注册页（仅邮箱密码）。"""
    return _render_register(request)


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    agree_terms: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> object:
    """邮箱注册 → 送免费额度 → 设 cookie → /dashboard。失败重渲染注册页并提示。"""
    email = email.strip().lower()
    if agree_terms != "on":
        return _render_register(request, error="请先阅读并同意《服务条款》", email=email)
    if err := _validate_email(email):
        return _render_register(request, error=err, email=email)
    if err := _validate_password(password, password_confirm):
        return _render_register(request, error=err, email=email)

    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        return _render_register(request, error="该邮箱已注册，请直接登录", email=email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=UserRole.USER.value,
        status=UserStatus.ACTIVE.value,
    )
    db.add(user)
    try:
        await db.flush()
        # 惰性导入防循环依赖（billing.service 依赖鉴权层）
        from ..billing.service import grant_credits

        from ..config import get_settings

        await grant_credits(
            db,
            user_id=user.id,
            amount=get_settings().free_tier_credits,
            tx_type="grant",
            remark="内测免费额度",
        )
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
    except IntegrityError:
        # 并发重复注册竞态：唯一索引兜底
        await db.rollback()
        return _render_register(request, error="该邮箱已注册，请直接登录", email=email)

    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(resp, str(user.id))
    return resp


# ---------- 忘记密码 / 重置密码 ----------


@router.get("/forgot-password")
async def forgot_password_page(request: Request) -> object:
    """忘记密码页。"""
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": False})


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> object:
    """发重置邮件。无论邮箱是否注册都展示同一话术（防枚举）。"""
    await request_password_reset(db, email)
    return templates.TemplateResponse(
        request, "forgot_password.html", {"sent": True, "email": email.strip().lower()}
    )


@router.get("/reset-password")
async def reset_password_page(
    request: Request, token: str = Query(...)
) -> object:
    """重置密码页：预检 token 有效性，无效直接提示链接失效。"""
    valid = (await peek_reset_token(token)) is not None
    return templates.TemplateResponse(
        request, "reset_password.html", {"token": token, "invalid": not valid}
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> object:
    """校验 token 改密 → 302 登录页（带重置成功提示）。"""
    if err := _validate_password(password, password_confirm):
        return templates.TemplateResponse(
            request, "reset_password.html", {"token": token, "error": err}
        )
    user_id = await consume_reset_token(token)
    user = await db.get(User, user_id) if user_id else None
    if not user:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"token": token, "invalid": True},
        )
    user.password_hash = hash_password(password)
    await db.commit()
    return RedirectResponse(
        url="/dashboard/login?reset=1", status_code=status.HTTP_303_SEE_OTHER
    )


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


@router.get("/feedback")
async def dashboard_feedback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> object:
    """反馈工单页：提交表单 + 我的工单列表。"""
    user = await _user_from_session(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request, "feedback.html", {"user": user, "active": "feedback"}
    )
