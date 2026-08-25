"""内容审核提供方抽象 —— 阿里云内容安全。

ModerationProvider：
- check_text(text) -> ModerationResult(passed, labels)
输入审核（query）+ 输出审核（answer）双过滤（生成式AI办法第14条）。
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModerationResult:
    """审核结果。passed=False 表示命中违禁。"""

    passed: bool
    labels: list[str] = field(default_factory=list)
    detail: str | None = None


class ModerationProvider(ABC):
    """内容审核接口。"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def check_text(self, text: str) -> ModerationResult:
        """检测文本是否合规。"""
        ...


class AllowAllProvider(ModerationProvider):
    """默认放行（moderation_enabled=False 时用）。"""

    @property
    def name(self) -> str:
        return "allow-all"

    async def check_text(self, text: str) -> ModerationResult:
        return ModerationResult(passed=True)
