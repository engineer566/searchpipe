"""FastAPI 入口 —— 汇聚全部商业化后端模块。

/search 端点依赖链（端点内显式调用，顺序明确）：
  1. get_current_user_or_api_key  鉴权（JWT 或 API Key）
  2. moderate_input               输入审核（命中违禁 400，不扣费）
  3. check_rate_limit             限流（admin/owner 豁免，超限 429）
  4. charge_search                扣费（余额不足 402；admin 已在 charge_credits 短路）
  5. run_search                   搜索管线（零侵入）
  6. check_output                 输出审核 + AI 标识
  失败 → refund_search 退还（幂等）
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .admin import router as admin_router
from .agent_setup import render_skill_md
from .api_keys import router as api_keys_router
from .auth import router as auth_router
from .auth.dependencies import AuthContext, get_current_user_or_api_key
from .billing.dependencies import charge_search, refund_search
from .billing.routes import router as billing_router
from .billing.service import InsufficientCreditsError
from .core.search_service import run_search
from .dashboard import router as dashboard_router, site_router
from .db.base import dispose_engine
from .db.models import UserRole
from .feedback import router as feedback_router
from .moderation.dependencies import moderate_input
from .moderation.service import ModerationError, check_output
from .mcp_server import mcp as mcp_server_obj
from .payments import router as payments_router
from .rate_limit.service import WINDOW_SEC, RateLimitExceeded, check_rate_limit
from .schemas import SearchRequest, SearchResponse
from .usage import UsageLogMiddleware
from .usage.routes import router as usage_router
from .utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "dashboard" / "static"

# MCP streamable-http ASGI 子应用 —— 必须在构造 lifespan 前创建，
# 以便把它的 lifespan 合并进 FastAPI 的 lifespan（FastMCP 的 SessionManager
# 需在启动时初始化 task group，否则 /mcp 请求报 "Task group is not initialized"）。
_mcp_app = mcp_server_obj.http_app(transport="streamable-http", path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("SearchPipe 启动")
    # 进入 MCP 子应用的 lifespan（初始化 streamable-http session manager）
    async with _mcp_app.lifespan(_mcp_app):
        yield
    logger.info("SearchPipe 关闭，释放 DB engine")
    await dispose_engine()


app = FastAPI(title="SearchPipe", version="0.2.0", lifespan=lifespan)

# 中间件（外→内）：CORS → 用量日志
# 限流已下沉到 /search 端点内（需 ctx.user.role 判 admin 豁免），故不再注册中间件。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(UsageLogMiddleware)

# 静态资源（控制台 CSS/JS）
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# MCP 远程端点（streamable-http）——与 /search 共用同一进程/容器，
# 复用 mcp_server.py 的 ai_search_search tool（含完整商业管线：鉴权→扣费→…）。
# 客户端（Claude Code）经 https://searchpipe.tech/mcp 连接，Authorization 头传 sp- key。
app.mount("/mcp", _mcp_app)

# 路由器
app.include_router(site_router)        # / 营销首页
app.include_router(auth_router)        # /auth
app.include_router(api_keys_router)    # /api-keys
app.include_router(billing_router)     # /billing
app.include_router(payments_router)    # /payments
app.include_router(usage_router)       # /usage
app.include_router(feedback_router)    # /feedback
app.include_router(admin_router)       # /admin
app.include_router(dashboard_router)   # /dashboard


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/agent-setup/SKILL.md", response_class=PlainTextResponse)
async def agent_setup_skill() -> str:
    """一句话 MCP 配置指南：AI Agent 自动配置 SearchPipe MCP 的 SKILL.md。"""
    return render_skill_md()


@app.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    request: Request,
    ctx: AuthContext = Depends(get_current_user_or_api_key),
) -> SearchResponse:
    """搜索 → 抓取 → 重排 → (可选摘要) → 输出审核 → 返回。

    依赖链：鉴权 → 输入审核 → 限流 → 扣费 → 搜索 → 输出审核/AI标识。
    admin/owner 豁免限流与扣费。搜索失败退款（幂等）。
    """
    # 把搜索参数挂 state，供 UsageLog 中间件记录
    request.state.search_query = req.query
    request.state.search_max_results = req.max_results
    request.state.search_depth = req.search_depth

    # 1. 输入审核（命中违禁不扣费）
    await moderate_input(req.query)

    # 2. 限流（admin/owner 豁免；超限 429）
    if not UserRole.is_admin(ctx.user.role):
        identifier = (
            f"key:{ctx.api_key_id}" if ctx.api_key_id else f"user:{ctx.user_id}"
        )
        try:
            await check_rate_limit(identifier)
        except RateLimitExceeded as e:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                str(e),
                headers={"Retry-After": str(WINDOW_SEC)},
            ) from e

    # 3. 扣费（需独立 session：扣费要提交，搜索不持有 db 事务；
    #    admin 已在 charge_credits 内短路为 cost=0）
    from .db.base import async_session_factory

    async with async_session_factory() as db:
        try:
            await charge_search(request, db, ctx, req.search_depth)
            await db.commit()
        except InsufficientCreditsError as e:
            await db.rollback()
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"积分不足：余额 {e.balance}，本次需要 {e.required}",
            ) from e
        except Exception:
            await db.rollback()
            raise

    # 4. 搜索（管线零侵入）
    try:
        resp = await run_search(req)
    except Exception as e:  # noqa: BLE001
        logger.error("检索失败: %s", e)
        # 退款
        async with async_session_factory() as db:
            await refund_search(request, db)
            await db.commit()
        raise HTTPException(status_code=502, detail=f"检索源失败: {e}") from e

    # 5. 输出审核 + AI 标识（深度合成规定第16-17条）
    if resp.answer:
        try:
            await check_output(resp.answer)
        except ModerationError as e:
            # 输出违规：退款（已扣费）
            async with async_session_factory() as db:
                await refund_search(request, db)
                await db.commit()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"输出内容违规: {e.labels}"
            ) from e
        resp.ai_generated = True

    return resp
