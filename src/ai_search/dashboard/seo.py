"""SEO 基建 —— 站点公开页清单、元信息/结构化数据构造、robots/sitemap、站点图标。

设计原则（2026-09-13 SEO 整改）：
1. **单一事实来源**：`PUBLIC_PAGES` 是「可索引公开页」的唯一清单，robots.txt、
   sitemap.xml 与测试都从它派生，杜绝「sitemap 里列了 robots 屏蔽的 URL」这类
   自相矛盾（整改前 sitemap 就列了 `/dashboard/docs`：既被 Disallow，又需要登录）。
2. **结构化数据集中在 Python 侧生成**，模板只负责 `{{ xxx_jsonld | safe }}` 注入，
   避免在 17 个模板里手写 JSON-LD。
3. **canonical / og:url 一律用配置里的 `APP_BASE_URL`**（而不是 `request.base_url`），
   防止同页多域名（www/HTTP/IP 直连）时产出互斥 canonical。
"""

import json

from fastapi import APIRouter
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pathlib import Path

from ..config import get_settings

router = APIRouter(tags=["seo"])

_STATIC_DIR = Path(__file__).parent / "static"

# ---------- 站点常量 ----------

SITE_NAME = "SearchPipe"
SITE_TAGLINE = "AI search API for agents that can read the Chinese web"
SITE_ONE_LINER = "AI search API and remote MCP server for AI agents"
SITE_DESCRIPTION = (
    "SearchPipe is an AI search API and remote MCP server built for AI agents: "
    "one call runs multi-engine retrieval, full-text fetching, LLM reranking and "
    "optional summarization, returning structured results with relevance scores. "
    "A Tavily alternative that can search the Chinese web."
)
SITE_KEYWORDS = (
    "AI search API,Tavily alternative,MCP search server,search API for agents,"
    "web search API,Chinese web search,AI agent search,real-time search API,"
    "search MCP server,Claude Code search"
)
OG_IMAGE_PATH = "/static/og-image.png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630

# 内容最后更新日（改公开页文案时同步 bump，用于 sitemap lastmod）
SITE_LAST_MODIFIED = "2026-09-14"

# 可索引公开页：(路径, sitemap 权重, 更新频率)
PUBLIC_PAGES: tuple[tuple[str, str, str], ...] = (
    ("/", "1.0", "weekly"),
    ("/docs", "0.9", "weekly"),
    ("/mcp-server", "0.9", "weekly"),
    ("/pricing", "0.8", "weekly"),
    ("/faq", "0.7", "monthly"),
    ("/terms", "0.3", "yearly"),
    ("/privacy", "0.3", "yearly"),
)

# robots.txt / X-Robots-Tag 共用：需屏蔽的私有路径前缀
PRIVATE_PATH_PREFIXES: tuple[str, ...] = (
    "/dashboard",
    "/admin",
    "/auth",
    "/api-keys",
    "/billing",
    "/payments",
    "/usage",
    "/feedback",
    "/messages",
    "/search",
    # 注意写 "/mcp/"（带尾斜杠）：裸 "/mcp" 会把公开页 /mcp-server 一起屏蔽
    "/mcp/",
    "/healthz",
    "/api-docs",
    "/openapi.json",
    "/agent-setup",
)


def is_private_path(path: str) -> bool:
    """是否属于「需登录/内部/接口」路径（robots.txt 与 X-Robots-Tag 共用判定）。

    等价于 robots 前缀匹配，但额外容忍尾斜杠差异：`/mcp` 与 `/mcp/xxx` 都命中
    `/mcp/` 规则，而公开页 `/mcp-server` 不受影响。
    """
    for prefix in PRIVATE_PATH_PREFIXES:
        if path == prefix or path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False

# 定价（与 landing.html / pricing.html 展示价保持一致，测试会交叉校验）
# 出海定价（USD）：充值 $5→1,000 / $10→2,100 / $20→4,400 credits；
# 订阅 Starter $4.99→1,000/月、Pro $9.99→3,000/月、Max $19.99→10,000/月；$0.005 = 1 credit。
RECHARGE_TIERS = (("$5", "1,000 credits"), ("$10", "2,100 credits"), ("$20", "4,400 credits"))
SUBSCRIPTION_PLANS = (
    ("Starter", "4.99", "4.99", "1,000 credits / month"),
    ("Pro", "9.99", "9.99", "3,000 credits / month"),
    ("Max", "19.99", "19.99", "10,000 credits / month"),
)
RECHARGE_RATE = "0.005"  # $0.005 = 1 credit


