"""限流中间件 —— 委托 rate_limit.service.check_rate_limit 做 Redis 滑动窗口。

按 request.state.user_id 或 API key 标识（取到哪个用哪个）。
仅对 /search 端点生效（路径白名单判断），超限返回 429 + Retry-After。

滑动窗口逻辑抽出至 service.py，HTTP 中间件与 MCP tool 共享，
保证同一 API key 在两个协议下共享限流额度。
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings
from .service import WINDOW_SEC, RateLimitExceeded, check_rate_limit

logger = logging.getLogger(__name__)

RATE_LIMIT_PATHS = {"/search"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis 滑动窗口限流。仅作用于 RATE_LIMIT_PATHS 中的路径。"""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        if request.url.path not in RATE_LIMIT_PATHS:
            return await call_next(request)

        identifier = self._identifier(request)
        if identifier is None:
            # 无标识（鉴权依赖会在更内层拦截），放行让鉴权处理 401
            return await call_next(request)

        try:
            await check_rate_limit(identifier)
        except RateLimitExceeded:
            settings = get_settings()
            logger.warning("限流触发 identifier=%s path=%s", identifier, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many requests, limit is {settings.rate_limit_rpm} per minute"},
                headers={"Retry-After": str(WINDOW_SEC)},
            )

        return await call_next(request)

    def _identifier(self, request: Request) -> str | None:
        """取限流标识：优先已解析的 user_id/api_key_id（中间件在鉴权后执行才有），

        但鉴权是 Depends（端点级），中间件先于端点执行，故此处从请求头自行解析 token/key 前缀。
        """
        # 中间件先于 Depends 执行，request.state 此时无 user_id；
        # 改为从 Authorization/X-API-Key 头提取稳定标识
        auth = request.headers.get("authorization", "")
        xkey = request.headers.get("x-api-key", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            if token.startswith("sp-"):
                return f"key:{token[:8]}"  # API key 用前缀
            # JWT：用 token 前 16 位作标识（同一 token 短期内稳定；不同 token 不同）
            return f"jwt:{token[:16]}"
        if xkey:
            xkey = xkey.strip()
            if xkey.startswith("sp-"):
                return f"key:{xkey[:8]}"
            return f"key:{xkey[:8]}"
        return None
