"""SearXNG JSON API 客户端 —— 异步 httpx 封装。"""

import logging

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


class SearXNGError(Exception):
    """SearXNG 调用失败。"""


class SearXNGClient:
    """封装 SearXNG /search?format=json 调用。"""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.searxng_url.rstrip("/")
        self._timeout = settings.request_timeout
        # SearXNG 不直接吃代理头，出站代理在 settings.yml 配置；
        # 这里仅用于 httpx 客户端层面（如需独立代理可在此扩展）。
        self._proxy = settings.searxng_proxy

    async def search(
        self, query: str, *, max_results: int = 20, categories: str | None = None
    ) -> list[dict]:
        """调用 SearXNG 搜索，返回原始结果列表。

        每条结果至少含: url, title, content, engine, score。
        """
        params: dict = {
            "q": query,
            "format": "json",
            "pageno": 1,
        }
        if categories:
            params["categories"] = categories

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                proxy=self._proxy,
            ) as client:
                resp = await client.get(
                    f"{self._base_url}/search", params=params
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("SearXNG 返回错误状态: %s", e.response.status_code)
            raise SearXNGError(f"SearXNG HTTP {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("SearXNG 请求失败: %s", e)
            raise SearXNGError(f"SearXNG unreachable: {e}") from e

        results = data.get("results", [])
        logger.info("SearXNG 返回 %d 条结果 (query=%r)", len(results), query)
        return results[:max_results]
