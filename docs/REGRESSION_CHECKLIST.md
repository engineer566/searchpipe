# SearchPipe MVP 全量回归测试清单

> **定位**：本文档是每次版本迭代上线（合并 dev / 部署测试服）前**必须执行**的全量回归清单。
> 新增或变更任何功能时，**必须同步更新本文档**对应模块的检查点（维护约定见下节）。
> 依据：`history/20260910.txt` 第 3 条需求。

## 维护约定

1. **上线前必跑**：每次版本迭代，先跑自动化基线，再按本清单逐项手工验证（测试服内网 curl / 浏览器），全部勾选通过后方可上线。
2. **随功能更新**：新增/变更功能的提交必须同时更新本清单——新增检查点、修订预期结果、或标注废弃项。只改代码不改清单视为任务未完成。
3. **新增模块**：在本清单新建二级分组；新增检查点统一用 `- [ ] 操作步骤 → 预期结果` 一行式写法，步骤要具体到 URL/命令，预期要可判定。
4. **执行留痕**：每次回归结束后，复制文末「执行记录表」追加一条记录（可粘贴在部署记录或迭代备忘中）。
5. **与代码冲突时以代码为准**，并顺手修订本清单（路由以 `docs/INDEX.md` 路由速查为核对依据）。

## 环境准备

### 本地（跑自动化测试）

```bash
# 1. 起依赖容器（真实 PG/Redis/SearXNG，不用内存 mock）
docker start ai-search-postgres ai-search-redis ai-search-searxng

# 2. 跑全量自动化测试
.venv/bin/python -m pytest tests/ -q
```

⚠️ 必须用 `.venv/bin/python -m pytest`，不要直接调 `.venv/bin/pytest`——venv 从旧路径 `~/Projects/ai-search` 迁来，入口脚本 shebang 已失效，`python -m` 才可靠。

自动化测试已覆盖：auth/注册登录、搜索扣费与退款、MCP、支付 catalog/下单/回调、积分批次、反馈工单、SEO（`tests/test_seo.py` 44 项一致性校验）、服务条款、admin 限流豁免、admin 监控页（见 `tests/`）。**本清单的手工项是自动化之外的补充，不能互相替代。**

### 测试服（上线验证）

测试服是远端 `http://47.98.124.167:8001`（阿里云小机），**公网 8001 有 IP 白名单**，未放行 IP 访问会超时（不是服务挂了）。验证一律 SSH 进服务器后打内网：

```bash
ssh -i /home/wuyuming/Projects/test_host.pem root@47.98.124.167
# 进服务器后：
curl -s 127.0.0.1:8001/healthz          # 应返回 {"status":"ok"}

# 登库（用户/库名是 ai/ai，不是 postgres）：
docker exec -it ai-search-postgres psql -U ai -d ai

# 管理员提权（系统无 owner 引导流程，验证管理端前先做）：
docker exec ai-search-postgres psql -U ai -d ai \
  -c "UPDATE users SET role='owner' WHERE email='你的测试邮箱';"
```

SMTP 未配置时，重置密码邮件不发真实邮件，看应用日志取重置链接：

```bash
docker logs ai-search-app --tail 200 | grep -i reset
```

---

## 一、基础健康与站点公开页

- [ ] `curl -s 127.0.0.1:8001/healthz` → 200，`{"status":"ok"}`
- [ ] 浏览器打开 `/`（落地页）→ 200，h1 含「让你的 AI Agent 联网」
- [ ] 落地页首屏 → 含 MCP 接入命令（`claude mcp add` 代码块）；匿名访客的副按钮为「登录后一键配置 Agent」（已登录时为「复制一句话配置」，且页面 HTML 不含明文 Key）
- [ ] 落地页定价区 → 含订阅三档（包月·基础/进阶/旗舰）
- [ ] 落地页首屏「在线体验」搜索框：未登录提交 → 302 到 `/dashboard/login?next=/dashboard?q=...`，登录后自动回跳并预填触发搜索；已登录提交 → 302 进 `/dashboard?q=...` 自动开始搜索
- [ ] `curl -s 127.0.0.1:8001/terms` → 200，含「不支持自动续订」条款，且不含「概不退款」类无效声明（2026-09-12 需求 6）
- [ ] `curl -s 127.0.0.1:8001/agent-setup/SKILL.md` → 200，text 内容含 MCP 配置（`{APP_BASE_URL}/mcp?api_key=sp-…`）

