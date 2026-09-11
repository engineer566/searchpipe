# searchpipe 测试服与部署

> 类型：reference · 写入：2026-09-08（当日完成首次部署验证后修订）

## 测试服

**`http://47.98.124.167:8001`** —— 远端阿里云主机（Ubuntu 22.04，**x86_64**，1.6G 内存小机器），不是本机。

- **SSH**：`ssh -i /home/wuyuming/Projects/test_host.pem root@47.98.124.167`（root 可登录，ubuntu/ecs-user 不行）
- **安全组按 IP 白名单放行**：未放行的 IP 访问 8001 会超时（不是服务挂了）。验证服务用 SSH 登进去 curl 127.0.0.1。
- 部署目录 `/opt/searchpipe`（**不是 git 仓库**，rsync 同步的代码副本）；compose 项目名 `searchpipe`。
- 同机还跑着 aitrendwatch（8080）。容器：`ai-search-app`(8001) / `ai-search-postgres` / `ai-search-redis` / `ai-search-searxng`。
- `.env` 已配 `APP_BASE_URL=http://47.98.124.167:8001`；**SMTP 已配阿里企业邮箱**（smtp.mxhichina.com:465 SSL，support@searchpipe.tech，授权码=登录密码），验证邮件真实可达；排查发信看 `docker logs ai-search-app`。

## 部署流程（2026-09-08 实测通过）

```bash
# 1. 同步源码（⚠️ 必须排除 .env 和 docker-compose.yml，会覆盖远端配置！）
rsync -az --delete \
  --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=node_modules --exclude=.dsh --exclude=.claude --exclude=history \
  --exclude=.env --exclude=docker-compose.yml \
  -e "ssh -i /home/wuyuming/Projects/test_host.pem" \
  ./ root@47.98.124.167:/opt/searchpipe/

# 2. 服务器上原生构建（见下方架构坑）
ssh -i ... root@47.98.124.167 "cd /opt/searchpipe && docker build --build-arg UV_COMPILE_BYTECODE=0 -t searchpipe:latest ."

# 3. 重建 app（用 test 文件：app 服务是纯 image 引用；postgres 数据卷不动）
ssh -i ... root@47.98.124.167 "cd /opt/searchpipe && docker compose -f docker-compose.test.yml up -d app"
```

entrypoint 自动 `alembic upgrade head`；`docker compose` 插件可用（v5.5.0）。

## 坑（全是 2026-09-08 实测踩出来的）

1. **架构不同：本机 aarch64，测试服 x86_64。** 本机 `docker save | ssh docker load` 传的镜像会 `exec format error` 崩溃循环——**只能在服务器上原生构建**。
2. **rsync 会覆盖远端 `.env` / `docker-compose.yml`**（本地版本不含 APP_BASE_URL，且 app 服务带 build 段指向 ai-search:latest，会导致 compose 去 pull 不存在的镜像卡死）。部署必须加 `--exclude=.env --exclude=docker-compose.yml`。
3. 服务器 1.6G 内存：原生构建实测可用（uv sync 期间可用 ~500Mi），无需 swap；若将来 OOM 先加 swapfile。
4. `pkill -f 'docker compose'` 会匹配到自身 ssh 远程命令行把自己杀掉，别用。
5. 本机 dockerd 的坑（nofile=1024 需 UV_COMPILE_BYTECODE=0、compose 用 `-p ai-search`、venv shebang 失效用 `python -m`）仅适用于本机容器场景，本机默认不跑这套栈，只起依赖容器跑测试。
6. **测试服在境内（阿里云），google/brave/wikipedia/wikidata 全不可达（必超时）**；baidu/sogou/360search 直连 <0.5s。SearXNG 采用双环境配置：`settings.yml`=境外默认、`settings.cn.yml`=境内，compose 通过 `SEARXNG_SETTINGS_PATH` 环境变量选用（境内服在 `.env` 设 `/etc/searxng/settings.cn.yml`）。baidu 机房 IP 偶发 CAPTCHA，靠 SearXNG 自动 Suspended 降级。（2026-09-08 排查 "Qwen3.8-27B" 召回差实锤后落地）
7. **测试服 Postgres 用户/库名是 `ai`/`ai`，不是 postgres**（compose 里 POSTGRES_USER=ai）。登库用 `docker exec ai-search-postgres psql -U ai -d ai`。（2026-09-08 二次部署实测）
8. 验证一律 SSH 登进去 `curl 127.0.0.1:8001`；公网 8001 只对白名单 IP 放行，本机 IP 变了就会超时（000），不是服务挂了。

