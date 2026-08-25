"""协议无关限流 —— Redis 滑动窗口（ZSET 时间戳）。

HTTP 中间件（rate_limit.middleware）与 MCP tool（mcp_server.py）共用，
保证同一 API key 在两个协议下共享限流额度，不会因换协议绕过限流。
"""

import time

from ..config import get_settings
from ..utils.cache import get_cache

WINDOW_SEC = 60  # 1 分钟窗口


class RateLimitExceeded(Exception):
    """触发限流。HTTP 中间件转 429，MCP tool 转 ToolError。"""


async def check_rate_limit(identifier: str) -> None:
    """滑动窗口判定。允许 burst 突发。超限抛 RateLimitExceeded。

    identifier 格式与 HTTP 中间件一致（如 key:sp-xxxx），确保两端共享额度。
    """
    settings = get_settings()
    limit = settings.rate_limit_rpm
    cache = get_cache()
    key = f"ratelimit:{identifier}"
    now = time.time()
    window_start = now - WINDOW_SEC

    # 触发懒连接，拿到底层 Redis 客户端用原生 ZSET 命令做滑动窗口
    client = await cache._client()  # noqa: SLF001
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)  # 清旧
    pipe.zcard(key)  # 计数
    pipe.zadd(key, {str(now): now})  # 加本次
    pipe.expire(key, WINDOW_SEC + 5)  # 设过期防内存泄漏
    results = await pipe.execute()
    count = results[1]
    # burst：允许短时突发到 limit + burst
    if count > limit + settings.rate_limit_burst:
        raise RateLimitExceeded(f"请求过于频繁，每分钟限 {limit} 次")
