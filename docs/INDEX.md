# SearchPipe 代码索引（Agent 入口）

> **Agent 进场第一份文件。** 读这里定位目标，按需精读单文件，避免全量扫描。
> 本索引基于 overseas 分支 2026-09-14 代码实读生成，行号真实可跳。

## 项目一句话定位

自建 AI 搜索 API（Tavily 风格）：`POST /search` 一个端点跑完「SearXNG 多引擎检索 → trafilatura 正文抓取 → DeepSeek 重排/摘要」，外覆完整商业化后端（用户鉴权 / 积分计费（批次化、2 位小数）/ API Key / MoR 支付（Creem/Dodo 托管收银台：充值 + 原生自动续订订阅）/ 内容审核 / 限流 / 用量日志），另提供 Jinja2 服务端渲染控制台（/dashboard）与 MCP Server 入口（/mcp）。出海版全站英文、USD 结算；国内版封存于 `archive/china-2026-09` tag 与 `china-archive` 分支。

## 目录树（仅项目代码）

```
searchpipe/
├── src/ai_search/
│   ├── main.py               # FastAPI 入口：中间件/路由汇聚 + /search 依赖链 + /healthz + /agent-setup/SKILL.md + 订阅积分过期清理后台任务（279 行；/static no-cache 防旧缓存；私有路径 X-Robots-Tag noindex 中间件 + 浏览器 404 HTML 页；Swagger 在 /api-docs；/search 增加邮箱验证检查）
│   ├── config.py             # pydantic-settings 全部配置（含 SMTP、OAuth(GitHub/Google)、MoR 支付(creem/dodo)、currency=USD、充值规则、审核、限流、抓取超时/并发、结果缓存 TTL、API Key 加密主密钥）（116 行）
│   ├── schemas.py            # /search 请求/响应模型
│   ├── mcp_server.py         # FastMCP streamable-http 子应用（/mcp，共用商业管线；鉴权支持 URL ?api_key= / Authorization 头 / 工具参数；增加邮箱验证检查）（225 行）
│   ├── agent_setup.py        # /agent-setup/SKILL.md 生成（Tavily 式 URL 内嵌 Key 的 MCP 接入指南）+ mcp_url()/build_agent_prompt()（一句话配置，agent_setup.py:180/185）（199 行）
│   ├── auth/                 # 鉴权：JWT + session cookie + API Key 三通道
│   │   ├── routes.py         # /auth/*：注册/登录/refresh/忘记密码/重置/邮箱验证（通过即自动建默认 API Key）/OAuth(GitHub+Google)/me（407 行）
│   │   ├── dependencies.py   # get_current_user 等 DI 依赖（cookie 兜底）（139 行）
│   │   ├── core.py           # 协议无关鉴权核（resolve_jwt/resolve_api_key/require_email_verified）（111 行）
│   │   ├── jwt_handler.py    # python-jose HS256 签发/解码（39 行）
│   │   ├── session.py        # itsdangerous 签名 session cookie（7 天 httponly）（56 行）
│   │   ├── password.py       # argon2 哈希（passlib）（19 行）
│   │   ├── password_reset.py # Redis 一次性重置 token + 60s 发信冷却（87 行；2026-09-08）
│   │   ├── email_verification.py # 邮箱验证 token + 发信冷却（~110 行；2026-09-11）
│   │   ├── oauth.py          # GitHub/Google OAuth 客户端（authorize_url/fetch_user/enabled；微信 stub 已删）（145 行）
│   │   └── errors.py         # AuthError（17 行）
│   ├── dashboard/            # 控制台 + 公开站点（Jinja2 SSR + session cookie）
│   │   ├── routes.py         # 控制台 /dashboard/* 页面（581 行） + 登录/注册/忘记密码表单处理（支持 ?next= 站内回跳）+ 反馈工单页 + 站内信页；/dashboard?q= 预填快速搜索；/dashboard/docs → 301 /docs；/dashboard 与 /dashboard/api-keys 对已验证用户惰性补默认 API Key
│   │   ├── public_pages.py   # 公开可索引页（196 行；2026-09-13 SEO 整改从 routes.py 拆出 + 新增内容页）：/ 落地页 /terms 条款 /privacy 隐私政策 /docs 开发文档 /mcp-server MCP 接入指南 /pricing 定价 /faq 常见问题
│   │   ├── seo.py            # SEO 基建（622 行；2026-09-13 新增；出海版全英文 + priceCurrency=USD）：PUBLIC_PAGES 单一事实来源 + robots.txt/sitemap.xml/llms.txt/favicon/og-image 路由 + JSON-LD 构造 + 私有路径判定 + 站长验证 meta（Google/Bing）
│   │   ├── templates/        # base(控制台壳)/base_public(公开站点壳，含完整 SEO head + Privacy 页脚链接)/landing/public_docs/mcp_server/pricing/faq/terms/privacy/not_found/login/register/forgot_password/reset_password/dashboard/api_keys/usage/billing/feedback/messages/admin_*（23 个模板；公开页全部 extends base_public.html 且全英文）；api_keys.html = Key 打码+点击查看 + MCP 配置卡（复选框选 Key / MCP 链接 / 一句话配置只留复制按钮）；dashboard.html 概览卡同款复制按钮 + 打码 MCP 命令
│   │   └── static/           # app.css（双主题设计系统，含 .brand-mark 品牌 logo 样式）+ app.js + favicon.ico/svg、favicon-16/32.png、apple-touch-icon.png、icon-192/512.png、og-image.png、site.webmanifest（2026-09-11 品牌图标换新为 searchpipe-brand 钥匙形 logo，源文件在仓库 searchpipe-brand/）
│   ├── db/
│   │   ├── base.py           # engine/session 工厂 + dispose_engine（44 行）
│   │   ├── session.py        # get_db 依赖
│   │   └── models/           # user(含 OAuthAccount)/api_key(含 key_cipher 密文与 is_default 默认 Key 标记)/billing(Plan 含 provider_products JSONB 套餐↔product 映射/Order 含订阅字段)/credit(含 CreditLot 批次)/subscription(含 provider/provider_subscription_id/provider_customer_id)/usage/feedback_ticket/site_message(站内信，batch_id 聚合已读统计)
│   ├── billing/              # 积分计费：批次化扣费/退款/赠送/过期清理（service 396 行；pipeline 89 行；subscription 订阅事件驱动到账/续订/升级 199 行）
│   ├── payments/             # MoR 支付（Creem/Dodo 托管收银台，USD）：catalog/下单（托管收银台 URL，下单需邮箱已验证，未验证 403）/webhook 验签归一化事件/portal 客户门户/状态查询（routes 306 行；service 476 行；provider.py 归一化 PaymentEvent 接口 91 行；creem.py 201 行；dodo.py 232 行）
│   ├── api_keys/             # sp- 前缀 API Key CRUD + 默认 Key + 明文可查看（crypto.py 47 行 Fernet 加解密；service 207 行；routes 180 行，含 /reveal）
│   ├── usage/                # 用量日志中间件 + 统计/导出（middleware 75 行）
│   ├── rate_limit/           # Redis ZSET 滑动窗口限流（service 42 行）
│   ├── moderation/           # 阿里云内容安全（输入/输出审核）（aliyun 109 行）
│   ├── admin/                # 管理端：用户/积分/订单/统计/反馈工单/站内信/运营监控页
│   ├── feedback/             # 用户反馈工单：提交/列表/管理侧关闭（__init__ 162 行）
│   ├── messages/             # 站内信用户侧：GET /messages 列表 + 未读数 + 标记已读（__init__ 134 行；管理端发送在 admin/routes.py）
│   ├── search/               # 检索编排：SearXNG 客户端 + 多引擎聚合（orchestrator 53 行）
│   ├── extract/              # trafilatura 正文抓取（fetcher 230 行；超时/并发走 config fetch_timeout/fetch_concurrency）
│   ├── rerank/               # LLM 重排（llm_reranker 137 行；prompt 已英文化）
│   ├── core/search_service.py# 搜索管线总装 run_search（含 Redis 结果缓存 + 精排前候选裁剪）（143 行）
│   └── utils/                # cache（Redis 懒连接单例 97 行；限流/重置 token/结果缓存共用）/ mailer（smtplib+to_thread，SMTP 未配置降级日志，54 行；2026-09-08）/ logger
├── alembic/                  # DB 迁移（入口 entrypoint.sh 自动 upgrade head）
├── tests/                    # pytest；真实 PG/Redis；conftest 有 loop 隔离硬约束（必读）
├── searxng/                  # SearXNG 双环境配置：settings.yml=境外默认（bing+google cse+brave+wiki 系）；settings.cn.yml=境内（bing+baidu+sogou+360search），compose 按 SEARXNG_SETTINGS_PATH 选用
├── history/                  # 需求/任务备忘（按日期）
├── docs/                     # 本索引 + 项目记忆 + REGRESSION_CHECKLIST.md（MVP 全量回归测试清单，版本迭代上线前必跑）
├── Dockerfile / entrypoint.sh / docker-compose.yml / docker-compose.test.yml / docker-compose.prod.yml
└── AGENTS.md                 # Agent 入口规则（DSH 自动加载）
```

