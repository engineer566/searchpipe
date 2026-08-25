"""检索编排层 —— 调 SearXNG、去重合并、取 Top-N 候选。

后续迭代可在此加入：多源并发、公共实例池兜底、降级链、缓存。
"""

import logging

from ..config import Settings
from ..schemas import SearchResult
from .searxng_client import SearXNGClient, SearXNGError

logger = logging.getLogger(__name__)


class SearchOrchestrator:
    """编排检索源，产出候选 SearchResult 列表（尚未抓正文/重排）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = SearXNGClient(settings)

    async def gather_candidates(
        self, query: str, *, max_results: int
    ) -> list[SearchResult]:
        """从 SearXNG 取候选并按 url 去重合并。

        召回量取 max_results*5（原 *3），给 LLM 重排更大选择空间——
        SearXNG 各引擎 score 尺度不一，靠召回广度对冲排序噪声。
        """
        try:
            raw = await self._client.search(query, max_results=max_results * 5)
        except SearXNGError:
            # MVP：单源失败直接抛出；后续迭代在此加降级链
            raise

        seen: dict[str, SearchResult] = {}
        for item in raw:
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            seen[url] = SearchResult(
                url=url,
                title=item.get("title", "") or "",
                content=item.get("content", "") or "",
                # SearXNG 自带 score 可能缺失/尺度不一，先置 0，交给 LLM 重排
                score=float(item.get("score") or 0.0),
            )

        candidates = list(seen.values())[:max_results * 5]
        logger.info(
            "去重后候选 %d 条 (query=%r)", len(candidates), query
        )
        return candidates