### 1.1 SEO / 收录面（2026-09-13 整改，随迭代必跑）

> 详细约定与不变量见 `docs/memory/searchpipe-seo.md`；自动化覆盖在 `tests/test_seo.py`（44 项）。

- [ ] 6 个公开页全部 200：`/`、`/docs`、`/mcp-server`、`/pricing`、`/faq`、`/terms`
- [ ] `curl -s 127.0.0.1:8001/robots.txt` → 200 `text/plain`，含 `Disallow: /mcp/`（带尾斜杠，不能是裸 `/mcp`）与 `Sitemap:` 声明
- [ ] `curl -si 127.0.0.1:8001/sitemap.xml | grep -i content-type` → `application/xml`（不是 `text/plain`）
- [ ] sitemap 里每个 URL 都能 200，且**不被 robots 任何 Disallow 前缀命中**（自洽性）
- [ ] 每个公开页 `<link rel="canonical">` 指向自身（形如 `{APP_BASE_URL}/docs`），且 `<meta name="robots">` 不含 noindex
- [ ] 每个公开页只有一个 `<h1>`；`<title>` 与 `<meta name="description">` 非空且各页不重复
- [ ] 每个公开页含 `og:image`（`{APP_BASE_URL}/static/og-image.png`）与 `twitter:image`；且该图 URL 200 image/png
- [ ] 公开页含合法 JSON-LD（`<script type="application/ld+json">`，解析无报错）：首页含 Organization/WebSite/SoftwareApplication/FAQPage；`/docs` 含 TechArticle；`/mcp-server` 含 HowTo；`/faq` 含 FAQPage；`/pricing` 含 Product+Offer
- [ ] `/pricing` 的 Offer 价格都能在页面上看到（结构化数据与展示一致）
- [ ] `/favicon.ico`、`/favicon.svg`、`/apple-touch-icon.png`、`/llms.txt` 均 200 且 Content-Type 正确（favicon 不能是 404 JSON）
- [ ] `curl -si 127.0.0.1:8001/dashboard/login | grep -i x-robots-tag` → `noindex, nofollow`；`/admin/users`、`/api-docs`、`/openapi.json`、`/search` 同样带该头
- [ ] `/dashboard/docs` → 301 到 `/docs`；`/docs` 200（公开文档页，控制台导航「文档」指向它）
- [ ] 404 双形态：`curl -H "Accept: text/html" .../no-such-page` → 404 HTML（含「页面不存在」与站内链接）；`curl -H "Accept: application/json" .../no-such-page` → 404 JSON `{"detail":"Not Found"}`
- [ ] 生产域名侧（仅生产）：`https://www.searchpipe.tech/xxx` 与 `http://searchpipe.tech/xxx` 均 301 到 `https://searchpipe.tech/xxx`；`curl -w '%{http_version}'` 为 `2`；`app.css`/`app.js`/`sitemap.xml` 响应带 `content-encoding: gzip`
- [ ] 站长平台（Google/Bing/百度）验证码填进 `.env` 的 `*_SITE_VERIFICATION` 后，首页 `<head>` 出现对应 meta；未配置时**不出现**空标签

## 二、鉴权

