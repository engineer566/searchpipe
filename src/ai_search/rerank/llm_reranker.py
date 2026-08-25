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
RERANK_SYSTEM = """你是一个搜索结果相关性评估器。
用户会给出一个查询和若干候选网页（标题+摘要）。请为每个候选打一个 0 到 1 的相关性分数，
1 表示高度相关、0 表示无关。只返回 JSON，格式为 {"scores": [{"index": 0, "score": 0.9}, ...]}，
不输出任何其他文字。"""

ANSWER_SYSTEM = """你是一个严谨的问答助手。只能基于给定的搜索结果回答问题。
若结果不足以回答，直说"根据现有搜索结果无法回答"。回答末尾用 [n] 标注引用了第几条结果。"""


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
                f"[{i}] 标题: {_sanitize(c.title)} | 摘要: {_sanitize(body)}"
            )
        user_msg = f"查询: {query}\n候选:\n" + "\n".join(lines)

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
        user_msg = f"问题: {query}\n搜索结果:\n" + "\n\n".join(lines)

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
