"""共享搜索编排 —— FastAPI 端点与 MCP server 共用同一套逻辑。

把 main.py 原有的五步流程（检索 → 抓取 → 重排 → 可选摘要 → 裁剪）抽到这里，
组件单例也集中在此，确保两个入口共享同一套实例与配置。

提速措施：
- 结果缓存：相同 query+max_results+include_answer+search_depth 的请求在
  TTL 内直接返回 Redis 缓存（缓存统一存带正文的完整结果，命中后按请求
  include_raw_content 裁剪，故该参数不进 key）；Redis 故障自动降级为不缓存。
- 精排前裁剪：第二次 LLM 重排只对「抓取覆盖量与返回数两倍」以内的候选打分，
  控制 prompt 规模（粗排已决定抓取范围，精排只定最终序）。
"""

import hashlib
import json
import logging

from ..config import Settings, get_settings
from ..extract.fetcher import Fetcher
from ..rerank.llm_reranker import LLMReranker
from ..schemas import SearchRequest, SearchResponse
from ..search.orchestrator import SearchOrchestrator
from ..utils.cache import get_cache

logger = logging.getLogger(__name__)

# 组件单例（get_settings 经 lru_cache 保证全局唯一）
settings: Settings = get_settings()
orchestrator = SearchOrchestrator(settings)
fetcher = Fetcher(settings)
reranker = LLMReranker(settings)


def _cache_key(req: SearchRequest) -> str:
    """结果缓存 key：query + 影响结果内容的参数。

    不含 include_raw_content——缓存统一存带正文的完整结果，
    命中后按请求的 include_raw_content 裁剪，两种调用共享一份缓存。
    """
    raw = json.dumps(
        {
            "q": req.query,
            "n": req.max_results,
            "a": req.include_answer,
            "d": req.search_depth,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return "search:result:" + hashlib.sha256(raw.encode()).hexdigest()


async def _cache_get(key: str) -> SearchResponse | None:
    """读缓存。Redis 故障/数据损坏一律按未命中处理，绝不影响搜索。"""
    try:
        raw = await get_cache().get(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("结果缓存读取失败，按未命中处理: %s", e)
        return None
    if not raw:
        return None
    try:
        return SearchResponse.model_validate_json(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("结果缓存解析失败，按未命中处理: %s", e)
        return None


async def _cache_set(key: str, resp: SearchResponse) -> None:
    """写缓存。失败仅记日志，不影响响应。"""
    try:
        await get_cache().set(
            key, resp.model_dump_json(), ttl=settings.search_cache_ttl
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("结果缓存写入失败，忽略: %s", e)


def _trim_raw_content(resp: SearchResponse, include_raw_content: bool) -> SearchResponse:
    """按需裁剪 raw_content（缓存命中与新结果共用同一裁剪逻辑）。"""
    if not include_raw_content:
        for c in resp.results:
            c.raw_content = None
    return resp


async def run_search(req: SearchRequest) -> SearchResponse:
    """搜索 → 抓取 → 重排 → (可选摘要) → 返回。

    检索源失败时抛出异常，由调用方决定如何呈现：
    - FastAPI 端点 → HTTPException(502)
    - MCP tool → fastmcp 自动转为 tool error response
    """
    logger.info("搜索请求: query=%r", req.query)

    # 0. 结果缓存：命中直接返回（/search 端点扣费在缓存之前，命中不退款）
    cache_key = _cache_key(req) if settings.enable_cache else None
    if cache_key:
        cached = await _cache_get(cache_key)
        if cached is not None:
            logger.info("结果缓存命中: query=%r", req.query)
            return _trim_raw_content(cached, req.include_raw_content)

    # 1. 检索编排：取候选（召回量 = max_results*5，给重排留选择空间）
    candidates = await orchestrator.gather_candidates(
        req.query, max_results=req.max_results
    )

    if not candidates:
        resp = SearchResponse(query=req.query, answer=None, results=[])
        if cache_key:
            await _cache_set(cache_key, resp)
        return resp

    # 2. LLM 粗排：先对全部召回候选打分排序（此时多数无正文，靠标题+摘要片段）
    #    再对粗排后的 Top-N 抓取正文，避免按 SearXNG 原始顺序抓到不相关的 URL
    candidates = await reranker.rerank(req.query, candidates)

    # 3. 抓取正文：取粗排后的 Top-N URL
    top_urls = [c.url for c in candidates[: settings.fetch_top_n]]
    contents = await fetcher.fetch_batch(top_urls)
    for c in candidates:
        if c.url in contents and contents[c.url]:
            c.raw_content = contents[c.url]

    # 4. LLM 精排：有了正文后再打一次分，正文提升相关结果的可信度排序。
    #    只对头部候选精排（粗排已定抓取范围，长尾候选进不了最终 Top），
    #    控制 prompt 规模以缩短精排耗时。
    keep = max(settings.fetch_top_n, req.max_results * 2)
    candidates = await reranker.rerank(req.query, candidates[:keep])
    candidates = candidates[: req.max_results]

    # 5. 可选：生成摘要
    answer = None
    if req.include_answer:
        answer = await reranker.generate_answer(req.query, candidates)

    resp = SearchResponse(query=req.query, answer=answer, results=candidates)
    if cache_key:
        await _cache_set(cache_key, resp)

    # 6. 按需裁剪 raw_content（缓存里始终保留完整正文）
    return _trim_raw_content(resp, req.include_raw_content)
