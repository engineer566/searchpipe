"""MCP server —— 把 /search 能力以 stdio tool 暴露给 MCP 客户端（Claude Code 等）。

方案 A：MCP 是同一产品的第二协议入口，与 HTTP /search 共享同一套商业管线
（鉴权 → 限流 → 输入审核 → 扣费 → 搜索 → 输出审核 + AI 标识 → 失败退款 → 用量日志）。
必须带 sp- API Key、按调用量扣积分，对齐 Tavily MCP 计费模型。

复用 core.search_service.run_search（管线零侵入），商业逻辑走协议无关核：
- auth.core.resolve_api_key
- billing.pipeline.charge_credits / refund_credits_for
- moderation.service.check_input / check_output
- rate_limit.service.check_rate_limit
- usage.service.record_usage

stdio transport 下 stdout 是协议流，日志必须走 stderr。
"""

import logging
import os
import sys
import time

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .auth.core import resolve_api_key
from .auth.errors import AuthError
from .billing.pipeline import ChargeResult, charge_credits, refund_credits_for
from .billing.service import InsufficientCreditsError
from .config import get_settings
from .core.search_service import run_search
from .db.base import async_session_factory
from .moderation.service import ModerationError, check_input, check_output
from .rate_limit.service import RateLimitExceeded, check_rate_limit
from .schemas import SearchRequest, SearchResponse
from .usage.service import record_usage
from .utils.logger import setup_logging

setup_logging(stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP("ai-search")


def _resolve_raw_key(api_key: str | None) -> str:
    """取本次调用用的 API Key：参数优先，其次 SEARCHPIPE_API_KEY 环境变量。

    mcp_require_api_key=True 时必须返回 sp- 开头的 key，否则抛 ToolError。
    """
    settings = get_settings()
    raw = (api_key or os.getenv("SEARCHPIPE_API_KEY") or "").strip()
    if not settings.mcp_require_api_key:
        return raw  # 本地 dev 旁路：允许空 key（裸调 run_search）
    if not raw.startswith("sp-"):
        raise ToolError(
            "缺少有效的 sp- API Key（传 api_key 参数或设 SEARCHPIPE_API_KEY 环境变量）"
        )
    return raw


@mcp.tool()
async def ai_search_search(
    query: str,
    max_results: int = 5,
    include_answer: bool = False,
    include_raw_content: bool = False,
    api_key: str | None = None,
) -> SearchResponse:
    """对给定查询执行 AI 搜索：SearXNG 检索 → 抓取正文 → LLM 重排 → 可选摘要。

    商业化管线（与 HTTP /search 一致）：鉴权 → 限流 → 输入审核 → 扣费 → 搜索
    → 输出审核 + AI 标识 → 失败退款 → 用量日志。按调用量扣积分。

    api_key：sp- 开头的 API Key。也可通过 SEARCHPIPE_API_KEY 环境变量提供（参数优先）。
    返回 Tavily 风格结构化结果（query / answer / results[]）。
    鉴权失败 / 余额不足 / 内容违规 / 检索失败时返回 tool error。
    """
    settings = get_settings()
    req = SearchRequest(
        query=query,
        max_results=max_results,
        include_answer=include_answer,
        include_raw_content=include_raw_content,
    )

    # 本地 dev 旁路：mcp_require_api_key=False 时裸调 run_search，不走商业管线
    if not settings.mcp_require_api_key:
        return await run_search(req)

    raw = _resolve_raw_key(api_key)

    t0 = time.monotonic()
    ctx = None  # 鉴权成功前无 ctx；finally 据此判是否记用量
    charge: ChargeResult | None = None
    status = "ok"
    error_msg: str | None = None

    try:
        # 1. 鉴权 + 限流 + 输入审核 + 扣费（同一 session，扣费提交）
        async with async_session_factory() as db:
            try:
                ctx = await resolve_api_key(raw, db)
            except AuthError as e:
                raise ToolError(f"API Key 无效或已吊销: {e}") from e

            try:
                await check_rate_limit(f"key:{raw[:8]}")
            except RateLimitExceeded as e:
                raise ToolError(str(e)) from e

            # 输入审核（命中违禁不扣费）
            try:
                await check_input(req.query)
            except ModerationError as e:
                raise ToolError(f"输入内容违规: {e.labels}") from e

            # 扣费（独立提交：搜索不持有 db 事务，镜像 /search 设计）
            try:
                charge = await charge_credits(db, ctx, req.search_depth)
                await db.commit()
            except InsufficientCreditsError as e:
                await db.rollback()
                raise ToolError(
                    f"积分不足：余额 {e.balance}，本次需要 {e.required}"
                ) from e
            except Exception:
                await db.rollback()
                raise

        # 2. 搜索（管线零侵入）
        try:
            resp = await run_search(req)
        except Exception as e:  # noqa: BLE001
            logger.error("MCP 检索失败: %s", e)
            # 退款
            async with async_session_factory() as db:
                await refund_credits_for(db, charge)
                await db.commit()
                charge = None  # 已退，标记防 finally 重复记费
            status = "error"
            error_msg = f"检索源失败: {e}"
            raise ToolError(f"检索源失败: {e}") from e

        # 3. 输出审核 + AI 标识（深度合成规定第16-17条）
        if resp.answer:
            try:
                await check_output(resp.answer)
            except ModerationError as e:
                async with async_session_factory() as db:
                    await refund_credits_for(db, charge)
                    await db.commit()
                    charge = None
                raise ToolError(f"输出内容违规: {e.labels}") from e
            resp.ai_generated = True

        return resp

    finally:
        # 4. 用量日志（独立 session，不阻塞、不影响响应；鉴权失败不记）
        if ctx is not None:
            latency_ms = int((time.monotonic() - t0) * 1000)
            # 成功路径记实际扣费；退款路径 charge 已置 None → 记 0
            credits = charge.cost if charge is not None else 0
            try:
                async with async_session_factory() as db:
                    await record_usage(
                        db,
                        user_id=ctx.user_id,
                        api_key_id=ctx.api_key_id,
                        query=req.query,
                        max_results=req.max_results,
                        search_depth=req.search_depth,
                        credits_consumed=credits,
                        latency_ms=latency_ms,
                        status=status,
                        error_msg=error_msg,
                    )
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("MCP 用量日志落库失败（不影响响应）")


def main() -> None:
    """stdio 入口（[project.scripts] 指向此处）。"""
    mcp.run()


if __name__ == "__main__":
    main()
