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
SITE_TAGLINE = "让你的 AI Agent 联网"
SITE_ONE_LINER = "面向 AI Agent 的联网搜索 API 与远程 MCP Server"
SITE_DESCRIPTION = (
    "SearchPipe 是为 AI Agent 而生的联网搜索 API 与远程 MCP Server："
    "一条命令接入 Claude Code、Cursor 等 Agent，一次调用完成多引擎检索、正文抓取、"
    "LLM 重排与摘要，返回带相关性评分的结构化结果。"
)
SITE_KEYWORDS = (
    "AI搜索API,MCP Server,Claude Code 联网搜索,Cursor MCP 配置,"
    "AI Agent 联网,联网搜索 API,大模型联网,搜索 API,实时搜索"
)
OG_IMAGE_PATH = "/static/og-image.png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630

# 内容最后更新日（改公开页文案时同步 bump，用于 sitemap lastmod）
SITE_LAST_MODIFIED = "2026-09-10"

# 可索引公开页：(路径, sitemap 权重, 更新频率)
PUBLIC_PAGES: tuple[tuple[str, str, str], ...] = (
    ("/", "1.0", "weekly"),
    ("/docs", "0.9", "weekly"),
    ("/mcp-server", "0.9", "weekly"),
    ("/pricing", "0.8", "weekly"),
    ("/faq", "0.7", "monthly"),
    ("/terms", "0.3", "yearly"),
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
RECHARGE_TIERS = (("¥10", "350 积分"), ("¥20", "700 积分"), ("¥50", "1800 积分"), ("¥100", "5000 积分"))
SUBSCRIPTION_PLANS = (
    ("包月·基础", "9.99", "19.99", "1000 积分 / 30 天"),
    ("包月·进阶", "24.99", "49.99", "3000 积分 / 30 天"),
    ("包月·旗舰", "49.99", "99.99", "10000 积分 / 30 天"),
)
RECHARGE_RATE = "0.03"  # ¥0.03 = 1 积分


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
        "areaServed": "CN",
    }


def _website_node(base: str) -> dict:
    return {
        "@type": "WebSite",
        "@id": f"{base}/#website",
        "name": SITE_NAME,
        "alternateName": "SearchPipe 联网搜索 API",
        "url": f"{base}/",
        "inLanguage": "zh-CN",
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
            "多引擎聚合检索（SearXNG）",
            "网页正文抓取与清洗",
            "LLM 相关性重排（0–1 评分）",
            "带引用标注的 AI 摘要",
            "远程 MCP Server（streamable-http）",
            "REST API（POST /search）",
        ],
        "offers": {
            "@type": "Offer",
            "price": "0",
            "priceCurrency": "CNY",
            "description": "注册即送免费额度，含完整 API 与 MCP 能力",
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
                "headline": "SearchPipe 开发文档：联网搜索 API 与 MCP 接入",
                "description": "SearchPipe 的 REST API（POST /search）参数、响应结构与 MCP Server 接入方式，含 curl / Python / JavaScript 调用示例与错误码。",
                "inLanguage": "zh-CN",
                "dateModified": SITE_LAST_MODIFIED,
                "mainEntityOfPage": canonical_url("/docs"),
                "author": {"@id": f"{base}/#organization"},
                "publisher": {"@id": f"{base}/#organization"},
                "about": {"@id": f"{base}/#software"},
            },
            breadcrumb_ld([("首页", "/"), ("开发文档", "/docs")]),
        ]
    )


def mcp_page_ld() -> str:
    """MCP 接入指南：HowTo（步骤）+ 面包屑。"""
    base = get_base_url()
    steps = [
        ("注册并获取 API Key", "注册 SearchPipe 账号，在控制台「API Keys」页创建一个 sp- 开头的密钥。"),
        ("拼接 MCP 链接", "把 API Key 内嵌进 MCP 地址：https://searchpipe.tech/mcp?api_key=sp-你的密钥。"),
        ("写入客户端配置", "Claude Code 执行 claude mcp add --transport http searchpipe \"<MCP 链接>\"；Cursor 等在 mcp.json 的 mcpServers 中添加该 URL。"),
        ("让 Agent 验证", "在对话中要求 Agent 使用 ai_search_search 工具检索一个实时问题，确认返回带链接的结果。"),
    ]
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "HowTo",
                "name": "如何给 Claude Code / Cursor 接入远程 MCP 搜索服务",
                "description": "四步把 SearchPipe 远程 MCP Server 接入任意兼容 MCP 的 AI 客户端，获得实时联网搜索能力。",
                "inLanguage": "zh-CN",
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
            breadcrumb_ld([("首页", "/"), ("MCP 接入指南", "/mcp-server")]),
        ]
    )


