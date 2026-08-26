"""限流包 —— Redis 滑动窗口。

限流逻辑在 service.check_rate_limit，由 /search 端点（main.py）与 MCP tool
（mcp_server.py）显式调用，不再走中间件（中间件层拿不到 user.role，无法判 admin 豁免）。
middleware.py 保留 RateLimitMiddleware 类骨架以备它用，但当前未注册。
"""

from .middleware import RateLimitMiddleware
from .service import RateLimitExceeded, check_rate_limit

__all__ = ["RateLimitExceeded", "RateLimitMiddleware", "check_rate_limit"]