## 部署记录

- **2026-09-14 十二次部署（dev 58e7b8a）**：history/20260912.txt 需求 6/7——**①移除「概不退款」类无效声明**：terms.html 删除「一经售出概不退款」总声明及各处「不予退款/补偿」表述（改为依法处理的中性条款：服务终止/注销后未用积分清零、已购未消费款项按法律法规及条款处理），pricing/landing 页同步改为引导阅读服务条款 + 平台故障返还补偿承诺，billing 页下单二次确认弹窗去掉退款拒绝 bullet 改引服务条款，meta description 不再含退款拒绝。**②充值邮箱验证门禁**：POST /payments/orders 增加 `require_email_verified`（未验证 403，admin/owner 豁免），/dashboard/billing 对未验证用户显示「充值前请先完成邮箱验证」横幅 + 重发验证邮件按钮。test_payments `_register` 补验证流程并新增 3 用例（未验证 403/验证后放行/控制台引导）。全量 pytest **184 passed / 5 skipped**。验证（SSH 内网 curl 全过）：/terms、/pricing、/ 落地页「概不退款/不予退款」0 残留且产品规则（不支持自动续订/永久有效）仍在；未验证用户 POST /payments/orders **403**「请先验证邮箱才能使用此功能」；未验证 /dashboard/billing 横幅 + 重发按钮命中，Redis 取 verify:token 验证后横幅消失、页面正常；日志无报错。验证后清理 2 个测试用户。真实下单未测（避免真实虎皮椒下单， mock 覆盖在 pytest）。

