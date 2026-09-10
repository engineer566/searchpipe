"""前端 Dashboard 包 —— Jinja2 服务端渲染 + session cookie。

三个路由集合分开注册（见 main.py）：
- `router`      控制台（/dashboard/*，需登录，noindex）
- `site_router` 公开内容页（/ /terms /docs /mcp-server /pricing /faq，可索引）
- `seo_router`  SEO 基建（/robots.txt /sitemap.xml /favicon.ico /og-image.png …）
"""

from .public_pages import router as site_router
from .routes import router
from .seo import router as seo_router

__all__ = ["router", "site_router", "seo_router"]
