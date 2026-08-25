"""抓取清洗层 —— 并发抓取 URL 正文，用 trafilatura 提取干净文本。

三层策略（按站点特性分流，避免一刀切）：
1. 站点专用 API 适配器（GitHub / HuggingFace）——走官方 API 或 raw 文件，
   绕开 JS 渲染与反爬，直接拿到 Markdown 正文，成功率最高。
2. Reddit 等 .json API 被云主机 IP 硬封 403，不做适配；trafilatura 同样取不到，
   记为已知限制。
3. 其余站点走 trafilatura（纯 Python，无浏览器），JS 重页面会取空。
"""

import asyncio
import logging
import re

import httpx
import trafilatura

from ..config import Settings

logger = logging.getLogger(__name__)

# 单条正文最大字符数，防止超长内容撑爆 LLM 上下文
MAX_CONTENT_CHARS = 8000

# 真实浏览器 UA —— HuggingFace/GitHub 等站点会拒绝自曝机器人的 UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# 常见 403/反爬站点的 Referer 映射，带上对应 Referer 可显著降低被拒概率
REFERER_MAP = {
    "huggingface.co": "https://huggingface.co/",
    "github.com": "https://github.com/",
    "www.reddit.com": "https://www.reddit.com/",
    "reddit.com": "https://www.reddit.com/",
    "medium.com": "https://medium.com/",
}


def _build_headers(url: str) -> dict[str, str]:
    """根据 URL 域名补 Referer，其余用统一浏览器头。"""
    headers = dict(BROWSER_HEADERS)
    for host, ref in REFERER_MAP.items():
        if host in url:
            headers["Referer"] = ref
            break
    return headers


# ---------------------------------------------------------------------------
# 站点专用适配器：用官方 API / raw 文件绕开 JS 渲染与反爬
# ---------------------------------------------------------------------------

# 匹配 https://github.com/{owner}/{repo}（可带子路径，如 /blob/、/issues/）
_GITHUB_REPO_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/|$)"
)
# 匹配 https://huggingface.co/{org}/{model}（model 名可含连字符/数字）
_HF_MODEL_RE = re.compile(
    r"^https?://huggingface\.co/(?P<org>[^/]+)/(?P<model>[^/]+)(?:/|$)"
)


async def _fetch_github(url: str, client: httpx.AsyncClient) -> str | None:
    """GitHub 仓库页 → 抓 README。

    api.github.com/repos/{owner}/{repo}/readme 配 Accept: raw 直接返回 README 正文，
    无需 JS 渲染。仅处理仓库根页（子路径如 /blob/、/issues/ 不适配，回退通用路径）。
    """
    m = _GITHUB_REPO_RE.match(url)
    if not m:
        return None
    owner, repo = m.group("owner"), m.group("repo")
    # 子路径（/blob /tree /issues /pulls /actions 等）不走 README 适配
    rest = url[m.end():]
    if rest and not rest.startswith(("tree/", "blob/main", "blob/master")):
        return None
    api = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        resp = await client.get(
            api,
            headers={
                "Accept": "application/vnd.github.raw",
                "User-Agent": BROWSER_UA,
            },
        )
        if resp.status_code != 200:
            logger.info("GitHub README API %d: %s", resp.status_code, api)
            return None
        text = resp.text
        if not text.strip():
            return None
        logger.info("GitHub 适配命中: %s (%d bytes)", repo, len(text))
        return text
    except httpx.HTTPError as e:
        logger.debug("GitHub 适配失败 %s: %s", api, e)
        return None


async def _fetch_huggingface(url: str, client: httpx.AsyncClient) -> str | None:
    """HuggingFace 模型/数据集页 → 抓 raw README.md。

    huggingface.co/{org}/{model}/raw/main/README.md 直接返回模型卡 Markdown，
    比解析 JSON API 更干净、信息更全（实测 65KB 真正文 vs JSON 嵌套字段）。
    """
    m = _HF_MODEL_RE.match(url)
    if not m:
        return None
    org, model = m.group("org"), m.group("model")
    rest = url[m.end():]
    # 仅根页或 /raw 适配；/discussions、/tree、/resolve 等子路径回退
    if rest and not rest.startswith(("raw/", "blob/main", "blob/master")):
        return None
    raw_url = f"https://huggingface.co/{org}/{model}/raw/main/README.md"
    try:
        resp = await client.get(
            raw_url, headers={"User-Agent": BROWSER_UA}
        )
        if resp.status_code != 200:
            logger.info("HF raw README %d: %s", resp.status_code, raw_url)
            return None
        text = resp.text
        if not text.strip():
            return None
        logger.info("HuggingFace 适配命中: %s/%s (%d bytes)", org, model, len(text))
        return text
    except httpx.HTTPError as e:
        logger.debug("HuggingFace 适配失败 %s: %s", raw_url, e)
        return None


async def _site_adapter(url: str, client: httpx.AsyncClient) -> str | None:
    """按域名分流到站点专用适配器；不命中返回 None，由调用方回退 trafilatura。"""
    if "github.com" in url:
        return await _fetch_github(url, client)
    if "huggingface.co" in url:
        return await _fetch_huggingface(url, client)
    return None


def _strip_frontmatter(text: str) -> str:
    """去掉 Markdown/README 顶部的 YAML frontmatter（--- ... ---），减少噪声。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")


async def fetch_url_text(url: str, *, timeout: int = 15) -> str | None:
    """抓取单个 URL 并提取正文。失败返回 None。

    流程：站点专用适配器（GitHub/HF）→ 命中则直接返回 Markdown 正文；
    未命中则 httpx 抓 HTML → trafilatura 提取。

    网络层：优先 IPv4。部分云主机/容器环境 IPv6 不通但 DNS 优先返回 AAAA，
    导致 httpx 默认走 IPv6 后 ConnectError。固定 family=IPv4 可避开。
    """
    headers = _build_headers(url)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers=headers,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        ) as client:
            # 1) 站点专用适配器优先（API/raw，无 JS 依赖）
            adapted = await _site_adapter(url, client)
            if adapted:
                cleaned = _strip_frontmatter(adapted)
                return cleaned[:MAX_CONTENT_CHARS]

            # 2) 通用路径：抓 HTML → trafilatura
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        logger.info("抓取 %s 返回 HTTP %d，跳过", url, e.response.status_code)
        return None
    except httpx.HTTPError as e:
        logger.debug("抓取失败 %s: %s", url, e)
        return None

    # trafilatura 是同步库，丢到线程池避免阻塞事件循环
    extracted = await asyncio.to_thread(
        trafilatura.extract,
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )
    if not extracted:
        logger.info("正文提取为空（可能是 JS 渲染页面）: %s", url)
        return None
    return extracted[:MAX_CONTENT_CHARS]


class Fetcher:
    """并发抓取多个 URL 的正文。"""

    def __init__(self, settings: Settings) -> None:
        self._timeout = settings.request_timeout

    async def fetch_batch(
        self, urls: list[str], *, concurrency: int = 5
    ) -> dict[str, str | None]:
        """并发抓取，返回 {url: 正文或None}。"""
        sem = asyncio.Semaphore(concurrency)

        async def _one(u: str) -> tuple[str, str | None]:
            async with sem:
                return u, await fetch_url_text(u, timeout=self._timeout)

        pairs = await asyncio.gather(*[_one(u) for u in urls])
        results = dict(pairs)
        ok = sum(1 for v in results.values() if v)
        logger.info("抓取完成: %d/%d 成功", ok, len(urls))
        return results