**⚠️ `.venv/`（1.2 万+ 文件）是依赖环境，永远不读、不索引、不改。**

## 三句话架构

1. **FastAPI 单体多入口**：同一进程挂 `/search`（API）、`/dashboard`（SSR 控制台）、`/mcp`（FastMCP streamable-http 子应用，lifespan 合并进主 app）。
2. **三通道鉴权**：程序调用走 `Authorization: Bearer <JWT 或 sp- API Key>`，浏览器走 itsdangerous 签名 session cookie；`auth/dependencies.py` 统一解析为 `AuthContext` 供计费/限流/用量复用。
3. **商业化依赖链在端点内显式编排**：/search 内依次 鉴权 → 输入审核 → 限流（admin 豁免）→ 扣费 → 搜索 → 输出审核/AI 标识，失败幂等退款。

## 模块速查表

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.py` | 279 | 入口汇聚 + /search 依赖链编排 + /agent-setup/SKILL.md + X-Robots-Tag 中间件 + 404 页（/static 挂 _NoCacheStaticFiles；Swagger 挪到 /api-docs；/search 增加邮箱验证检查） |
| `auth/routes.py` | 407 | 注册/登录/refresh/忘记密码/重置密码/邮箱验证（验证通过即自动生成默认 API Key）/OAuth(GitHub+Google)/me |
| `auth/dependencies.py` | 139 | JWT/API Key/cookie 三通道 DI |
| `auth/oauth.py` | 145 | GitHub/Google OAuth 客户端（authorize_url/fetch_user/enabled 开关；login/register 模板按开关渲染 Continue with GitHub/Google 按钮） |
| `auth/password_reset.py` | 87 | Redis 一次性重置 token（`pwdreset:token:*`）+ 冷却（`pwdreset:cooldown:*`） |
| `auth/email_verification.py` | ~110 | Redis 一次性验证 token（`verify:token:*`）+ 冷却（`verify:cooldown:*`） |
| `dashboard/routes.py` | 581 | 控制台 SSR 页面 + 表单登录/注册/密码重置/反馈工单/站内信页（失败重渲染，不裸 4xx；登录/注册支持 ?next= 站内回跳）+ `/dashboard/docs` 301 → `/docs` + /dashboard 与 /dashboard/api-keys 对已验证用户惰性补默认 API Key |
| `dashboard/public_pages.py` | 196 | 公开可索引页（全英文）：`/` `/terms` `/privacy` `/docs` `/mcp-server` `/pricing` `/faq`（统一走 `base_public.html` 壳 + 注入 canonical/og/JSON-LD） |
| `dashboard/seo.py` | 622 | SEO 基建：`PUBLIC_PAGES` 单一事实来源、JSON-LD 构造（priceCurrency=USD）、robots.txt/sitemap.xml/llms.txt/favicon/og-image 路由、`is_private_path()`、站长验证 meta（Google/Bing） |
| `api_keys/service.py` | 207 | API Key：创建（key_hash + key_cipher 双写）/列表/吊销（默认 Key 自动顺延）/ensure_default_key/明文解密 |
| `api_keys/routes.py` | 180 | /api-keys CRUD + GET /api-keys/reveal 与 GET /api-keys/{id}/reveal（返回明文 + MCP 链接 + 一句话配置；仅控制台登录态） |
| `api_keys/crypto.py` | 47 | key_cipher 的 Fernet 对称加解密（主密钥由 KEY_ENCRYPTION_SECRET / SESSION_COOKIE_SECRET 派生） |
| `agent_setup.py` | 206 | SKILL.md 生成 + `mcp_url()` + `build_agent_prompt()` 一句话配置渲染（已英文化） |
| `feedback/__init__.py` | 162 | 用户反馈工单：POST/GET /feedback + Redis 频率限制 |
| `messages/__init__.py` | 134 | 站内信用户侧：GET /messages（列表+未读数）、GET /messages/unread-count、POST /messages/{id}/read（越权 404） |
| `admin/routes.py` | 660 | 管理端：用户/积分/订单/统计/反馈工单（Accept: text/html 渲染管理页）/站内信（GET/POST /admin/messages，定向+广播）/运营监控 SSR 页（含注册用户列表） |
| `billing/service.py` | 396 | 积分账户：批次化 grant/deduct/refund/sweep_expired（行锁；限时批次优先消耗；退款按 lot_usage 还原原批次） |
| `billing/subscription.py` | 199 | 订阅事件驱动到账：fulfill_subscribe/fulfill_subscription_payment（Creem 首期 subscription.paid、Dodo 首期 subscription.active，含归属校验）/fulfill_renew（event_id 幂等）/fulfill_upgrade |
| `payments/provider.py` | 91 | 归一化支付接口：`PaymentEvent`（one_time_paid/subscription_checkout/subscription_activated/subscription_paid/subscription_canceled/ignored）+ Provider 抽象（create_checkout/parse_webhook/customer_portal_url） |
| `payments/creem.py` | 201 | Creem provider：POST {api_base}/v1/checkouts（x-api-key）；webhook creem-signature=HMAC-SHA256 hex；Customer Portal POST /v1/customers/billing-portal |
| `payments/dodo.py` | 232 | Dodo provider：POST {api_base}/checkouts（Bearer）；webhook 为 Standard Webhooks 三头（webhook-id/webhook-timestamp/webhook-signature）HMAC-SHA256+base64；portal POST /customers/{id}/customer-portal/session |
| `payments/routes.py` | 306 | /payments/*：catalog（USD 字段 price/original_price/credit_price_rate/max_recharge_amount/currency）/orders（托管收银台 URL）/webhooks/{provider}（raw body + 验签 400/重试 500）/portal/orders/{id}/packages |
| `payments/service.py` | 476 | 下单编排 + webhook 事件分发（按 provider 名匹配激活单例，便于测试注入 FakeProvider）+ 幂等发积分 |
| `usage/middleware.py` | 75 | BaseHTTPMiddleware 用量日志（注意 task group 约束） |
| `rate_limit/service.py` | 42 | Redis ZSET 滑动窗口 |
| `utils/cache.py` | 97 | RedisCache 懒连接单例（测试 rebind 见 conftest） |
| `utils/mailer.py` | 54 | 事务邮件；SMTP 未配置降级日志，绝不抛错阻断业务 |

## 路由速查

**API（JWT/API Key）**：`/auth/register|login|refresh|forgot-password|reset-password|verify-email|resend-verification|me` + `/auth/oauth/github|google`（+各自 /callback）（auth/routes.py）· `/api-keys` CRUD + `GET /api-keys/reveal`（默认 Key 明文）/ `GET /api-keys/{id}/reveal`（指定 Key 明文，返回明文 + MCP 链接 + 一句话配置；仅控制台登录态）· `/billing/balance|transactions|plans` · `/payments/catalog|packages|orders|webhooks/{provider}|portal` · `/usage|/usage/logs|/usage/export` · `/feedback` 提交/列表 · `/messages` 列表/未读数/标记已读（messages/__init__.py）· `/admin/users|credits|orders|stats|feedback|messages|monitor` · `POST /search`（main.py:197）· `GET /agent-setup/SKILL.md`（main.py:190）

**公开页（可索引，seo.py + public_pages.py）**：`GET /` 落地页（hero 已登录给「复制一句话配置」按钮，匿名降级为「登录后一键配置 Agent」跳登录并回跳 `/dashboard/api-keys`） · `GET /docs` 开发文档 · `GET /mcp-server` MCP 接入指南 · `GET /pricing` 定价 · `GET /faq` 常见问题 · `GET /terms` 服务条款 · `GET /privacy` 隐私政策 · `GET /robots.txt` · `GET /sitemap.xml`（application/xml） · `GET /llms.txt` · `GET /favicon.ico|/favicon.svg|/apple-touch-icon.png|/og-image.png`

**控制台（session cookie，noindex）**：`/dashboard/login|register|forgot-password|reset-password|logout`（login/register 支持 ?next= 回跳） · `/dashboard[|/api-keys|/usage|/billing|/feedback|/messages]`（/dashboard 支持 ?q= 预填并自动触发快速搜索；/api-keys 页含 MCP 配置卡：选 Key → MCP 链接 + 一句话配置复制） · `/dashboard/docs` → 301 `/docs` · Swagger UI 在 `/api-docs`（`/docs` 已被公开文档页占用）

## 按任务跳转表

| 要做的事 | 先读 | 然后精读 |
|----------|------|----------|
| 改鉴权/登录态 | 本表 + `docs/memory/searchpipe-auth-design.md` | `auth/routes.py` / `auth/dependencies.py` |
| 改注册/忘记密码邮件 | `docs/memory/searchpipe-auth-design.md` | `auth/password_reset.py` + `utils/mailer.py` |
| 改邮箱验证 | `docs/memory/searchpipe-auth-design.md` | `auth/email_verification.py` + `auth/routes.py`（verify-email/resend-verification） |
| 改控制台页面 | `dashboard/routes.py` 头部 docstring 路由表 | 对应 `dashboard/templates/*.html` |
| 改公开页/元信息/robots/sitemap | `docs/memory/searchpipe-seo.md`（SEO 不变量） | `dashboard/seo.py` + `dashboard/public_pages.py` + `base_public.html` |
| 改 API Key（默认 Key／明文可查看／MCP 链接与一句话配置） | `api_keys/service.py` 头部 docstring | `api_keys/crypto.py` + `api_keys/routes.py` + `agent_setup.py` + 模板 `api_keys.html`/`dashboard.html` |
| 改站内信 | `messages/__init__.py`（用户侧）+ `admin/routes.py` 站内信段（管理端） | `db/models/site_message.py` + 模板 `messages.html`/`admin_messages.html`/`admin_feedback.html` |
| 改搜索管线 | `core/search_service.py` | `search/orchestrator.py` → `extract/fetcher.py` → `rerank/llm_reranker.py` |
| 改计费/退款/订阅 | `billing/service.py` 头部 docstring | `billing/subscription.py`（事件驱动到账）+ `billing/pipeline.py` + `payments/service.py` + `payments/provider.py` |
| 改支付 provider / webhook / 套餐映射 | `docs/memory/searchpipe-overseas-migration.md`（MoR 模式与事件编排约定） | `payments/provider.py` + `payments/creem.py` / `payments/dodo.py` + `payments/routes.py` + `db/models/billing.py`（plans.provider_products） |
| 加测试 | `tests/conftest.py` 头部 docstring（loop 隔离硬约束） | 现有 `tests/test_auth.py` / `tests/test_feedback.py` 作范式 |
| 部署到测试服 | `docs/memory/searchpipe-test-server.md` | `Dockerfile` / `docker-compose.test.yml` |
| 部署到生产服 | `docs/memory/searchpipe-prod-server.md` | `Dockerfile` / `docker-compose.prod.yml` |
| 版本迭代上线前全量回归 | `docs/REGRESSION_CHECKLIST.md`（随功能变更同步更新） | 路由核对用上方「路由速查」 |
| 改配置/环境变量 | `config.py` | `.env.example`（同步更新） |

## Agent 使用约定

1. **进场先读根目录 `AGENTS.md` 和本文件**，再读 `docs/PROJECT_MEMORY.md`；用速查表定位后精读单文件。
2. 行号锚点格式 `file.py:行号`；改动代码后顺手更新本索引对应条目（保持行号同步）。
3. `.venv/`、`__pycache__/`、`.pytest_cache/` 永远不读——依赖环境与字节码缓存。
4. `.env` 含真实密钥，内容不引用到任何文档。