# ---------- URL 工具 ----------


def get_base_url() -> str:
    """对外站点基址（配置驱动：生产 https://searchpipe.tech，测试服为 IP:8001）。"""
    return get_settings().app_base_url.rstrip("/")


def canonical_url(path: str = "/") -> str:
    """相对路径 → 规范绝对 URL（canonical / og:url / JSON-LD 统一走这里）。

    首页固定为 `{base}/`，其余页面原样拼接，不做尾斜杠改写。
    """
    base = get_base_url()
    if path in ("", "/"):
        return base + "/"
    return base + (path if path.startswith("/") else "/" + path)


def jsonld_script(payload: dict | list) -> str:
    """结构化数据 → 可安全内联进 <script type="application/ld+json"> 的字符串。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 防止内容里出现 </script> 提前闭合标签
    return text.replace("</", "<\\/")


def _organization_node(base: str) -> dict:
    return {
        "@type": "Organization",
        "@id": f"{base}/#organization",
        "name": SITE_NAME,
        "url": f"{base}/",
        "logo": {
            "@type": "ImageObject",
            "url": f"{base}/static/icon-512.png",
            "width": 512,
            "height": 512,
        },
        "description": SITE_DESCRIPTION,
        "areaServed": "Worldwide",
    }


def _website_node(base: str) -> dict:
    return {
        "@type": "WebSite",
        "@id": f"{base}/#website",
        "name": SITE_NAME,
        "alternateName": "SearchPipe AI Search API",
        "url": f"{base}/",
        "inLanguage": "en",
        "publisher": {"@id": f"{base}/#organization"},
    }


def _software_node(base: str) -> dict:
    return {
        "@type": "SoftwareApplication",
        "@id": f"{base}/#software",
        "name": SITE_NAME,
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Web (REST API / MCP Server)",
        "description": SITE_DESCRIPTION,
        "url": f"{base}/",
        "featureList": [
            "Multi-engine aggregated search (SearXNG)",
            "Web page full-text fetching and cleaning",
            "LLM relevance reranking (0–1 score)",
            "AI summaries with citation markers",
            "Remote MCP Server (streamable-http)",
            "REST API (POST /search)",
            "Chinese web coverage",
        ],
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "USD",
            "description": "Free tier: 1,000 credits per month on sign-up, full API and MCP access",
            "url": f"{base}/pricing",
        },
        "provider": {"@id": f"{base}/#organization"},
    }


def breadcrumb_ld(items: list[tuple[str, str]]) -> dict:
    """面包屑：items = [(名称, 路径), ...]，第一项通常是首页。"""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx,
                "name": name,
                "item": canonical_url(path),
            }
            for idx, (name, path) in enumerate(items, start=1)
        ],
    }


def landing_ld() -> str:
    """首页结构化数据：Organization + WebSite + SoftwareApplication + FAQPage。"""
    base = get_base_url()
    return jsonld_script(
        {
            "@context": "https://schema.org",
            "@graph": [
                _organization_node(base),
                _website_node(base),
                _software_node(base),
                _faq_node(LANDING_FAQ),
            ],
        }
    )


def docs_ld() -> str:
    """开发文档页：TechArticle + 面包屑。"""
    base = get_base_url()
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "TechArticle",
                "headline": "SearchPipe Docs: AI Search API and MCP Integration",
                "description": "Full reference for the SearchPipe REST API (POST /search): parameters, response schema, curl / Python / JavaScript examples, error codes, and how to connect the remote MCP server.",
                "inLanguage": "en",
                "dateModified": SITE_LAST_MODIFIED,
                "mainEntityOfPage": canonical_url("/docs"),
                "author": {"@id": f"{base}/#organization"},
                "publisher": {"@id": f"{base}/#organization"},
                "about": {"@id": f"{base}/#software"},
            },
            breadcrumb_ld([("Home", "/"), ("Docs", "/docs")]),
        ]
    )


def mcp_page_ld() -> str:
    """MCP 接入指南：HowTo（步骤）+ 面包屑。"""
    base = get_base_url()
    steps = [
        ("Sign up and create an API key", "Register a SearchPipe account and create an sp- prefixed key on the API Keys page of the dashboard."),
        ("Build your MCP URL", "Embed the API key into the MCP endpoint: https://searchpipe.tech/mcp?api_key=sp-your-key."),
        ("Add it to your client", "For Claude Code run claude mcp add --transport http searchpipe \"<MCP URL>\"; for Cursor etc., add the URL under mcpServers in mcp.json."),
        ("Verify with your agent", "Ask your agent to run a live search with the ai_search_search tool and confirm it returns results with source links."),
    ]
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "HowTo",
                "name": "How to connect SearchPipe MCP search to Claude Code / Cursor",
                "description": "Four steps to add the SearchPipe remote MCP server to any MCP-compatible AI client and get real-time web search.",
                "inLanguage": "en",
                "totalTime": "PT3M",
                "mainEntityOfPage": canonical_url("/mcp-server"),
                "step": [
                    {
                        "@type": "HowToStep",
                        "position": i,
                        "name": name,
                        "text": text,
                        "url": canonical_url("/mcp-server") + f"#step-{i}",
                    }
                    for i, (name, text) in enumerate(steps, start=1)
                ],
                "publisher": {"@id": f"{base}/#organization"},
            },
            breadcrumb_ld([("Home", "/"), ("MCP Integration", "/mcp-server")]),
        ]
    )


def faq_page_ld() -> str:
    """FAQ 页：FAQPage + 面包屑。"""
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                **_faq_node(FAQ_ITEMS),
                "inLanguage": "en",
                "mainEntityOfPage": canonical_url("/faq"),
            },
            breadcrumb_ld([("Home", "/"), ("FAQ", "/faq")]),
        ]
    )


def pricing_page_ld() -> str:
    """定价页：Product + 各档 Offer + 面包屑。"""
    base = get_base_url()
    offers = [
        {
            "@type": "Offer",
            "name": f"{name} (monthly subscription, credits valid 30 days)",
            "price": price,
            "priceCurrency": "USD",
            "url": canonical_url("/pricing"),
            "availability": "https://schema.org/InStock",
        }
        for name, price, _original, _credits in SUBSCRIPTION_PLANS
    ]
    offers.append(
        {
            "@type": "Offer",
            "name": "Credit recharge (from $5, $0.005 = 1 credit, never expires)",
            "price": "5",
            "priceCurrency": "USD",
            "url": canonical_url("/pricing"),
            "availability": "https://schema.org/InStock",
        }
    )
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": f"{SITE_NAME} AI Search API / MCP Server",
                "description": "AI search API for agents: pay-as-you-go credits at $0.005 per credit. Recharged credits never expire; subscription credits are valid for 30 days. Free tier includes 1,000 credits per month.",
                "brand": {"@type": "Brand", "name": SITE_NAME},
                "category": "DeveloperApplication",
                "url": canonical_url("/pricing"),
                "offers": offers,
                "provider": {"@id": f"{base}/#organization"},
            },
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "SearchPipe Pricing: credit recharges and monthly subscriptions",
                "url": canonical_url("/pricing"),
                "inLanguage": "en",
                "isPartOf": {"@id": f"{base}/#website"},
            },
            breadcrumb_ld([("Home", "/"), ("Pricing", "/pricing")]),
        ]
    )


def terms_ld() -> str:
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "SearchPipe Terms of Service",
                "url": canonical_url("/terms"),
                "inLanguage": "en",
                "dateModified": SITE_LAST_MODIFIED,
            },
            breadcrumb_ld([("Home", "/"), ("Terms of Service", "/terms")]),
        ]
    )


def privacy_ld() -> str:
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "SearchPipe Privacy Policy",
                "url": canonical_url("/privacy"),
                "inLanguage": "en",
                "dateModified": SITE_LAST_MODIFIED,
            },
            breadcrumb_ld([("Home", "/"), ("Privacy Policy", "/privacy")]),
        ]
    )


def not_found_ld() -> str:
    return jsonld_script(
        {"@context": "https://schema.org", "@type": "WebPage", "name": "Page Not Found · SearchPipe"}
    )


# ---------- FAQ 内容（页面与结构化数据共用同一份，避免两边文案漂移） ----------

LANDING_FAQ: tuple[tuple[str, str], ...] = (
    (
        "What is SearchPipe?",
        "SearchPipe is an AI search API and remote MCP server for AI agents: a single call runs multi-engine retrieval, full-text fetching, LLM reranking and optional summarization, returning structured results with relevance scores that agents can consume directly.",
    ),
    (
        "How is SearchPipe different from calling a search engine directly?",
        "SearchPipe returns cleaned page content, relevance scores and optional answers — not raw search-result HTML that you have to parse. Agents can consume the response directly, and failed requests are automatically refunded.",
    ),
    (
        "Which AI clients are supported?",
        "Any MCP-compatible client with streamable-http support can connect — Claude Code and Cursor are tested. Self-built agents can call the REST API directly via POST /search.",
    ),
    (
        "Can SearchPipe search the Chinese web?",
        "Yes. SearchPipe aggregates multiple search engines and can retrieve and clean Chinese-language pages, making it a practical choice when your agents need coverage of both English and Chinese web content.",
    ),
    (
        "How does pricing work?",
        "Credits: $0.005 = 1 credit. A basic search costs 1 credit and an advanced search costs 2 credits. New accounts get 1,000 free credits per month. Recharged credits never expire; subscription credits are valid for 30 days.",
    ),
    (
        "How long does integration take?",
        "Copy the MCP link from the dashboard into your client config — typically 1–3 minutes. Agents can also read /agent-setup/SKILL.md to configure themselves automatically.",
    ),
)

FAQ_ITEMS: tuple[tuple[str, str], ...] = LANDING_FAQ + (
    (
        "Is the API key embedded in the MCP URL safe?",
        "The key lives in the client config file, so treat that file like a credential. Create a separate key for each agent client, and revoke it immediately in the dashboard if it leaks — a revoked key stops working at once.",
    ),
    (
        "Why is a search sometimes slow?",
        "A cold query runs the full pipeline — multi-engine retrieval, full-text fetching, LLM reranking — which usually takes a few seconds. Identical queries with identical parameters hit the cache (default TTL 300 seconds) and return in milliseconds.",
    ),
    (
        "How many results does one request return?",
        "Five by default; adjust with max_results (1–20). Results carry 0–1 relevance scores and are sorted from most to least relevant.",
    ),
    (
        "How is the answer summary generated?",
        "With include_answer enabled, the service generates a summary with citation markers based on the fetched page content, flagged with ai_generated=true.",
    ),
    (
        "Do failed searches cost credits?",
        "No. When upstream retrieval fails (502) or output moderation rejects the content (400), the charge is automatically refunded (idempotently). Only successful requests are billed.",
    ),
    (
        "Is there a rate limit?",
        "Yes: a sliding-window limiter (default 100 requests per minute, burst 20). Over-limit requests get a 429 with a Retry-After header. Contact us to raise the quota for high-volume use.",
    ),
    (
        "Do credits expire?",
        "Credits from recharges never expire. Credits from monthly subscriptions (including renewals and upgrades) are valid for 30 days from the moment they land and expire automatically if unused.",
    ),
)


def _faq_node(items: tuple[tuple[str, str], ...]) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in items
        ],
    }


# ---------- 站长平台验证 ----------


def verification_metas() -> list[tuple[str, str]]:
    """站长平台验证 meta（只渲染配置了的，未配置则完全不出现在 HTML 里）。

    支持 Google Search Console / Bing Webmaster，
    统一通过 .env 配置，避免改模板发版。
    """
    settings = get_settings()
    metas = [
        ("google-site-verification", settings.google_site_verification),
        ("msvalidate.01", settings.bing_site_verification),
    ]
    return [(name, value) for name, value in metas if value]


# ---------- llms.txt（面向 AI 检索/问答引擎的站点摘要） ----------


def build_llms_txt() -> str:
    """llms.txt：给 AI 客户端与问答引擎看的纯文本站点导航。

    这是 llms.txt 约定（https://llmstxt.org）的最小实现：站点定位 + 关键链接 +
    核心事实。传统搜索引擎之外，ChatGPT / Perplexity 这类引擎更依赖文本理解，
    Agent-first 产品值得提供一份机器友好的摘要。
    """
    base = get_base_url()
    lines = [
        f"# {SITE_NAME}",
        "",
        f"> {SITE_ONE_LINER}: one call runs multi-engine retrieval → full-text fetching → LLM reranking → optional summarization, "
        "returning structured results with relevance scores. Available as a remote MCP Server and a REST API. "
        "A Tavily alternative with Chinese web coverage.",
        "",
        "## Key pages",
        "",
        f"- [Home]({base}/): product positioning, capabilities and live demo",
        f"- [Docs]({base}/docs): POST /search parameters, response schema, examples and error codes",
        f"- [MCP integration]({base}/mcp-server): Claude Code / Cursor setup and troubleshooting",
        f"- [Pricing]({base}/pricing): ${RECHARGE_RATE} = 1 credit; recharges never expire, subscription credits valid 30 days",
        f"- [FAQ]({base}/faq): billing, rate limits, key safety and more",
        f"- [Terms of Service]({base}/terms): credits, refunds and acceptable use",
        f"- [Privacy Policy]({base}/privacy): data collection, processors and your rights",
        f"- [Agent self-setup guide]({base}/agent-setup/SKILL.md): a one-page onboarding doc for AI agents",
        "",
        "## Core facts",
        "",
        f"- Access: remote MCP Server (streamable-http, key can be embedded in the URL) and REST API `POST /search`",
        f"- Pricing: credit-based; basic search 1 credit, advanced search 2 credits; failed requests auto-refunded; free tier 1,000 credits/month",
        f"- Capabilities: multi-engine aggregated search, full-text fetching and cleaning, LLM relevance reranking (0–1), summaries with citations",
        f"- Chinese web coverage: can retrieve and clean Chinese-language pages",
        f"- Result cache: identical query and parameters hit the cache for 300 seconds by default",
        f"- Rate limit: sliding window, 100 requests/minute with burst 20 by default",
        "",
    ]
    return "\n".join(lines)


# ---------- robots.txt / sitemap.xml ----------


def build_robots_txt() -> str:
    """robots.txt：公开页放行、私有路径屏蔽，并声明 sitemap。"""
    lines = [
        "# SearchPipe robots.txt",
        "# Public, indexable pages are listed in /sitemap.xml",
        "User-agent: *",
        "Allow: /",
        "",
        "# The following prefixes require login / are internal / are API endpoints",
    ]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PATH_PREFIXES]
    lines += [
        "",
        "# AI search/answer engines are explicitly allowed on public pages",
        "User-agent: GPTBot",
        "Allow: /",
        "",
        "User-agent: ClaudeBot",
        "Allow: /",
        "",
        "User-agent: PerplexityBot",
        "Allow: /",
    ]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PATH_PREFIXES]
    lines += [
        "",
        f"Sitemap: {get_base_url()}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


def build_sitemap_xml() -> str:
    """sitemap.xml：仅列 PUBLIC_PAGES（可索引公开页），带 lastmod。"""
    entries = "\n".join(
        "  <url>\n"
        f"    <loc>{canonical_url(path)}</loc>\n"
        f"    <lastmod>{SITE_LAST_MODIFIED}</lastmod>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>"
        for path, priority, changefreq in PUBLIC_PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


# ---------- 路由 ----------


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> str:
    """爬虫规则（`text/plain`）。"""
    return build_robots_txt()


@router.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    """站点地图（必须是 XML Content-Type，否则爬虫按纯文本忽略）。"""
    return Response(
        content=build_sitemap_xml(),
        media_type="application/xml; charset=utf-8",
    )


@router.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt() -> str:
    """面向 AI 客户端/问答引擎的站点摘要（llms.txt 约定）。"""
    return build_llms_txt()


def _static_file(name: str, media_type: str) -> FileResponse:
    """站点图标类文件：长缓存（内容不变，改图换文件名）。"""
    return FileResponse(
        _STATIC_DIR / name,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico() -> FileResponse:
    """根路径 favicon（浏览器与搜索爬虫默认请求 /favicon.ico）。"""
    return _static_file("favicon.ico", "image/x-icon")


@router.get("/favicon.svg", include_in_schema=False)
async def favicon_svg() -> FileResponse:
    return _static_file("favicon.svg", "image/svg+xml")


@router.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon() -> FileResponse:
    """iOS 添加到主屏图标（根路径约定）。"""
    return _static_file("apple-touch-icon.png", "image/png")


@router.get("/og-image.png", include_in_schema=False)
async def og_image() -> FileResponse:
    """社交分享封面（og:image / twitter:image 指向的根路径别名）。"""
    return _static_file("og-image.png", "image/png")
