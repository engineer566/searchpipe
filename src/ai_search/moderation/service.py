"""审核服务 —— 输入 + 输出双过滤。

- check_input(query)：/search 前置，命中违禁抛 ModerationError（端点转 400）。
- check_output(answer)：run_search 返回后、响应前，命中违禁同样拦截。
moderation_enabled=False 时全放行（内测期默认关，上线前必开）。
"""

import logging

from ..config import get_settings
from .aliyun import AliyunModerationProvider
from .provider import AllowAllProvider, ModerationProvider, ModerationResult

logger = logging.getLogger(__name__)


class ModerationError(Exception):
    """内容违规。由端点转 HTTP 400。"""

    def __init__(self, labels: list[str], stage: str = "input") -> None:
        self.labels = labels
        self.stage = stage
        super().__init__(f"{stage} content violation: {labels}")


_provider: ModerationProvider | None = None


def get_provider() -> ModerationProvider:
    """按配置返回审核提供方单例。enabled=False 用 AllowAllProvider。"""
    global _provider
    if _provider is not None:
        return _provider

    s = get_settings()
    if not s.moderation_enabled:
        _provider = AllowAllProvider()
    elif s.moderation_provider == "aliyun":
        _provider = AliyunModerationProvider()
    else:
        _provider = AllowAllProvider()
    return _provider


async def check_input(query: str) -> ModerationResult:
    """审核搜索输入。命中违禁抛 ModerationError。"""
    result = await get_provider().check_text(query)
    if not result.passed:
        raise ModerationError(result.labels, stage="input")
    return result


async def check_output(answer: str) -> ModerationResult:
    """审核 LLM 输出。命中违禁抛 ModerationError。"""
    result = await get_provider().check_text(answer)
    if not result.passed:
        raise ModerationError(result.labels, stage="output")
    return result
