"""LLM 重排 + 摘要层 —— OpenAI 兼容接口（DeepSeek 等）。

职责：
1. 重排：对候选结果批量打相关性分数 (0-1)，按分排序。
2. 摘要：可选，基于结果生成 LLM answer。

安全：对抓取内容做最小 prompt-injection 防护（截断 + 标记为不可信数据）。
"""

import json
import logging
import re

from openai import AsyncOpenAI

from ..config import Settings
from ..schemas import SearchResult

logger = logging.getLogger(__name__)

# 单条候选喂给 LLM 的最大字符数（防止注入/超长）
MAX_SNIPPET_CHARS = 1500
# 强制 JSON 输出的指令
RERANK_SYSTEM = """You are a search-result relevance evaluator.
Given a query and several candidate web pages (title + snippet), assign each candidate a
relevance score from 0 to 1, where 1 means highly relevant and 0 means irrelevant.
Return only JSON in the form {"scores": [{"index": 0, "score": 0.9}, ...]} — no other text."""

ANSWER_SYSTEM = """You are a rigorous answering assistant. Answer only based on the given
search results. If the results are insufficient, say so plainly. Cite sources at the end
of your answer using [n] markers referring to result numbers."""


def _sanitize(text: str) -> str:
    """最小 prompt-injection 防护：截断 + 去掉控制字符。"""
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.strip()[:MAX_SNIPPET_CHARS]


class LLMReranker:
    """用 LLM 对候选重排并可选生成摘要。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
        )
        self._model = settings.llm_model

    async def rerank(
        self, query: str, candidates: list[SearchResult]
    ) -> list[SearchResult]:
        """打分并按分数降序排序。LLM 失败时退回原始顺序。"""
        if not candidates:
            return candidates

        # 构造候选描述：正文优先（截短），缺失则回退到 SearXNG 摘要片段
        lines = []
        for i, c in enumerate(candidates):
            body = c.raw_content or c.content
            lines.append(
                f"[{i}] Title: {_sanitize(c.title)} | Snippet: {_sanitize(body)}"
            )
        user_msg = f"Query: {query}\nCandidates:\n" + "\n".join(lines)

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": RERANK_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content or "{}"
            # DeepSeek 偶尔把 JSON 包在 ```json ... ``` 里，先剥代码块围栏
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            data = json.loads(content)
            scores = {
                int(item["index"]): float(item["score"])
                for item in data.get("scores", [])
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LLM 重排失败，退回原始顺序: %s | 候选数=%d", e, len(candidates)
            )
            return candidates

        for i, c in enumerate(candidates):
            if i in scores:
                c.score = scores[i]

        candidates.sort(key=lambda c: c.score, reverse=True)
        logger.info(
            "重排完成，top score=%.3f (候选 %d, 返回分数 %d)",
            candidates[0].score if candidates else 0,
            len(candidates),
            len(scores),
        )
        return candidates

    async def generate_answer(
        self, query: str, results: list[SearchResult]
    ) -> str | None:
        """基于结果生成摘要。失败返回 None。"""
        if not results:
            return None

        lines = []
        for i, r in enumerate(results):
            # 用正文优先，没有则用摘要
            body = r.raw_content or r.content
            lines.append(f"[{i}] {_sanitize(r.title)}\n{_sanitize(body)}")
        user_msg = f"Question: {query}\nSearch results:\n" + "\n\n".join(lines)

        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": ANSWER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
            )
            answer = (resp.choices[0].message.content or "").strip()
            return answer or None
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 摘要生成失败: %s", e)
            return None