def faq_page_ld() -> str:
    """FAQ 页：FAQPage + 面包屑。"""
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                **_faq_node(FAQ_ITEMS),
                "inLanguage": "zh-CN",
                "mainEntityOfPage": canonical_url("/faq"),
            },
            breadcrumb_ld([("首页", "/"), ("常见问题", "/faq")]),
        ]
    )


def pricing_page_ld() -> str:
    """定价页：Product + 各档 Offer + 面包屑。"""
    base = get_base_url()
    offers = [
        {
            "@type": "Offer",
            "name": f"{name}（30 天有效期）",
            "price": price,
            "priceCurrency": "CNY",
            "url": canonical_url("/pricing"),
            "availability": "https://schema.org/InStock",
        }
        for name, price, _original, _credits in SUBSCRIPTION_PLANS
    ]
    offers.append(
        {
            "@type": "Offer",
            "name": "积分充值（自定义金额，¥0.03 = 1 积分）",
            "price": "10",
            "priceCurrency": "CNY",
            "url": canonical_url("/pricing"),
            "availability": "https://schema.org/InStock",
        }
    )
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": f"{SITE_NAME} 联网搜索 API / MCP Server",
                "description": "面向 AI Agent 的联网搜索服务：按次计费积分制，支持积分充值（永久有效）与包月订阅（30 天有效期），注册即送免费额度。",
                "brand": {"@type": "Brand", "name": SITE_NAME},
                "category": "DeveloperApplication",
                "url": canonical_url("/pricing"),
                "offers": offers,
                "provider": {"@id": f"{base}/#organization"},
            },
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "SearchPipe 定价：积分充值与包月订阅",
                "url": canonical_url("/pricing"),
                "inLanguage": "zh-CN",
                "isPartOf": {"@id": f"{base}/#website"},
            },
            breadcrumb_ld([("首页", "/"), ("定价", "/pricing")]),
        ]
    )


def terms_ld() -> str:
    return jsonld_script(
        [
            {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": "SearchPipe 服务条款",
                "url": canonical_url("/terms"),
                "inLanguage": "zh-CN",
                "dateModified": SITE_LAST_MODIFIED,
            },
            breadcrumb_ld([("首页", "/"), ("服务条款", "/terms")]),
        ]
    )


def not_found_ld() -> str:
    return jsonld_script(
        {"@context": "https://schema.org", "@type": "WebPage", "name": "页面不存在 · SearchPipe"}
    )


# ---------- FAQ 内容（页面与结构化数据共用同一份，避免两边文案漂移） ----------

LANDING_FAQ: tuple[tuple[str, str], ...] = (
    (
        "SearchPipe 和直接用搜索引擎有什么不同？",
        "SearchPipe 面向程序与 AI Agent：一次调用返回的是清洗过的正文、相关性评分与可选摘要，"
        "而不是需要再解析的搜索结果页 HTML，Agent 可以直接消费。",
    ),
    (
        "支持哪些 AI 客户端？",
        "任何兼容 MCP streamable-http 的客户端都能接入，已实测 Claude Code 与 Cursor；"
        "自建 Agent 框架也可以直接调用 REST API 的 POST /search。",
    ),
    (
        "怎么计费？",
        "积分制：¥0.03 = 1 积分，基础搜索每次 1 积分、高级搜索每次 2 积分，注册即送免费额度；"
        "支持积分充值（永久有效）与包月订阅（30 天有效期）。",
    ),
    (
        "搜索结果可以商用吗？",
        "可以。SearchPipe 提供的是聚合检索与结构化整理能力，请在使用结果时遵守来源网站的服务条款与著作权规定。",
    ),
    (
        "接入需要多长时间？",
        "注册后复制控制台给出的 MCP 链接，写进客户端配置即可，通常 1–3 分钟；"
        "Agent 也可以直接读取 /agent-setup/SKILL.md 自动完成配置。",
    ),
)