- **2026-09-13 十次部署（dev 93003aa）**：history/20260913.txt SEO 全面整改（技术 SEO + 内容层）。代码：新增 dashboard/seo.py（PUBLIC_PAGES 单一事实来源 → robots/sitemap/llms.txt/测试全从它派生；JSON-LD 构造 Organization/WebSite/SoftwareApplication/FAQPage/HowTo/TechArticle/BreadcrumbList/Product+Offer；favicon 与 og-image 路由；is_private_path 判定；站长验证 meta）与 dashboard/public_pages.py（公开内容页 /docs、/mcp-server、/pricing、/faq + /、/terms）；模板新增 base_public.html 公开站点壳与 5 个内容页模板，landing 改 extends 并加「典型使用场景」「常见问题」节与内链，terms 补元信息，删除 docs.html；/dashboard/docs 改 301 /docs，Swagger 从 /docs 挪到 /api-docs（FastAPI 默认 /docs 会抢路由）；main.py 加私有路径 X-Robots-Tag 中间件 + 浏览器 404 HTML 页（Accept 不含 text/html 仍 JSON）；生成 favicon.ico/svg、apple-touch-icon、icon-512、og-image(1200×630)、site.webmanifest；config 加 GOOGLE/BING/BAIDU_SITE_VERIFICATION。测试：test_seo.py 重写为 44 项一致性防回归，全量 pytest 147 passed / 5 skipped。验证（SSH 内网 curl 全过）：healthz ok；6 个公开页 200；sitemap.xml content-type=application/xml 且 6 条 URL 逐条 200；robots 无冲突（`Disallow: /mcp/` 带尾斜杠，未误伤 /mcp-server）；/favicon.ico 200 image/x-icon、/og-image.png 200、/llms.txt 200；/dashboard/docs 301 → /docs；/dashboard/login 带 x-robots-tag: noindex, nofollow；无报错。**坑**：/docs 与 FastAPI 自带 Swagger 路由冲突（include_router 前已注册，模板会被 Swagger 顶掉）——必须 docs_url 改路径。
- **2026-09-12 十一次部署（dev 8178578 → d94c781）**：history/20260912.txt 需求 4/5——**①默认 API Key**：邮箱验证通过即自动生成「默认 Key」（`is_default`），老用户在 `/dashboard`、`/dashboard/api-keys` 惰性补齐，吊销默认 Key 自动顺延到剩下一把最新 Key；**②Key 明文可查看**：新增 `api_keys.key_cipher`（Fernet 对称加密，主密钥由 `KEY_ENCRYPTION_SECRET` 派生、留空回退 `SESSION_COOKIE_SECRET`），鉴权仍只走 argon2 hash；新增 `GET /api-keys/reveal`（默认 Key）与 `GET /api-keys/{id}/reveal`（指定 Key），返回明文 + 现成 MCP 链接 + 一句话配置，**只有控制台登录态能读**（带 `sp-` Key 的请求 401、越权 404、老 Key 无密文 409）；列表 Key 平时打码、点「显示」才出明文；**③MCP 配置卡**：API Keys 页新增有效 Key 复选框（默认勾选默认 Key），勾选即切换 MCP 链接；**④一句话配置**：`agent_setup.build_agent_prompt()` 渲染含 Key + MCP 链接 + SKILL.md 地址的提示词，API Keys 页/概览页/落地页（登录态）**只给复制按钮、页面不展示内容**，匿名落地页降级为「登录后一键配置 Agent」跳登录并回跳 `/dashboard/api-keys`；SKILL.md 改为「用户提示词里已有 Key/链接就直接用，别再索要」。迁移 `f0a2b7c4d9e1`（key_cipher + is_default，含历史数据回填每个用户最新有效 Key 为默认）entrypoint 自动 upgrade 成功。**验证（Playwright 打公网 + SSH 内网 DB/Redis 全过，44/44）**：注册→从 Redis 取 `verify:token:*`→点验证链接→DB 确认 email_verified 且自动建 1 把「默认 Key（is_default=t，有密文）」；概览页/API Keys 页一处复制按钮、页面与 HTML 均无明文 Key；点「显示」出明文、再点隐藏、刷新恢复打码；MCP 链接与一句话复制内容含真实 Key；建第二把 Key 后勾选切换→链接随新 Key 变；吊销默认 Key 后「第二把」成为默认且无默认空档；匿名/API-Key 读 reveal 均 401；**revealed Key 实测可用**：`/mcp/?api_key=…` initialize 拿到 session id，`POST /search` 200 返回真实结果。验证后已清理 6 个测试用户（usage/流水/批次/账户/keys/用户按序 DELETE）。**顺带修复**：①dev 遗留的 4 个失败用例（8178578 邮箱验证门禁后 `/search` 返 403 而测试断言 502）——conftest 夹具改为注册后消费验证邮件 token 完成验证，并补未验证 403 门禁用例，全量 pytest 174 passed / 5 skipped；②**浏览器实测抓到的真 bug**：api_keys.html 内联脚本同步调用 `AIS`，而 app.js 是 `defer` 加载 → MCP 配置卡首屏 `ReferenceError: AIS is not defined`、链接区显示「加载失败」、复选框与复制按钮全失效（pytest 只断言 HTML 内容，测不出来），修复为首屏 fetch 放进 `DOMContentLoaded` 后复验通过。**注意**：测试服 `.env` **没有** `KEY_ENCRYPTION_SECRET`，主密钥回退 `SESSION_COOKIE_SECRET`——**轮换 SESSION_COOKIE_SECRET 会让存量 Key 无法再查看明文**（鉴权不受影响）；如需彻底隔离请在 `.env` 显式配置 `KEY_ENCRYPTION_SECRET` 并备份。**d94c781 追加**：上线前抽查发现生产侧有「老 Key 无密文」场景——`GET /api-keys` 现返回 `viewable`，老 Key 列表不再渲染「显示」按钮、改显灰色「不可查看」，MCP 配置卡拉取失败按状态码给可操作提示（409 → 换 Key 或新建），`AIS.api` 抛出的 Error 附带 `status` 供调用方分流。