- [ ] `/dashboard/register` 填邮箱+密码+勾选服务条款提交 → 303 跳转 `/dashboard`，已登录，注册送免费额度（FREE_TIER_CREDITS）
- [ ] 注册时不勾选服务条款 → 被拒，页面重渲染并提示（不裸 4xx）
- [ ] `/dashboard/login` 用刚注册账号登录 → 303 跳转 `/dashboard`
- [ ] 登录时密码错误 → 重渲染登录页并提示错误
- [ ] 登录时不勾选服务条款 → 被拒并重渲染
- [ ] `/dashboard/logout` → 302 到 `/dashboard/login`，cookie 已清，再访问 `/dashboard` 跳登录
- [ ] `/dashboard/forgot-password` 提交邮箱 → 统一话术提示（防枚举）；SMTP 未配置时 `docker logs ai-search-app` 可见重置链接
- [ ] 用日志中的重置链接打开 `/dashboard/reset-password?token=...` → 200，提交新密码 → 302 登录页；用新密码可登录
- [ ] 同一重置 token 二次使用 → 失效被拒（一次性 token）
- [ ] 1 分钟内重复提交忘记密码 → 触发 60s 发信冷却提示
- [ ] 邮箱验证门禁：新注册账号（未点验证链接）调 `POST /search` 与 MCP `tools/call ai_search_search` → 403「请先验证邮箱」；点邮件验证链接后再调 → 正常（admin/owner 豁免）
- [ ] 邮箱验证通过后 `/api-keys` 立即出现自动生成的「默认 Key」（见第四节默认 API Key 检查点）

## 三、搜索核心

- [ ] `curl -s -X POST 127.0.0.1:8001/search -H "Authorization: Bearer <JWT或sp-Key>" -H "Content-Type: application/json" -d '{"query":"测试"}'` → 200，返回 results
- [ ] 无凭证 POST /search → 401/403
- [ ] basic 搜索前后查 `/billing/balance` → 余额减少 `credit_cost_basic`（1 积分），流水有一条消费记录
- [ ] advanced 搜索（`"search_depth":"advanced"`）→ 扣 `credit_cost_advanced`（2 积分）
- [ ] 余额为 0 的账号 POST /search → 402，detail 含「积分不足」及余额/所需
- [ ] **MCP 全流程**（注意 `/mcp` 无尾斜杠会 307 到 `/mcp/`，curl 必须加 `-L` 或直接打 `/mcp/`）：
  `curl -sL -X POST 127.0.0.1:8001/mcp/ -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'`（URL 带 `?api_key=sp-…`）→ initialize 成功 → 发 `notifications/initialized` → `tools/call` 调 `ai_search_search` → 返回真实搜索结果，且余额被扣
- [ ] MCP 用坏 key → 报「无效或已吊销」；无 key → 报「缺少有效的 sp- API Key」

## 四、控制台页面（session cookie 登录态）