FAQ_ITEMS: tuple[tuple[str, str], ...] = LANDING_FAQ + (
    (
        "MCP 链接里的 API Key 安全吗？",
        "Key 内嵌在 URL 里虽然方便，但会出现在客户端配置文件中。建议为每个 Agent 客户端单独创建 Key，"
        "发现泄露时在控制台立即吊销——吊销后该 Key 立即失效。",
    ),
    (
        "为什么有时候搜索比较慢？",
        "冷启动查询要跑完「多引擎检索 → 正文抓取 → LLM 重排」全链路，通常数秒；"
        "相同 query 与参数在缓存有效期（默认 300 秒）内会直接命中缓存，返回是毫秒级。",
    ),
    (
        "一次请求会返回多少条结果？",
        "默认返回 5 条，可在请求里用 max_results 调整（1–20）。返回结果带 0–1 的相关性评分，按相关性从高到低排序。",
    ),
    (
        "摘要（answer）是怎么生成的？",
        "开启 include_answer 后，服务会基于抓取到的正文生成带引用编号的摘要，并标记 ai_generated=true，"
        "符合生成式内容标识要求。",
    ),
    (
        "搜索失败会扣费吗？",
        "不会。检索源失败返回 502、输出内容违规返回 400 时都会自动退款（幂等）；"
        "只有成功返回结果的请求才计费。",
    ),
    (
        "有调用频率限制吗？",
        "有滑动窗口限流（默认每分钟 100 次、突发 20 次），超限返回 429 并带 Retry-After 头。"
        "高频场景可以联系我们调整额度。",
    ),
    (
        "积分会过期吗？",
        "充值获得的积分永久有效；包月订阅（含续订、升级）获得的积分自到账起 30 天有效，到期未用完自动清零。",
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

    支持 Google Search Console / Bing Webmaster / 百度搜索资源平台，
    统一通过 .env 配置，避免改模板发版。
    """
    settings = get_settings()
    metas = [
        ("google-site-verification", settings.google_site_verification),
        ("msvalidate.01", settings.bing_site_verification),
        ("baidu-site-verification", settings.baidu_site_verification),
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
        f"> {SITE_ONE_LINER}：一次调用完成多引擎检索 → 正文抓取 → LLM 重排 → 可选摘要，"
        "返回带相关性评分的结构化结果；同时提供远程 MCP Server 与 REST API 两种接入方式。",
        "",
        "## 关键链接",
        "",
        f"- [首页]({base}/)：产品定位、能力说明与在线体验入口",
        f"- [开发文档]({base}/docs)：POST /search 参数、响应结构、调用示例与错误码",
        f"- [MCP 接入指南]({base}/mcp-server)：Claude Code / Cursor 等客户端配置与排障",
        f"- [定价]({base}/pricing)：¥{RECHARGE_RATE} = 1 积分，充值永久有效、订阅 30 天有效",
        f"- [常见问题]({base}/faq)：计费、限流、Key 安全等 12 组问答",
        f"- [服务条款]({base}/terms)：积分、退款与使用规范",
        f"- [Agent 自举配置说明]({base}/agent-setup/SKILL.md)：给 AI Agent 读的一页接入指南",
        "",
        "## 核心事实",
        "",
        f"- 接入方式：远程 MCP Server（streamable-http，Key 可内嵌 URL）与 REST API `POST /search`",
        f"- 计费：积分制，基础搜索 1 积分/次、高级搜索 2 积分/次，失败自动退款",
        f"- 能力：多引擎聚合检索、网页正文抓取清洗、LLM 相关性重排（0–1 分）、带引用摘要",
        f"- 结果缓存：相同 query 与参数默认 300 秒内命中缓存",
        f"- 限流：滑动窗口，默认 100 次/分钟、突发 20 次",
        "",
    ]
    return "\n".join(lines)


# ---------- robots.txt / sitemap.xml ----------


def build_robots_txt() -> str:
    """robots.txt：公开页放行、私有路径屏蔽，并声明 sitemap。"""
    lines = [
        "# SearchPipe robots.txt",
        "# 公开可索引页面清单见 /sitemap.xml",
        "User-agent: *",
        "Allow: /",
        "",
        "# 以下为需登录/内部/接口路径，不参与索引",
    ]
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PATH_PREFIXES]
    lines += [
        "",
        "# 主流中文搜索引擎（Baidu 单独声明，避免继承默认 UA 规则时出现歧义）",
        "User-agent: Baiduspider",
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
