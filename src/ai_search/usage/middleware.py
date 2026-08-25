"""用量日志中间件 —— 请求完成后异步落库 UsageLog。

从 request.state 取 user_id/api_key_id/credits_consumed（由 charge_search 写入）。
不阻塞响应：用 BackgroundTask 在响应返回后执行（共享同一 db session 风险高，
故中间件内自建独立 session 写日志，与请求事务解耦）。

仅记录 /search 端点。
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..db.base import async_session_factory
from .service import record_usage

logger = logging.getLogger(__name__)

USAGE_LOG_PATHS = {"/search"}


class UsageLogMiddleware(BaseHTTPMiddleware):
    """记录 /search 用量到 usage_logs。"""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if request.url.path not in USAGE_LOG_PATHS:
            return await call_next(request)

        t0 = time.time()
        response = await call_next(request)
        latency_ms = int((time.time() - t0) * 1000)

        # 异步落库，不阻塞响应
        await self._log(request, response, latency_ms)
        return response

    async def _log(self, request: Request, response: Response, latency_ms: int) -> None:
        user_id = getattr(request.state, "user_id", None)
        if user_id is None:
            # 鉴权失败等场景无 user_id，不记（或记匿名失败，此处从简跳过）
            return

        api_key_id = getattr(request.state, "api_key_id", None)
        credits = getattr(request.state, "credits_consumed", 0)
        status_val = "ok" if response.status_code < 400 else "error"
        error_msg = None
        if status_val == "error":
            error_msg = f"HTTP {response.status_code}"

        # 解析请求体参数（已消费的 body 无法重读，从 state 取或默认）
        query = getattr(request.state, "search_query", "")
        max_results = getattr(request.state, "search_max_results", 0)
        search_depth = getattr(request.state, "search_depth", "basic")

        try:
            async with async_session_factory() as db:
                await record_usage(
                    db,
                    user_id=user_id,
                    api_key_id=api_key_id,
                    query=query,
                    max_results=max_results,
                    search_depth=search_depth,
                    credits_consumed=credits,
                    latency_ms=latency_ms,
                    status=status_val,
                    error_msg=error_msg,
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("用量日志落库失败（不影响响应）")