- [ ] `/dashboard` → 200，含余额/统计卡、「接入你的 Agent」快速上手卡（MCP 命令优先）、快速搜索入口
- [ ] 快速搜索：提交一次搜索 → 加载态为 spinner +「搜索中…」（**不含任何秒数/时间预期提示**），有结果返回；TTL（默认 300s）内重复相同 query → 秒回（结果缓存命中）
- [ ] `/dashboard/api-keys` → 200；创建 Key → 弹窗显示完整 MCP 链接（`{APP_BASE_URL}/mcp?api_key=sp-…`）；吊销 Key 后该 Key 调 /search 被拒
- [ ] 默认 API Key：新账号完成邮箱验证后 `/api-keys` 即有一把「默认 Key」（`is_default=true`，列表带「默认」标签），无需手动创建；未验证邮箱的账号没有
- [ ] Key 明文可查看（平时隐藏）：列表 Key 列只显示 `sp-xxxxxxxx…` 打码；点「显示」→ 出明文，再点「隐藏」→ 恢复打码，刷新页面仍为打码；带 sp- Key 调 `GET /api-keys/reveal` → 401（Key 不能读 Key）
- [ ] MCP 配置卡选 Key：勾选任一有效 Key → MCP 链接随该 Key 切换；链接默认打码，点「显示完整链接」出明文，点「复制 MCP 链接」剪贴板是完整链接
- [ ] 一句话配置只留复制按钮：`/dashboard/api-keys` 与 `/dashboard` 页面看不到提示词正文，HTML 里也没有 `sp-` 明文 Key；点「复制一句话配置」→ 剪贴板内容含 `{APP_BASE_URL}/mcp?api_key=sp-…`、`sp-…` 与 `/agent-setup/SKILL.md`，粘给 Agent 即可按要求自动配置
- [ ] 落地页（`/`）匿名访问 → 无「复制一句话配置」按钮、显示「登录后一键配置 Agent」（登录后回到 `/dashboard/api-keys`）；登录态访问 → 有该按钮且页面无明文 Key
- [ ] 吊销默认 Key → 剩下最新一把自动升为默认（「默认」标签随之移动）；有效 Key 全部吊销后再取配置 → 自动新建一把默认 Key
- [ ] 迁移 `f0a2b7c4d9e1`（api_keys.key_cipher / is_default）已 upgrade：老 Key 无密文时点「显示」提示「无法查看明文，请吊销后重新创建」而非 500
- [ ] `/dashboard/usage` → 200，用量统计/日志可见刚产生的搜索记录
- [ ] `/dashboard/billing` → 200，含充值 4 档（¥10/¥20/¥50/¥100）+ 自定义金额（≤¥100）、订阅 3 档（限时 5 折划线价）、余额与流水
- [ ] 计费页点击充值/订阅 → 弹出**二次确认弹窗**，确认后才下单
- [ ] `/dashboard/docs` → 301 跳公开文档页 `/docs`；`/docs` 200，MCP 接入/一句话配置章节在前，REST API 在后
- [ ] `/dashboard/feedback` → 200；提交反馈工单 → 成功，列表出现该工单；短时间重复提交 → 频率限制提示

## 五、支付

- [ ] `curl -s 127.0.0.1:8001/payments/catalog -H "Authorization: Bearer <token>"` → 200，结构含：**充值 4 档**（¥10/¥20/¥50/¥100）+ **订阅 3 档**（现价/原价双价）+ 自定义充值汇率（¥0.03=1 积分）与上限 + **支付宝/微信双渠道**
- [ ] **下单邮箱验证门禁（2026-09-12 需求 7）**：未验证邮箱的用户 POST /payments/orders → **403**（提示验证邮箱）；已完成验证的用户正常下单；/dashboard/billing 对未验证用户显示「充值前请先完成邮箱验证」横幅与重发验证邮件按钮
- [ ] **真实下单与回调（虎皮椒）**：⚠️ 需先在测试服 `.env` 配置 `XUNHUPAY_APPID_ALIPAY/APPSECRET_ALIPAY/APPID_WECHAT/APPSECRET_WECHAT/XUNHUPAY_NOTIFY_URL` 真实凭证；**未配置时本节跳过并在执行记录中注明**。配置后：
  - [ ] POST /payments/orders（kind=recharge）→ 返回 order_id + pay_url，订单状态 pending
  - [ ] 真实扫码支付小额 → 回调后订单转 paid，积分按 ¥0.03=1 积分到账（2 位小数），流水有 recharge 记录
  - [ ] 同一回调重放 → 幂等，不重复到账
  - [ ] 订阅下单支付 → 到账 30 天有效期订阅积分批次；续订/升级规则符合「续订下周期生效、升级延期累积」
- [ ] 积分批次：限时/订阅批次到期后读路径惰性清理 + 后台每小时 sweep → 过期积分从余额扣除

## 六、管理端（先 `UPDATE users SET role='owner'` 提权）

