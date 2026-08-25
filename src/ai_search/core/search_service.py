"""共享搜索编排 —— FastAPI 端点与 MCP server 共用同一套逻辑。

把 main.py 原有的五步流程（检索 → 抓取 → 重排 → 可选摘要 → 裁剪）抽到这里，
组件单例也集中在此，确保两个入口共享同一套实例与配置。
"""

import logging

from ..config import Settings, get_settings
from ..extract.fetcher import Fetcher
from ..rerank.llm_reranker import LLMReranker
from ..schemas import SearchRequest, SearchResponse
from ..search.orchestrator import SearchOrchestrator

logger = logging.getLogger(__name__)

# 组件单例（get_settings 经 lru_cache 保证全局唯一）
settings: Settings = get_settings()
orchestrator = SearchOrchestrator(settings)
fetcher = Fetcher(settings)
reranker = LLMReranker(settings)


async def run_search(req: SearchRequest) -> SearchResponse:
    """搜索 → 抓取 → 重排 → (可选摘要) → 返回。

    检索源失败时抛出异常，由调用方决定如何呈现：
    - FastAPI 端点 → HTTPException(502)
    - MCP tool → fastmcp 自动转为 tool error response
    """
    logger.info("搜索请求: query=%r", req.query)

    # 1. 检索编排：取候选（召回量 = max_results*5，给重排留选择空间）
    candidates = await orchestrator.gather_candidates(
        req.query, max_results=req.max_results
    )

    if not candidates:
        return SearchResponse(query=req.query, answer=None, results=[])

    # 2. LLM 粗排：先对全部召回候选打分排序（此时多数无正文，靠标题+摘要片段）
    #    再对粗排后的 Top-N 抓取正文，避免按 SearXNG 原始顺序抓到不相关的 URL
    candidates = await reranker.rerank(req.query, candidates)

    # 3. 抓取正文：取粗排后的 Top-N URL
    top_urls = [c.url for c in candidates[: settings.fetch_top_n]]
    contents = await fetcher.fetch_batch(top_urls)
    for c in candidates:
        if c.url in contents and contents[c.url]:
            c.raw_content = contents[c.url]

    # 4. LLM 精排：有了正文后再打一次分，正文提升相关结果的可信度排序
    candidates = await reranker.rerank(req.query, candidates)
    candidates = candidates[: req.max_results]

    # 5. 可选：生成摘要
    answer = None
    if req.include_answer:
        answer = await reranker.generate_answer(req.query, candidates)

    # 6. 按需裁剪 raw_content
    if not req.include_raw_content:
        for c in candidates:
            c.raw_content = None

    return SearchResponse(query=req.query, answer=answer, results=candidates)
