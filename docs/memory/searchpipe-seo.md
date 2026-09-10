# searchpipe SEO 基建与不变量

> 类型：project · 写入：2026-09-13（全面 SEO 整改完成后定稿）
> 适用场景：改公开页/模板/元信息、加内容页、动 robots/sitemap、排查收录问题

## 站点可索引面（当前 6 个公开页）

`/`（落地页）· `/docs`（开发文档）· `/mcp-server`（MCP 接入指南）· `/pricing`（定价）· `/faq`（常见问题）· `/terms`（服务条款）

**唯一事实来源**：`src/ai_search/dashboard/seo.py` 的 `PUBLIC_PAGES`。robots.txt、sitemap.xml、`llms.txt` 与 `tests/test_seo.py` 全部从它派生 —— **加公开页只改这一处**，不要在别处再写一份 URL 清单（整改前正是清单分散导致 sitemap 收录了被 robots 屏蔽的 `/dashboard/docs`）。

## 必须守住的不变量（`tests/test_seo.py` 已覆盖）

1. **sitemap ⊄ robots 屏蔽**：sitemap 里每个 URL 都不能命中任何 `Disallow` 前缀。
2. **sitemap 里的页面必须可访问**：每个 URL 200、canonical 指向自身、无 `noindex`。
3. **canonical / og:url 一律用配置的 `APP_BASE_URL`**（不是 `request.base_url`），保证 www / IP 直连 / HTTP 时的 canonical 唯一。
4. **公开页 `index, follow`；控制台与接口路径 `noindex, nofollow`**（`base.html` 元标签 + `main.py` 的 `X-Robots-Tag` 中间件双保险，两者共用 `seo.is_private_path()`）。
5. **结构化数据必须与页面可见内容一致**（FAQ 问答、定价数字都做了交叉断言），禁止只写 JSON-LD 不写正文。

## 关键路径约定（容易踩）

- `/docs` = **公开文档页**；Swagger UI 已挪到 `/api-docs`（FastAPI 默认的 `/docs` 会抢路由：`docs_url="/api-docs"`，`redoc_url=None`）。
- `/dashboard/docs` → **301** `/docs`：老链接与书签不断，同时避免重复内容。控制台导航「文档」直接指 `/docs`。
- robots 里 MCP 规则写 **`Disallow: /mcp/`（带尾斜杠）**：裸 `/mcp` 会连公开页 `/mcp-server` 一起屏蔽。
- 私有路径清单 `PRIVATE_PATH_PREFIXES` 同时驱动 robots.txt 与 `X-Robots-Tag`；`/agent-setup`（SKILL.md）、`/api-docs`、`/openapi.json` 也在其中（内容薄，不值得收录）。
- 404 双形态：浏览器（`Accept` 含 `text/html`）渲染 `not_found.html`，其余仍是 JSON `{"detail": ...}`。

## 改公开页文案时的动作

1. `seo.SITE_LAST_MODIFIED` 同步 bump（写进 sitemap `lastmod`）。
2. 页面 `<title>` / `description` 在 `public_pages.py` 的路由里给（模板只渲染），title 建议 ≤ 90 字、description 50–200 字（测试会卡）。
3. 静态资源改动记得 bump 模板里的 `?v=` 版本号（`/static` 是 `no-cache` + ETag，版本号是打爆存量缓存的手段）。

## 站长平台验证

`.env` 配 `GOOGLE_SITE_VERIFICATION` / `BING_SITE_VERIFICATION` / `BAIDU_SITE_VERIFICATION`，配好重启即渲染 meta（`seo.verification_metas()` → `base_public.html`）；未配置则完全不出标签。sitemap 提交地址：`{APP_BASE_URL}/sitemap.xml`。

## 生产 nginx 侧（searchpipe.tech）

- 443 开 HTTP/2；`www` 与 80 端口一律 301 到 `https://searchpipe.tech`
- 全局 gzip 扩展在 `/etc/nginx/conf.d/gzip.conf`（默认只压 html，css/js/xml 需要显式声明）
- **`sites-enabled/` 里不许放 `.bak` 文件**：该目录被 `include` 全量加载，备份会与线上配置抢同名 server_name（实际触发过 `conflicting server name` warning）。备份放 `/root/nginx-backups/`。
