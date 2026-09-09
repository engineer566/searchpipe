# SearchPipe 代码索引（Agent 入口）

> **Agent 进场第一份文件。** 读这里定位目标，按需精读单文件，避免全量扫描。
> 本索引基于 main 分支 2026-09-08 代码实读生成，行号真实可跳。

## 项目一句话定位

自建 AI 搜索 API（Tavily 风格）：`POST /search` 一个端点跑完「SearXNG 多引擎检索 → trafilatura 正文抓取 → DeepSeek 重排/摘要」，外覆完整商业化后端（用户鉴权 / 积分计费（批次化、2 位小数）/ API Key / 虎皮椒支付（支付宝+微信双渠道：充值 + 包月订阅）/ 内容审核 / 限流 / 用量日志），另提供 Jinja2 服务端渲染控制台（/dashboard）与 MCP Server 入口（/mcp）。

## 目录树（仅项目代码）

```
searchpipe/
├── src/ai_search/
│   ├── main.py               # FastAPI 入口：中间件/路由汇聚 + /search 依赖链 + /healthz + /agent-setup/SKILL.md + 订阅积分过期清理后台任务（207 行）
│   ├── config.py             # pydantic-settings 全部配置（含 SMTP、OAuth、支付双渠道、审核、限流、充值规则）（99 行）
│   ├── schemas.py            # /search 请求/响应模型
│   ├── mcp_server.py         # FastMCP streamable-http 子应用（/mcp，共用商业管线；鉴权支持 URL ?api_key= / Authorization 头 / 工具参数）（219 行）
│   ├── agent_setup.py        # /agent-setup/SKILL.md 生成：Tavily 式 URL 内嵌 Key 的 MCP 接入指南（172 行）
│   ├── auth/                 # 鉴权：JWT + session cookie + API Key 三通道
│   │   ├── routes.py         # /auth/*：注册/登录/refresh/忘记密码/重置/OAuth stub/me（307 行）
│   │   ├── dependencies.py   # get_current_user 等 DI 依赖（cookie 兜底）（139 行）
│   │   ├── core.py           # 协议无关鉴权核（resolve_jwt/resolve_api_key）（88 行）
│   │   ├── jwt_handler.py    # python-jose HS256 签发/解码（39 行）
│   │   ├── session.py        # itsdangerous 签名 session cookie（7 天 httponly）（56 行）
│   │   ├── password.py       # argon2 哈希（passlib）（19 行）
│   │   ├── password_reset.py # Redis 一次性重置 token + 60s 发信冷却（87 行；2026-09-08）
│   │   ├── oauth.py          # GitHub/微信 OAuth 客户端（106 行）
│   │   └── errors.py         # AuthError（17 行）
│   ├── dashboard/            # 控制台（Jinja2 SSR + session cookie）
│   │   ├── routes.py         # /dashboard/* 页面 + 登录/注册/忘记密码表单处理 + 服务条款页 + 反馈工单页 + 站内信页
│   │   ├── templates/        # base/landing/login/register/forgot_password/reset_password/dashboard/api_keys/usage/billing/docs/terms/feedback/messages/admin_monitor/admin_feedback/admin_messages（17 个模板；2026-09-08 改版为 Agent-first 定位：落地页首屏 MCP 接入，弱化 RAG 叙事；base.html 导航含站内信未读角标）
│   │   └── static/           # app.css（双主题设计系统）+ app.js
│   ├── db/
│   │   ├── base.py           # engine/session 工厂 + dispose_engine（44 行）
│   │   ├── session.py        # get_db 依赖
│   │   └── models/           # user(含 OAuthAccount)/api_key/billing(Plan/Order 含订阅字段)/credit(含 CreditLot 批次)/subscription/usage/feedback_ticket/site_message(站内信，batch_id 聚合已读统计)
│   ├── billing/              # 积分计费：批次化扣费/退款/赠送/过期清理（service 396 行；pipeline 88 行；subscription 订阅/续订/升级到账 142 行）
│   ├── payments/             # 虎皮椒支付（支付宝/微信双渠道）：catalog/四类下单/回调/状态查询（routes 263 行；service 253 行）
│   ├── api_keys/             # sp- 前缀 API Key CRUD（service 91 行）
│   ├── usage/                # 用量日志中间件 + 统计/导出（middleware 75 行）
│   ├── rate_limit/           # Redis ZSET 滑动窗口限流（service 42 行）
│   ├── moderation/           # 阿里云内容安全（输入/输出审核）（aliyun 109 行）
│   ├── admin/                # 管理端：用户/积分/订单/统计/反馈工单/站内信/运营监控页
│   ├── feedback/             # 用户反馈工单：提交/列表/管理侧关闭（__init__ 162 行）
│   ├── messages/             # 站内信用户侧：GET /messages 列表 + 未读数 + 标记已读（__init__ 134 行；管理端发送在 admin/routes.py）
│   ├── search/               # 检索编排：SearXNG 客户端 + 多引擎聚合（orchestrator 53 行）
│   ├── extract/              # trafilatura 正文抓取（fetcher 229 行）
│   ├── rerank/               # LLM 重排（llm_reranker 136 行）
│   ├── core/search_service.py# 搜索管线总装 run_search（66 行）
│   └── utils/                # cache（Redis 懒连接单例 97 行）/ mailer（smtplib+to_thread，SMTP 未配置降级日志，54 行；2026-09-08）/ logger
├── alembic/                  # DB 迁移（入口 entrypoint.sh 自动 upgrade head）
├── tests/                    # pytest；真实 PG/Redis；conftest 有 loop 隔离硬约束（必读）
├── searxng/                  # SearXNG 双环境配置：settings.yml=境外默认（bing+google cse+brave+wiki 系）；settings.cn.yml=境内（bing+baidu+sogou+360search），compose 按 SEARXNG_SETTINGS_PATH 选用
├── history/                  # 需求/任务备忘（按日期）
├── docs/                     # 本索引 + 项目记忆
├── Dockerfile / entrypoint.sh / docker-compose.yml / docker-compose.test.yml
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
| `main.py` | 207 | 入口汇聚 + /search 依赖链编排 + /agent-setup/SKILL.md |
| `auth/routes.py` | 307 | 注册/登录/refresh/忘记密码/重置密码/OAuth stub/me |
| `auth/dependencies.py` | 139 | JWT/API Key/cookie 三通道 DI |
| `auth/password_reset.py` | 87 | Redis 一次性重置 token（`pwdreset:token:*`）+ 冷却（`pwdreset:cooldown:*`） |
| `dashboard/routes.py` | 503 | SSR 页面 + /robots.txt + /sitemap.xml + /terms 服务条款页 + 表单登录/注册/密码重置/反馈工单/站内信页（失败重渲染，不裸 4xx） |
| `feedback/__init__.py` | 162 | 用户反馈工单：POST/GET /feedback + Redis 频率限制 |
| `messages/__init__.py` | 134 | 站内信用户侧：GET /messages（列表+未读数）、GET /messages/unread-count、POST /messages/{id}/read（越权 404） |
| `admin/routes.py` | 651 | 管理端：用户/积分/订单/统计/反馈工单（Accept: text/html 渲染管理页）/站内信（GET/POST /admin/messages，定向+广播）/运营监控 SSR 页 |
| `billing/service.py` | 396 | 积分账户：批次化 grant/deduct/refund/sweep_expired（行锁；限时批次优先消耗；退款按 lot_usage 还原原批次） |
| `billing/subscription.py` | 142 | 包月订阅：订阅/续订/升级到账（30 天有效期、续订下周期生效、升级延期累积） |
| `payments/xunhupay.py` | 102 | 虎皮椒签名/下单/回调验签（支付宝/微信双渠道凭证，回调两套 secret 各验一次） |
| `usage/middleware.py` | 75 | BaseHTTPMiddleware 用量日志（注意 task group 约束） |
| `rate_limit/service.py` | 42 | Redis ZSET 滑动窗口 |
| `utils/cache.py` | 97 | RedisCache 懒连接单例（测试 rebind 见 conftest） |
| `utils/mailer.py` | 54 | 事务邮件；SMTP 未配置降级日志，绝不抛错阻断业务 |

## 路由速查

**API（JWT/API Key）**：`/auth/register|login|refresh|forgot-password|reset-password|me`（auth/routes.py:153-305）· `/api-keys` CRUD · `/billing/balance|transactions|plans` · `/payments/catalog|packages|orders|callback` · `/usage|/usage/logs|/usage/export` · `/feedback` 提交/列表 · `/messages` 列表/未读数/标记已读（messages/__init__.py）· `/admin/users|credits|orders|stats|feedback|messages|monitor` · `POST /search`（main.py:131）· `GET /agent-setup/SKILL.md`（main.py:125）

**控制台（session cookie）**：`GET /` 营销页 · `GET /robots.txt` · `GET /sitemap.xml` · `/terms` 服务条款 · `/dashboard/login|register|forgot-password|reset-password|logout` · `/dashboard[|/api-keys|/usage|/billing|/docs|/feedback|/messages]`（dashboard/routes.py）

## 按任务跳转表

| 要做的事 | 先读 | 然后精读 |
|----------|------|----------|
| 改鉴权/登录态 | 本表 + `docs/memory/searchpipe-auth-design.md` | `auth/routes.py` / `auth/dependencies.py` |
| 改注册/忘记密码邮件 | `docs/memory/searchpipe-auth-design.md` | `auth/password_reset.py` + `utils/mailer.py` |
| 改控制台页面/SEO | `dashboard/routes.py` 头部 docstring 路由表 | 对应 `dashboard/templates/*.html` |
| 改站内信 | `messages/__init__.py`（用户侧）+ `admin/routes.py` 站内信段（管理端） | `db/models/site_message.py` + 模板 `messages.html`/`admin_messages.html`/`admin_feedback.html` |
| 改搜索管线 | `core/search_service.py` | `search/orchestrator.py` → `extract/fetcher.py` → `rerank/llm_reranker.py` |
| 改计费/退款/订阅 | `billing/service.py` 头部 docstring | `billing/subscription.py` + `billing/pipeline.py` + `payments/service.py` |
| 加测试 | `tests/conftest.py` 头部 docstring（loop 隔离硬约束） | 现有 `tests/test_auth.py` / `tests/test_feedback.py` 作范式 |
| 部署到测试服 | `docs/memory/searchpipe-test-server.md` | `Dockerfile` / `docker-compose.test.yml` |
| 改配置/环境变量 | `config.py` | `.env.example`（同步更新） |

## Agent 使用约定

1. **进场先读根目录 `AGENTS.md` 和本文件**，再读 `docs/PROJECT_MEMORY.md`；用速查表定位后精读单文件。
2. 行号锚点格式 `file.py:行号`；改动代码后顺手更新本索引对应条目（保持行号同步）。
3. `.venv/`、`__pycache__/`、`.pytest_cache/` 永远不读——依赖环境与字节码缓存。
4. `.env` 含真实密钥，内容不引用到任何文档。