- [ ] `GET /admin/users` → 200，用户列表可见
- [ ] `GET /admin/credits` → 200，积分概览；POST /admin/credits/grant 手动发放 → 目标用户余额增加
- [ ] `GET /admin/orders` → 200，订单总览
- [ ] `GET /admin/stats` → 200，概览统计
- [ ] `GET /admin/feedback` → 200，工单列表；按 status 过滤正常
- [ ] `POST /admin/feedback/{id}/close` → 工单关闭，用户端列表状态同步
- [ ] `GET /admin/monitor` → 200，运营监控页含各指标区块

## 七、站内信（20260910 迭代新增，本迭代起纳入清单）

> 本迭代（history/20260910.txt 第 1 条）新增站内信功能：管理员可通知指定用户（配合反馈工单使用），也可全局通知。
> 路由核对依据：`docs/INDEX.md` 路由速查（用户侧 `GET /messages` / `GET /messages/unread-count` / `POST /messages/{id}/read`；管理端 `GET|POST /admin/messages`；控制台 `/dashboard/messages`）。

- [ ] 管理端 `GET /admin/messages` → 200，发送表单含「指定用户（按邮箱）/全局广播」选项
- [ ] 管理员向**指定用户**发送站内信 → 目标用户 `GET /messages` 可见，其他用户不可见；批次列表已读统计 0/1
- [ ] 管理员发送**全局通知** → 全部活跃用户各自收到一条（kind=broadcast），管理端批次投递数=用户数
- [ ] 用户端 `/dashboard/messages` → 200，消息列表按时间排序、未读高亮；点开消息 → 标记已读，未读角标数减少
- [ ] 导航「消息」入口未读角标与 `GET /messages/unread-count` 返回值一致；0 时角标隐藏
- [ ] 未登录访问 `/dashboard/messages` → 302 跳登录；未认证调 `GET /messages` → 401；普通用户访问 `/admin/messages`（GET/POST）→ 403
- [ ] 越权：`POST /messages/{他人消息id}/read` → 404（不暴露存在性）
- [ ] 反馈工单联动：浏览器打开 `/admin/feedback` → 工单管理页每行含「通知该用户」链接 → 跳转 `/admin/messages?to=<邮箱>&ticket=<id>` 预填收件人与「回复：工单」文案

## 八、安全与边界

- [ ] 未登录浏览器访问 `/dashboard`、`/dashboard/api-keys`、`/dashboard/billing` 等 → 跳登录页（302/303）
- [ ] 普通用户（member）访问 `/admin/users` 等 `/admin/*` → 被拒（403），看不到管理数据
- [ ] 普通用户伪造请求改他人数据（如关闭他人工单、查他人 Key）→ 403
- [ ] 限流 429：脚本对 `/search` 快速连发超过 `RATE_LIMIT_RPM`（默认 100/分，burst 20）→ 返回 429 且带 `Retry-After` 头；admin/owner 账号连发不触发
- [ ] 输出 AI 标识：带 answer 的搜索响应含 `ai_generated: true`（深度合成规定）
- [ ] 输入审核：违禁词查询 → 400 且不扣费（`MODERATION_ENABLED` 开启时验证；内测默认关则注明跳过）
- [ ] 失效/吊销的 API Key 调 /search 与 /mcp/ → 被拒
- [ ] session cookie：httponly、7 天过期；过期后访问 /dashboard 跳登录

---

## 执行记录表（模板）

每次回归复制下表追加一条：

| 字段 | 内容 |
|------|------|
| 版本/commit |  |
| 日期 |  |
| 执行人 |  |
| 自动化测试（pytest） | ☐ 通过 / ☐ 失败（附失败项） |
| 一、基础健康 | ☐ 通过 |
| 二、鉴权 | ☐ 通过 |
| 三、搜索核心（REST+MCP） | ☐ 通过 |
| 四、控制台页面 | ☐ 通过 |
| 五、支付 | ☐ 通过 / ☐ 跳过（虎皮椒凭证未配置） |
| 六、管理端 | ☐ 通过 |
| 七、站内信 | ☐ 通过 / ☐ 跳过（未随本迭代上线） |
| 八、安全与边界 | ☐ 通过 |
| 备注 |  |
