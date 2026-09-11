"""公开内容页 —— 供搜索引擎收录的静态营销/文档页面（无需登录）。

为什么单独成文件：SEO 的关键前提是「有足够多可索引的实质内容页面」。
整改前站点只有落地页一个可索引页面（/dashboard/docs 需登录且在 robots 里被屏蔽），
搜索侧几乎没有可承载长尾关键词的落点。本模块集中放这些公开页：

- GET /docs        开发文档（REST API 参考 + MCP 接入 + 错误码）
- GET /mcp-server  MCP Server 接入指南（Claude Code / Cursor / 通用客户端 + 排障）
- GET /pricing     定价（免费额度 / 充值 / 包月订阅）
- GET /faq         常见问题
- GET /terms       Terms of Service
- GET /privacy     Privacy Policy

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
            page_title="SearchPipe — AI Search API & MCP Server for Agents | Tavily Alternative",
            page_description=(
                "AI search API and MCP server for agents: multi-engine retrieval, "
                "page fetching and LLM reranking in one call. A Tavily alternative "
                "covering the Chinese web."
            ),
            page_keywords=seo.SITE_KEYWORDS,
            canonical=seo.canonical_url("/"),
            jsonld=seo.landing_ld(),
            faq_items=seo.LANDING_FAQ,
        ),
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> object:
    """Terms of Service page (public, indexable)."""
    return render_with_base(
        request,
        "terms.html",
        _public_context(
            request,
            nav_active="terms",
            page_title="Terms of Service · SearchPipe AI Search API",
            page_description=(
                "SearchPipe Terms of Service: credit recharges, monthly subscriptions, "
                "acceptable use, refunds, disclaimers and contact."
            ),
            page_keywords="SearchPipe terms of service,credits refund policy,subscription rules,acceptable use",
            canonical=seo.canonical_url("/terms"),
            jsonld=seo.terms_ld(),
        ),
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request) -> object:
    """Privacy Policy page (public, indexable)."""
    return render_with_base(
        request,
        "privacy.html",
        _public_context(
            request,
            nav_active="privacy",
            page_title="Privacy Policy · SearchPipe AI Search API",
            page_description=(
                "SearchPipe Privacy Policy: what data we collect, how merchant-of-record "
                "payments work, cookies, third-party processors, retention and GDPR rights."
            ),
            page_keywords="SearchPipe privacy policy,data collection,GDPR rights,MCP server privacy",
            canonical=seo.canonical_url("/privacy"),
            jsonld=seo.privacy_ld(),
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
            page_title="Docs · SearchPipe AI Search API & MCP Integration",
            page_description=(
                "SearchPipe docs: POST /search reference, response schema, MCP setup, "
                "curl/Python/JavaScript examples, error codes, rate limits and billing."
            ),
            page_keywords="SearchPipe docs,AI search API reference,MCP integration,POST /search,search API for agents",
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
            page_title="MCP Search Server: Add Web Search to Claude Code / Cursor · SearchPipe",
            page_description=(
                "Connect the SearchPipe remote MCP server to Claude Code, Cursor or any "
                "MCP-compatible client in minutes — API key embedded in the MCP URL."
            ),
            page_keywords="MCP search server,Claude Code web search,Cursor MCP config,MCP tutorial,remote MCP,streamable-http",
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
            page_title="Pricing: Credit Recharges & Monthly Subscriptions · SearchPipe",
            page_description=(
                "SearchPipe pricing: $0.005 per credit, free tier 1,000 credits/month. "
                "Recharges from $5 (never expire), monthly subscriptions from $4.99."
            ),
            page_keywords="AI search API pricing,Tavily alternative price,MCP server pricing,credit recharge,subscription",
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
            page_title="FAQ · SearchPipe AI Search API & MCP Server",
            page_description=(
                "SearchPipe FAQ: how credits and pricing work, MCP integration, API key "
                "safety, rate limits, Chinese web coverage and refund policy."
            ),
            page_keywords="SearchPipe FAQ,AI search API questions,MCP integration,credits expiration,rate limit",
            canonical=seo.canonical_url("/faq"),
            jsonld=seo.faq_page_ld(),
            faq_items=seo.FAQ_ITEMS,
        ),
    )