- **2026-09-10 九次部署（dev a65b2bf）**：修复「未勾选服务条款提示不生效」——根因是**浏览器启发式缓存旧 app.js**（Starlette StaticFiles 默认无 Cache-Control，Chrome 按 Last-Modified 启发式缓存约 2.4h），JS 逻辑本身在 Playwright 实测中一直正确。修复三件套：①app.js 文案改为「请同意《服务条款》」（setCustomValidity 实测 validationMessage 生效）；②main.py 挂 `_NoCacheStaticFiles` 给 /static 全部响应加 `Cache-Control: no-cache`（ETag/304 仍省流量）；③7 个模板的 app.js/app.css 引用加 `?v=20260912` 版本号，**强制打爆存量旧缓存**。验证（本地 Playwright + SSH 内网 curl 全过）：login/register 未勾选提交时 validationMessage=「请同意《服务条款》」；测试服 app.js 含新文案、Cache-Control: no-cache、页面引用带版本号、服务端兜底文案不变。全量 pytest 108 passed。**坑（重要）**：本次构建连续 2 次因 PyPI 直连下载超时失败（lxml 等）——**uv.lock 的 wheel URL 是钉死 files.pythonhosted.org 的，`--frozen` 下 UV_DEFAULT_INDEX 镜像不生效**，必须 `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ uv lock` 重生成 lock（哈希不变）才真正走镜像。改完后构建从 35min+超时 → **2 分钟完成**。Dockerfile 同时加了 UV_DEFAULT_INDEX ARG（默认阿里云）+ UV_HTTP_TIMEOUT 300。
- **2026-09-10 八次部署（dev 7244928）**：history/20260912.txt 三项需求——①服务条款匹配订阅制+积分制（terms.html：总述明确「积分充值永久有效 vs 订阅积分 30 天有效非永久」双模式，修正扣费表述为 basic 1 积分/advanced 2 积分，更新日期 2026-09-12）；②未勾选服务条款提示（app.js 新增 initTermsCheckbox，对 #agree_terms 用 setCustomValidity 覆盖浏览器原生「请勾选此框」为「请先阅读并勾选，表示同意《服务条款》」，login/register 页自动生效，服务端兜底文案不变）；③失败自动返还积分——验证结论：**退款链路早已实现**（main.py /search 两条退款路径 + refund_search 幂等 + refund_credits 按 lot_usage 还原批次），补 3 个端到端回归测试（tests/test_search.py：mock run_search 抛错 → 502/余额不变/consume+refund 成对流水；幂等；输出审核违规退款）。**顺带修复 dev 遗留**：236a082 邮箱验证功能留下的 6 个 test_auth.py 过时测试（注册后发验证邮件致邮件计数断言失效、dashboard 注册改跳 login?registered=1、注册占 60s 重发冷却）——测试改为基线计数/跟随新跳转/monkeypatch 冷却前缀绕过；并修复真 bug：dashboard 注册回跳丢失 next 参数（register_submit 现透传 next 到登录页）。全量 pytest 107 passed 全绿。验证（SSH 内网 curl 全过）：healthz ok；/terms 含双模式声明/30 天清零/basic 1 积分/advanced 2 积分/2026-09-12；app.js 含 setCustomValidity 自定义文案，login/register 均加载 app.js，不勾选 POST 仍被服务端兜底文案拦截；**失败退款实测：停 searxng → 搜索 502 → 余额 1000 不变 → 流水 consume -1.00 / refund +1.00 同 req_ref 成对** → 重启 searxng 冒烟搜索 200 恢复。验证后已清理 2 个测试用户。**坑**：本次服务器构建 uv sync 下载极慢（pypi 直连，~35 分钟），前台 ssh 耐心等或后台跑即可。
- **2026-09-10 SMTP 配置**：测试服 .env 追加阿里企业邮箱 SMTP（smtp.mxhichina.com:465 SSL，support@searchpipe.tech，授权码=登录密码），重启 app 后注册触发验证邮件，日志「邮件已发送」成功（阿里企业邮箱默认授权码即登录密码）。测试用户已清理。
- **2026-09-10 七次部署（dev 236a082）**：history/20260911.txt 邮箱验证功能——users 新增 email_verified 字段（迁移 d4b8e2f1a3c5 entrypoint 自动 upgrade）；Redis 一次性 token（verify:token:* TTL 24h）+ 每邮箱 60s 发信冷却（verify:cooldown:*）；GET /auth/verify-email?token= 成功 302 → login?verify_success=1（token 一次性，复用 → verify_error）；POST /auth/resend-verification（已验证返回 400「邮箱已验证」）；dashboard 注册 303 → login?registered=1 显示查收邮件提示，未验证用户 dashboard 警告卡片 + POST /dashboard/resend-verification（冷却 → ?resend_cooldown=1，匿名 303 → login）。验证（SSH 内网 curl 全过）：healthz ok；DB email_verified 字段存在（default false）；API 注册发信日志含完整验证链接；/auth/me 含 email_verified；验证链接 302 verify_success、DB 置 t、token 复用 verify_error；dashboard 注册 303 registered=1、登录页提示命中、未验证警告卡片命中；重发冷却 60s 内跳过、冷却后发出（重发）邮件；匿名重发 303；日志无报错。验证后已清理测试用户（DELETE 2）。当时 SMTP 未配置，验证邮件降级为日志输出，token 从日志 grep `verify-email?token=`（当日已配好 SMTP，见上一条）。
- **2026-09-09 六次部署（dev b04d944）**：history/20260910.txt 三项需求——①站内信（新表 site_messages 迁移 c3a9e4f71b28，一行一收件人+batch_id 聚合已读统计；用户侧 GET /messages、/messages/unread-count、POST /messages/{id}/read 越权 404；管理端 /admin/messages 定向按邮箱/全局广播 SSR 页 + 批次已读统计 + ?to/?ticket 预填；/admin/feedback 改 Accept 协商，浏览器渲染工单管理页含「通知该用户」入口，JSON 不变；/dashboard/messages 客户端渲染列表 + base.html 导航未读角标）。②快速搜索体验（dashboard 去掉「10–30 秒」时间提示改 spinner；Redis 结果缓存 key=sha256(query+参数) TTL 300s 由 enable_cache 控制；fetch_timeout 从 request_timeout 拆出收紧 20s→12s、抓取并发 5→8；精排候选裁剪到 max(fetch_top_n, max_results*2)；落地页首屏「在线体验」搜索框 → /dashboard?q= 预填自动搜索，匿名 303 到 login?next= 回跳，登录/注册支持 next 防 open redirect）。③docs/REGRESSION_CHECKLIST.md MVP 全量回归清单（八模块检查点+执行记录模板，上线前必跑、随功能更新）。**⚠️ 测试服 .env 有显式 ENABLE_CACHE=false，已改 true**（否则结果缓存不生效）；FETCH_TOP_N=5/REQUEST_TIMEOUT=20 与默认值一致。验证（SSH 内网 curl 全过）：healthz ok；落地页含在线体验入口，匿名 /dashboard?q= 303 带 next；注册 A/B + A 提权 owner；/admin/messages 200（B 403）；定向发送 B 可见 unread 1→已读→0；广播 sent=8（全部活跃用户）A/B 均可见；B 标 A 消息 404、匿名 401/303；/admin/feedback HTML 含通知入口且 JSON 不变；dashboard.html 时间提示 0 残留；**同 query 两次搜索 6.8s→0.017s（缓存命中，结果一致）**；回归清单已同步到 /opt/searchpipe/docs；日志无报错。验证后已清理验证消息（DELETE 9）。**坑**：长时间 ssh 前台构建会 client_loop broken pipe，但 docker build 是 daemon 侧会继续跑完、compose up 也已生效——断线后先查镜像/容器时间再决定是否重跑；服务器上残留 `while pgrep -f 'docker build'` 自匹配 watcher 进程，用 kill PID 清理（别 pkill）。

