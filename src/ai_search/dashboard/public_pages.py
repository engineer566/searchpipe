"""公开内容页 —— 供搜索引擎收录的静态营销/文档页面（无需登录）。

为什么单独成文件：SEO 的关键前提是「有足够多可索引的实质内容页面」。
整改前站点只有落地页一个可索引页面（/dashboard/docs 需登录且在 robots 里被屏蔽），
搜索侧几乎没有可承载长尾关键词的落点。本模块集中放这些公开页：

- GET /docs        开发文档（REST API 参考 + MCP 接入 + 错误码）
- GET /mcp-server  MCP Server 接入指南（Claude Code / Cursor / 通用客户端 + 排障）
- GET /pricing     定价（免费额度 / 充值 / 包月订阅）
- GET /faq         常见问题

页面模板统一 extends `base_public.html`（公开站点壳：顶部导航 + 页脚内链 + 完整 SEO 元信息），
并且在服务端渲染前注入 canonical / og / JSON-LD，保证爬虫拿到的是首屏就有内容的 HTML。
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import seo
from .routes import render_with_base

router = APIRouter(tags=["site"])


def _public_context(request: Request, **extra: object) -> dict:
    """公开页公共上下文：登录态 + 站点常量 + 内链（模板与 JSON-LD 共用同一份数据）。

    登录态探测只校验 session cookie 签名（不查库）——最坏情况是失效 cookie 的访客
    看到「进入控制台」文案，点击后控制台守卫仍会引导登录，无害。
    """
    from ..auth.session import read_session_cookie

    ctx: dict = {
        "logged_in": read_session_cookie(request) is not None,
        "site_name": seo.SITE_NAME,
        "site_tagline": seo.SITE_TAGLINE,
        "site_description": seo.SITE_DESCRIPTION,
        "site_keywords": seo.SITE_KEYWORDS,
        "site_url": seo.get_base_url(),
        "og_image": seo.canonical_url(seo.OG_IMAGE_PATH),
        "og_image_width": seo.OG_IMAGE_WIDTH,
        "og_image_height": seo.OG_IMAGE_HEIGHT,
    }
    ctx.update(extra)
    return ctx


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> object:
    """营销首页：产品定位 + 在线体验入口 + 特性 + 使用场景 + 定价 + 常见问题。"""
    return render_with_base(
        request,
        "landing.html",
        _public_context(
            request,
            nav_active="home",
            page_title="SearchPipe — 让 AI Agent 接入实时网络的搜索 API 与 MCP Server",
            page_description=(
                "SearchPipe 是为 AI Agent 而生的联网搜索 API 与远程 MCP Server："
                "一条命令接入 Claude Code、Cursor 等客户端，一次调用完成多引擎检索、正文抓取、"
                "LLM 重排与摘要，返回带相关性评分的结构化结果。"
            ),
            page_keywords=seo.SITE_KEYWORDS,
            canonical=seo.canonical_url("/"),
            jsonld=seo.landing_ld(),
            faq_items=seo.LANDING_FAQ,
        ),
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> object:
    """服务条款页（公开可索引）。"""
    return render_with_base(
        request,
        "terms.html",
        _public_context(
            request,
            nav_active="terms",
            page_title="服务条款 · SearchPipe 联网搜索服务",
            page_description=(
                "SearchPipe 服务条款：积分充值与包月订阅规则（充值积分永久有效、"
                "订阅积分 30 天有效）、账号责任、禁止用途、免责声明与争议解决。"
            ),
            page_keywords="SearchPipe 服务条款,积分退款政策,订阅规则,使用规范",
            canonical=seo.canonical_url("/terms"),
            jsonld=seo.terms_ld(),
        ),
    )


@router.get("/docs", response_class=HTMLResponse)
async def public_docs(request: Request) -> object:
    """开发文档（公开可索引；控制台侧的 /dashboard/docs 301 到这里）。"""
    return render_with_base(
        request,
        "public_docs.html",
        _public_context(
            request,
            page_title="开发文档 · SearchPipe 联网搜索 API 与 MCP 接入",
            page_description=(
                "SearchPipe 开发文档：POST /search 全部参数与响应字段、MCP 一键接入命令，"
                "以及 curl / Python / JavaScript 调用示例、错误码与限流计费说明。"
            ),
            page_keywords="SearchPipe 文档,搜索 API 文档,MCP 接入,POST /search,AI 搜索接口",
            canonical=seo.canonical_url("/docs"),
            jsonld=seo.docs_ld(),
        ),
    )


@router.get("/mcp-server", response_class=HTMLResponse)
async def mcp_server_page(request: Request) -> object:
    """MCP Server 接入指南（长尾落地页：Claude Code / Cursor 联网搜索配置）。"""
    return render_with_base(
        request,
        "mcp_server.html",
        _public_context(
            request,
            page_title="MCP Server 接入指南：让 Claude Code / Cursor 联网搜索 · SearchPipe",
            page_description=(
                "四步把 SearchPipe 远程 MCP Server（streamable-http）接入 Claude Code、Cursor 等 AI 客户端："
                "API Key 内嵌在 MCP 链接里，零配置即可获得实时联网搜索、正文抓取与引用摘要能力。"
            ),
            page_keywords="MCP Server,Claude Code 联网搜索,Cursor MCP 配置,MCP 教程,远程 MCP,streamable-http",
            canonical=seo.canonical_url("/mcp-server"),
            jsonld=seo.mcp_page_ld(),
        ),
    )


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request) -> object:
    """定价页（积分充值 + 包月订阅，独立可索引，承载价格类查询）。"""
    return render_with_base(
        request,
        "pricing.html",
        _public_context(
            request,
            page_title="定价：积分充值与包月订阅 · SearchPipe 搜索 API",
            page_description=(
                "SearchPipe 定价：¥0.03 = 1 积分，基础搜索 1 积分/次、高级搜索 2 积分/次，注册即送免费额度；"
                "支持 ¥10/¥20/¥50/¥100 积分充值（永久有效）与包月订阅（30 天有效，限时 5 折）。"
            ),
            page_keywords="AI 搜索 API 价格,搜索 API 计费,MCP Server 价格,积分充值,包月订阅",
            canonical=seo.canonical_url("/pricing"),
            jsonld=seo.pricing_page_ld(),
            recharge_tiers=seo.RECHARGE_TIERS,
            subscription_plans=seo.SUBSCRIPTION_PLANS,
            recharge_rate=seo.RECHARGE_RATE,
        ),
    )


@router.get("/faq", response_class=HTMLResponse)
async def faq_page(request: Request) -> object:
    """常见问题（长尾问答页，输出 FAQPage 结构化数据）。"""
    return render_with_base(
        request,
        "faq.html",
        _public_context(
            request,
            page_title="常见问题 · SearchPipe 联网搜索 API 与 MCP Server",
            page_description=(
                "SearchPipe 常见问题：如何接入 MCP、Key 是否安全、计费与积分有效期、"
                "限流额度、搜索慢的原因、失败是否扣费等，共 12 组问答。"
            ),
            page_keywords="SearchPipe 常见问题,MCP 接入问题,搜索 API 计费,积分有效期,限流",
            canonical=seo.canonical_url("/faq"),
            jsonld=seo.faq_page_ld(),
            faq_items=seo.FAQ_ITEMS,
        ),
    )
