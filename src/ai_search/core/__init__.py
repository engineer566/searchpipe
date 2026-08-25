"""核心编排层 —— FastAPI 端点与 MCP server 共用。"""

from .search_service import run_search

__all__ = ["run_search"]