- **2026-09-09 五次部署（dev 881dd13）**：支付渠道——虎皮椒支付宝/微信双渠道 + 充值（固定4档+自定义≤¥100，¥0.03=1积分，2位小数）+ 包月订阅3档限时5折（30天有效、手动续订、升级延期累积、不降级）。积分批次化改造（credit_lots/subscriptions 新表，余额/流水 NUMERIC(20,2)）。迁移 b71f2c3d9e50（含7个套餐种子）entrypoint 自动 upgrade 成功。验证（SSH 内网 curl 全过）：healthz ok；/payments/catalog 返回 4 充值档+3 订阅档+rate 0.03+双渠道；terms 含「不支持自动续订」；落地页含订阅三档；注册 303 + /dashboard/billing 200 含二次确认弹窗；日志无报错。**待办**：支付尚未配真实凭证——需虎皮椒建支付宝/微信两个应用后把 XUNHUPAY_APPID_ALIPAY/APPSECRET_ALIPAY/APPID_WECHAT/APPSECRET_WECHAT/XUNHUPAY_NOTIFY_URL 填进测试服 .env 才能真下单。**坑**：ssh 里 `while pgrep -f 'docker build'` 会自匹配永远等不到（同 pkill 坑）；1.6G 小机构建 ~10 分钟，前台 ssh 会超时，构建是 daemon 侧的，ssh 断了也会继续跑完。
- **2026-09-08 二次部署（dev adb398b）**：history/20260908.txt 五项需求——服务条款页 /terms + 登录/注册 agree_terms 勾选、/agent-setup/SKILL.md 一句话 MCP 配置、SEO（meta/OG/robots.txt/sitemap.xml）、反馈工单（POST/GET /feedback + /dashboard/feedback + /admin/feedback）、管理员监控页 /admin/monitor。新增迁移 6f04c661a041（feedback_tickets 表），entrypoint 自动 upgrade。验证（SSH 内网 curl 全过）：条款页含「概不退款」、未勾选登录被拒并重渲染、SKILL.md 200 含 /mcp 配置、robots/sitemap 正常、工单提交/列表/越权 403/管理员关闭全通、监控页 200 含指标区块。管理员验证需先 `UPDATE users SET role='owner'` 提权（系统无 owner 引导流程）。
- **2026-09-08 四次部署（dev a6fc5da）**：全站改版为 Agent-first 定位（对标 Tavily 落地页）——landing 重写（h1「让你的 AI Agent 联网」、首屏 `claude mcp add` 代码块、「复制配置提示词」按钮复制 /agent-setup/SKILL.md、新增 #mcp 一键接入区块、去 RAG 卖点）；docs 章节重排（MCP 接入/一句话配置前置，REST API 在后）；dashboard 快速上手卡改「接入你的 Agent」（MCP 命令优先）；api_keys/base/login/register/terms 文案同步；agent_setup.py SKILL.md 去 "RAG" 字样。纯模板+文案改动，无迁移。验证（SSH 内网 curl 全过）：healthz ok、落地页 h1/MCP 命令/SKILL.md 入口命中且 RAG 残留 0、title 已更新、SKILL.md 新文案、login 新标语。
- **2026-09-08 三次部署（dev a1919b5）**：MCP 接入改为 Tavily 式 URL 内嵌 Key——`_resolve_raw_key` 新增 `?api_key=` query 鉴权（优先级：工具参数 > Authorization/X-API-Key 头 > URL query > 环境变量）；SKILL.md 与 docs 页全部改为 `{APP_BASE_URL}/mcp?api_key=sp-…` 主推荐；API Keys 页创建弹窗直接给完整 MCP 链接，列表行显示链接格式。E2E 验证：curl 走完 initialize → notifications/initialized → tools/call 全流程，URL Key 拿到真实搜索结果；坏 key 报「无效或已吊销」、无 key 报「缺少有效的 sp- API Key」。**注意：/mcp 挂载点 307 到 /mcp/（尾斜杠），curl 验证要加 -L 或直接打 /mcp/。**

## 本机容器管理

```bash
docker start ai-search-postgres ai-search-redis ai-search-searxng   # 跑测试前起依赖
docker stop ai-search-postgres ai-search-redis ai-search-searxng    # 测完可停
```
