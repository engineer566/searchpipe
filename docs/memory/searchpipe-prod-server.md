# searchpipe 生产服与部署

> 类型：reference · 写入：2026-09-10（生产首发部署完成后定稿）

## 生产服

**`https://searchpipe.tech`** —— 远端阿里云境外主机（Ubuntu 22.04，x86_64，1.6G 内存小机器，与测试服同规格）。

- **SSH**：`ssh -i /home/wuyuming/Projects/work.pem root@47.89.243.229`（root 可登录；work.pem 是生产机钥匙，test_host.pem 是测试机的，别混）
- **Windows 环境密钥路径**：`D:\Projects\work.pem`（与 Linux 路径对应同一文件，DSH Windows 会话用此路径）
- 部署目录 `/opt/searchpipe`（rsync 代码副本，非 git 仓库）；compose 项目名 `searchpipe`
- 同机还跑着 aitrendwatch（127.0.0.1:5050）与 docparse（nginx upstream 127.0.0.1:8000，容器常停）
- **nginx(80/443) 已配好 searchpipe.tech 反代**：`proxy_pass http://127.0.0.1:8001`，含 `/mcp` SSE 专属块（关缓冲/300s 超时/upgrade 头）；Let's Encrypt 证书已签（www 二级域同证书）。改反代配置别动 8001 回源端口
- 容器：`ai-search-app`(127.0.0.1:8001) / `ai-search-postgres` / `ai-search-redis` / `ai-search-searxng`（后三个不发布宿主端口，仅 ai-search-net 内网）
- `.env` 从测试服迁移生成：LLM/SMTP 凭证沿用；**JWT_SECRET/SESSION_COOKIE_SECRET/SEARXNG_SECRET 为生产新生成**；`APP_BASE_URL`/`OAUTH_REDIRECT_BASE`=`https://searchpipe.tech`，`XUNHUPAY_NOTIFY_URL=https://searchpipe.tech/payments/callback`（虎皮椒后台配回调时同地址）；**不设 SEARXNG_SETTINGS_PATH**（境外走默认 settings.yml）

## 与测试服部署的差异（重要）

| 项 | 测试服（境内） | 生产服（境外） |
|---|---|---|
| 引擎配置 | .env 显式 `SEARXNG_SETTINGS_PATH=/etc/searxng/settings.cn.yml` | 不设该变量 → compose 默认 `settings.yml`（bing+google cse+brave+wiki 系） |
| 端口绑定 | app 绑 0.0.0.0:8001（靠安全组白名单） | app 绑 **127.0.0.1:8001**（只给本机 nginx 回源，不对公网暴露） |
| DB/Redis/SearXNG 端口 | 发布到宿主 5432/6379/8081 | 不发布，仅内网 |
| 编排文件 | docker-compose.test.yml | **docker-compose.prod.yml** |
| PyPI 源 | 阿里云镜像（uv.lock 钉死 aliyun URL） | 同一 lock 直接用（境外访问 aliyun 镜像可用，构建实测顺利） |
| 对外 URL | http://IP:8001 | https://searchpipe.tech（nginx 终止 TLS） |

## 部署流程（2026-09-10 生产首发实测通过）

```bash
# 0. 代码：main 分支（生产跟随 main，测试跟随 dev）
git checkout main && git merge dev && git push origin main   # 首发已做

# 1. 同步源码（排除 .env 和 docker-compose.yml，同测试服纪律）
rsync -az --delete \
  --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=node_modules --exclude=.dsh --exclude=.claude --exclude=history \
  --exclude=.env --exclude=docker-compose.yml \
  -e "ssh -i /home/wuyuming/Projects/work.pem" \
  ./ root@47.89.243.229:/opt/searchpipe/

# 2. 服务器上原生构建（x86_64；SSH 断了 build 也会跑完，别用 pkill/self-match watcher）
ssh -i ...work.pem root@47.89.243.229 "cd /opt/searchpipe && docker build --build-arg UV_COMPILE_BYTECODE=0 -t searchpipe:latest ."

# 3. 起全栈（fresh 库 entrypoint 自动 alembic upgrade head + 套餐种子）
ssh -i ...work.pem root@47.89.243.229 "cd /opt/searchpipe && docker compose -f docker-compose.prod.yml up -d"
```

## 需求 4/5 生产部署（main 3a8199d → e7e9f9f，2026-09-12）

- **背景**：history/20260912.txt 需求 4（默认 API Key 自动生成 / Key 明文可查看 / MCP 链接选 Key）与需求 5（一句话配置默认带 Key、页面只留复制按钮）。代码先合 dev → 合 main（`git merge dev`），生产按 `main` 走：rsync（work.pem）→ 服务器原生 `docker build` → `docker compose -f docker-compose.prod.yml up -d app`；entrypoint 自动 `alembic upgrade head` 到 `f0a2b7c4d9e1`（api_keys.key_cipher + is_default + 回填每个用户最新有效 Key 为默认）。**生产 `.env` 未改动**（无 `KEY_ENCRYPTION_SECRET`，主密钥回退 `SESSION_COOKIE_SECRET`；**轮换 SESSION_COOKIE_SECRET 会让存量 Key 无法再查看明文**，鉴权不受影响）。
- **验证（Playwright 打 `https://searchpipe.tech` + SSH 内网 DB/Redis，50/50 全过）**：注册→从 Redis 取 `verify:token:*`→点验证链接→DB 确认 `email_verified` 且自动生成「默认 Key（is_default=t、有密文）」；概览页/API Keys 页各一处复制按钮、两页 HTML 均无明文 Key；Key 列打码→点「显示」出明文→再点隐藏；MCP 链接默认打码、复制拿完整链接；建第二把 Key 后勾选复选框切换→链接随新 Key 变；吊销默认 Key→剩下一把自动成为默认；匿名/带 sp- Key 调 `/api-keys/reveal` 均 401；**revealed Key 实测可用**：`/mcp/?api_key=…` initialize → notifications/initialized → `tools/call ai_search_search` 返回真实结果，`POST /search` 200；healthz 内网 + HTTPS 双通、容器日志无 error。
- **本次浏览器实测抓到并修复的 2 个真 bug**（pytest 只断言 HTML，测不出这类问题）：
  1. `api_keys.html` 内联脚本同步调用 `AIS`，而 `app.js` 是 `defer` → MCP 配置卡首屏 `ReferenceError: AIS is not defined`、链接区「加载失败」、复选框与复制按钮全失效 → 首屏 fetch 放进 `DOMContentLoaded`（f105020）。
  2. 模板条件误用 `k.viewable`（`viewable` 只存在于 API 的 `KeyItem`，模板遍历的是 ORM `ApiKey`）→ Jinja2 取到 undefined、条件恒假 → **所有 Key 都渲染「不可查看」、没有「显示」按钮**（生产实测命中，测试服同样中招）→ 改用 `k.key_cipher` 判定（04f58ee），并把测试断言从类名改成断言渲染元素本身。
- **顺手为存量用户做的体验兜底**（e7e9f9f）：老 Key（无密文）在列表显示灰色「不可查看」、MCP 配置卡按 409 给「换 Key / 新建」提示、概览页改为引导去 API Keys 新建（不再给点了会报错的复制按钮）、落地页复制失败统一引导到 `/dashboard/api-keys`。
- **生产数据观察与清理**：①`ferriswym@163.com` 注册于 2026-09-10 09:10（UTC）但**邮箱未验证**——受门禁限制，其 `/search` 与 MCP 调用会 403（由项目负责人自行处理，本次未动）；②**孤儿记录已清理**：两个已删除冒烟用户（`018b3ad3-…`、`cc022fe9-…`）留下的 `api_keys` 1 条（`test-key`/`sp-XEFuV`）、`credit_lots` 1 条、`credit_transactions` 7 条、`usage_logs` 1 条，清理前导出备份到生产机 `/root/orphan-rows-backup-20260911.json`，清理后各表孤儿计数均为 0；真实用户的 1 个账户/1 个批次/3 条流水/2 条用量日志原样保留（已核对 `user_id` 全部属于 `57307365-…`）。
- **坑**：`api_keys`／`credit_*`／`usage_logs` 的 `user_id` **没有外键约束**，删用户不会级联删这些行——删测试用户时要顺手清干净（先备份）：`DELETE FROM usage_logs|credit_transactions|credit_lots|credit_accounts|api_keys WHERE user_id NOT IN (SELECT id FROM users);`
- **合并注意**：`docs/INDEX.md`、`docs/memory/searchpipe-test-server.md` 在 main 与 dev 两侧都被改过（main 侧是只提交到 main 的 SEO 文档更新），合并 dev→main 时需手工合并，本次已按「保留两侧内容」处理。

## 首发验证记录（2026-09-10）

- 迁移建表 5 段全过（init→email_verified）；healthz ok（内网+经 nginx HTTPS 双路）
- 落地页/SKILL.md/robots 正常，**SKILL.md 引用已是 https://searchpipe.tech**（APP_BASE_URL 生效）
- 全链路冒烟：注册 201 → 验证邮件实发（阿里企业邮箱境外可达）→ 登录 → 建 sp- Key → /search 200（8s，SearXNG 返 30 候选）→ 扣费 1 积分流水对账 → MCP initialize 过 nginx SSE 握手正常（裸 GET /mcp/ 406 属正常，要带 Accept: application/json, text/event-stream）
- 中英文多 query 召回正常（7-11s/次）
- 已清理冒烟用户（users 归零）


## SEO 整改与 nginx 调整（2026-09-13）

- **应用部署（main 93003aa）**：rsync → `docker build` → `docker compose -f docker-compose.prod.yml up -d app`；线上复验 6 个公开页 200、canonical/og 均为 `https://searchpipe.tech/...`、JSON-LD 可解析、sitemap 6 条 URL 逐条 200、robots 与 sitemap 自洽、`/dashboard/docs` 301 `/docs`、私有路径带 `X-Robots-Tag: noindex, nofollow`、404 双形态、6 页 HTML 标签闭合校验通过。
- **nginx（同日，先于应用部署已完成）**：①`www.searchpipe.tech` 独立 server 块 → 301 主域（此前 www 直接 200，属重复内容）；②`listen 443 ssl http2`（此前仅 HTTP/1.1）；③80 端口一律 301 到 `https://searchpipe.tech`（一步到位）；④新增 `/etc/nginx/conf.d/gzip.conf`（`gzip_types` 覆盖 css/js/json/xml/svg + `gzip_vary`，此前静态资源明文传输）；⑤安全头 HSTS/X-Content-Type-Options/Referrer-Policy/X-Frame-Options。实测 `http_version=2`、css/js/sitemap 均 `content-encoding: gzip`、www 与 http 均 301 归一。
- **坑（重要）**：`/etc/nginx/sites-enabled/` 里长期堆着历史 `.bak` 文件，而该目录被 `nginx.conf` 全量 include——**备份文件与线上配置抢同名 server_name**（本次 reload 实测报 `conflicting server name "www.searchpipe.tech" on [::]:80`）。已把全部备份挪到 `/root/nginx-backups/`，**以后备份不要放 sites-enabled**。
- **待办**：Google Search Console / Bing Webmaster / 百度搜索资源平台尚未提交 sitemap（需账号操作）；验证码填 `.env` 的 `*_SITE_VERIFICATION` 后重启 app 即渲染 meta。

## 邮箱验证限制与统计修复部署（2026-09-13）

- **应用部署（main e6b722e）**：Windows 环境通过 tar+scp 同步代码（无 rsync），`docker build` → `docker compose -f docker-compose.prod.yml up -d` 重启 app。
- **验证结果**：
  - 未验证邮箱用户调用 `/search`（JWT/API Key）返回 403「请先验证邮箱才能使用此功能」✓
  - 已验证邮箱用户正常调用 `/search` 返回搜索结果 ✓
  - `/usage` API 正常返回近 30 天统计数据 ✓
  - dashboard.html 统计 JS 改为 DOMContentLoaded 事件触发，确保 AIS 对象就绪后执行
- **MCP 接入限制**：mcp_server.py 中 ai_search_search tool 增加 require_email_verified 检查，未验证用户调用 MCP tool 将返回 ToolError。

## 虎皮椒支付接入生产（main 1283c40→368f279，2026-09-11）

- **背景**：拿到虎皮椒真实商户（**仅微信渠道**，appid 201906187427）。代码改造见 history/20260911.txt（动态渠道 + 签名修复）。
- **部署**：dev→main 快进合并 → rsync → 服务器原生 build → `up -d`；生产 `.env` 只补了 `XUNHUPAY_APPID_WECHAT`/`XUNHUPAY_APPSECRET_WECHAT` 两个值（`XUNHUPAY_NOTIFY_URL=https://searchpipe.tech/payments/callback` 首发时已就位）。
- **验证（Playwright 打 https://searchpipe.tech，6/6 全过）**：注册冒烟用户 → 登录 → /dashboard/billing 页头「支持微信支付。」→ 确认弹窗仅「微信支付」单选且默认勾选 → 真实下单 ¥10 档返回虎皮椒微信收银台 URL（id=20306963140…）→ 收银台 HTTP 200。API 侧 /payments/catalog 返回 `pay_channels:["wechat"]`，/terms /pricing 文案已是微信支付。
- **浏览器实测抓到的 2 个真 bug**（pytest 断言 HTML/JSON 测不出）：
  1. billing.html 内联 IIFE 同步调 `AIS` 而 app.js 是 defer → 首屏套餐「加载失败」（api_keys.html 同款旧坑的遗留）→ 包进 `DOMContentLoaded`（1d050a1）。
  2. `CreateOrderResponse` 无 `pay_channel` 字段 → 前端待支付提示 `d.pay_channel==='wechat'?…` 恒假，微信单显示成「支付宝」→ 响应补该字段（368f279）。
- **冒烟清理**：2 笔 pending 订单 + 冒烟用户的 account/lot/tx 已删（备份在生产机 `/root/smoke-user-backup-20260911.json`）；users 剩真实用户 1 个，孤儿订单 0。
- **注意**：真实微信付款后的异步回调链（虎皮椒→`/payments/callback`→发积分）尚未走真钱验证，原理与测试一致（验签=同一 `_sign`），待首笔真实收款时观察日志确认。

## 退款声明整改 + 充值邮箱验证门禁部署（main a4394c6，2026-09-14）

- **背景**：history/20260912.txt 需求 6（取消「一经售出概不退款」类法律无效声明：terms/pricing/landing/billing 全站整改）与需求 7（POST /payments/orders 增加 require_email_verified 门禁 + /dashboard/billing 未验证用户横幅引导）。dev 全量 pytest 184 passed / 5 skipped，测试服已先行验证（见 searchpipe-test-server.md 十二次部署）。
- **部署**：dev 快进合入 main（6b22b30→a4394c6，无冲突）→ rsync（work.pem）→ 服务器原生 `docker build`（~30s）→ `docker compose -f docker-compose.prod.yml up -d app`。无迁移。
- **验证（外网 HTTPS + SSH 内网全过）**：healthz 内外双通（注意 compose up 后 app 有数十秒启动窗口，外网会瞬时 502，等 healthy 再验）；/terms、/pricing、/ 落地页「概不退款/不予退款」**0 残留**，产品规则（不支持自动续订/永久有效）与 meta description 正常；未验证用户 POST /payments/orders **403**「请先验证邮箱才能使用此功能」；未验证 /dashboard/billing 横幅 + 重发按钮命中；Redis 按 user_id 精确匹配 verify:token 验证后横幅消失、页面正常；容器日志无 error。
- **坑/注意**：①SSH 远程脚本里 `UID` 是 bash 只读变量，别拿来存用户 id（踩实）；②生产 Redis 里 verify:token:* 有多个用户 token，`head -1` 取会拿错导致 verify_error——要按 GET 值（user_id）匹配；③**验证时观察到 2026-09-11 记录中的真实用户 `57307365-…` 已不在 users 表**（其 API Key/积分记录亦随之不在；本次所有清理均按 verify% 邮箱/测试 UUID 限定，未触碰该用户，应为负责人自行注销），当前仅剩真实用户 ferriswym@163.com（未验证，受门禁限制）；④测试用户已按无 FK 约束纪律清干净（users 剩 1）。
- **真实下单未测**：避免真实虎皮椒下单，放行路径由 pytest（mock provider）覆盖。

## 品牌图标换新部署（main 278108e → 080b816，2026-09-11）

- **背景**：用户提供 `searchpipe-brand/` 品牌包（钥匙形 logo，替换原 🔍 放大镜 emoji 风格）。favicon 全系（svg/ico 16+32+48/favicon-16/32.png/apple-touch-icon/icon-192+512）、webmanifest 补 icon-192、og-image 左上角小图标按新品牌重绘（PIL 抹除旧图标+8x 超采样绘制，其余像素不动）；6 个模板（base/base_public/login/register/forgot/reset）🔍 全换内联 SVG（currentColor，css 新增 `.brand-mark`）；app.css 版本号 bump `?v=20260914` 打爆旧缓存。品牌源文件已入仓库 `searchpipe-brand/`。
- **部署**：dev（278108e）→ main 快进 → 测试服 rsync+build+up 验证 → 生产 rsync（work.pem）→ build → `up -d app`。
- **验证（测试服 SSH 内网 + 生产内外网 + Playwright 截图全过）**：healthz 200；favicon.ico/svg、apple-touch-icon、favicon-16/32、icon-192/512、webmanifest（含 icon-192）、og-image（1200×630 新图标）全部 200 且 content-type 正确；落地页/登录页 `brand-mark` 渲染、🔍 emoji 0 残留；app.css 含 `.brand-mark` 且 `Cache-Control: no-cache`；生产 `https://searchpipe.tech` 外网复验一致；容器日志无 error。全量 pytest 181 passed / 5 skipped。

## 坑/注意
1. **境外引擎现实**：机房 IP 下 brave 爬搜常 429（自动 Suspended 180s 降级）、wikidata init 403、google cse 未配 key 天然失败——兜底靠 bing+wikipedia，实测召回仍够（30 候选）。要提质有两条路：配 Brave Search API key（free tier）或 Google CSE key 填进 searxng/settings.yml 对应引擎。
2. 1.6G 内存跑全栈 + aitrendwatch：available 常年 ~450Mi，和测试服同等吃紧；OOM 先加 swapfile。
3. app 绑 127.0.0.1:8001 后，**外部无法直连 8001 排障**，一律 SSH 上去 curl 127.0.0.1:8001。
4. ~~www.searchpipe.tech 证书已含但 nginx 只做 https 跳转不做归一~~ **2026-09-13 已修**：www 独立 server 块 301 归一到主域（证书是同一张，含 www SAN）。
5. 支付回调/邮件链接全部依赖 APP_BASE_URL，换域名只改 .env 三处（APP_BASE_URL/OAUTH_REDIRECT_BASE/XUNHUPAY_NOTIFY_URL）。

## 出海版生产部署（main 9e67790 → 74040b0，2026-09-14）

- **背景**：Creem 商家申请需提交网站审核 → 出海版直接上生产。`overseas` 合入 `main`（fast-forward）后按本文件部署流程执行。
- **步骤**：rsync → 迁移前 pg_dump 备份（`/opt/searchpipe/backup-pre-overseas-20260911-184357.sql`）→ 生产 `.env` 改 `PAYMENT_PROVIDER=creem`（密钥待 Creem 审核后补；`XUNHUPAY_*` 残留项已被 config `extra="ignore"` 忽略）→ 服务器原生 `docker build` → `docker compose -f docker-compose.prod.yml up -d`；entrypoint 自动迁移到 `g1a2b3c4d5e6`（旧 7 个 CNY 档 `is_active=false`，新 USD 档种子生效）。
- **验证**：healthz 200；外网 7 个公开页（/ /pricing /docs /mcp-server /faq /terms /privacy）全 200 且零中文；catalog 返回 USD 新套餐 + `pay_channels=["card","paypal"]`（Creem 空密钥下 provider 仍可构造）；/sitemap.xml、/llms.txt 含 /privacy；/search 未认证 401；容器日志无 error。
- **注意**：`OAUTH_GITHUB_CLIENT_ID` 在生产 .env 里为空 → 登录/注册页不显示 OAuth 按钮（预期行为）；要开 GitHub/Google 登录需先建 OAuth App（回调 `https://searchpipe.tech/auth/oauth/{github|google}/callback`）再填 .env。Creem 凭证到位前下单会 502（catalog 优雅降级不挂页）。

## Creem 收款开通 + 自定义充值按分计价修复（main → 1016fea，2026-09-12）

- **Creem 后台（经 live API 操作）**：既有 `Credits (Pay-as-you-go)` = `prod_29JsUAMP9ZE6tYSr0HLRIK`（$1/onetime）；新建 3 充值档（Recharge $5/$10/$20 → `prod_1tdF1iRHpblpRzGYi52hLx` / `prod_7XFQxetCdyymxePpiduAvf` / `prod_4GZRlvxgs2pD4HVuh5axsv`）+ 3 订阅档（Starter/Pro/Max → `prod_f9w26mFHcphDUBEf65AYr` / `prod_2Z2Rc1YT8TiNvDbDNu5uAz` / `prod_3OInC8GbchcOipNy6WRUuD`）。**一次性产品创建时必须省略 `billing_period`**（传 `once` 会被判 recurring 报 400）；`abandoned_cart_recovery_enabled` 只在创建时可设、PATCH 报 `should not exist`（只能后台 UI 开）。
- **生产 `.env`**：补 `CREEM_API_KEY` / `CREEM_WEBHOOK_SECRET` / `CREEM_API_BASE` / `CREEM_CREDIT_PRODUCT_ID`（**原来根本没有 CREDIT_PRODUCT_ID 这一项**，缺它自定义充值必失败）。DB 回填 6 条 `plans.provider_products`（`UPDATE 6`）。
- **修复的 bug**：自定义充值非整数美元（如 $3.50）被 400 拒绝——provider 要求整美元而 billing.html 允许 step=0.01。改为按分计价（Creem `custom_price`、Dodo `product_cart[].amount`）+ 补 $1 下限（config `MIN_RECHARGE_USD`、catalog 新增 `min_recharge_amount`、billing.html 同步）。
- **测试**：全量 **208 passed, 5 skipped**。⚠️ 本机（Windows 工作副本）无 docker/venv/WSL，跑法是在**测试服**用隔离环境：`git archive` 打包 → 测试服 `CREATE DATABASE ai_pytest` + redis db9 → `docker run --entrypoint /bin/sh`（覆盖镜像 ENTRYPOINT，否则会被 entrypoint.sh 接管）+ 挂载源码 + `PYTHONPATH=/src/src:/usr/local/lib/python3.13/site-packages`（镜像 venv 无 pip，pytest 装系统 site-packages）→ `pip install pytest pytest-asyncio` → `alembic upgrade head` → `pytest tests/ -q`；跑完删库/删容器/清 redis。
- **顺带修掉 main 上既有 2 个失败**：`test_renewal_via_webhook` / `test_creem_style_subscribe_flow` 的余额断言是 `4b3f4d1` 机械 +200 漏改（续期场景 Starter 出现两次，应 +400）；A/B 实证未改动的 main 上同样 2 failed → 与本次修复无关。
- **部署**：Windows 侧 `git archive` 打包（无 rsync）→ scp → `/opt/searchpipe` 解压（`.env` 未动）→ `docker build --build-arg UV_COMPILE_BYTECODE=0`（23.7s）→ `up -d app`；代码级备份 `code-backup-pre-recharge-fix-20260912-180417.tgz`。分支不含新迁移，无需 alembic 动作。
- **验证（生产真实 HTTP）**：healthz 200；catalog `min_recharge_amount:1`；测试用户注册→Redis 取 token→验证邮箱→建单：**自定义 $3.50 → 201**（credits 700.00）、**$0.50 → 400「Minimum recharge is $1」**、自定义 $5 与固定档 Recharge $5 → 201；3 笔 pending 订单落库；测试数据定向清理（users 仅剩真实用户 `ferriswym@163.com`）。**Creem 侧复核**：`GET /v1/checkouts?checkout_id=ch_5QHPyvy41fJCyNjb8FXUo` → `custom_price: 350, units: 1, mode: prod`。
- **同期修复的生产阻塞**：SMTP 认证失败（`526 Authentication failure`，生产与测试服同因）→ 更新两台 `SMTP_PASSWORD` 后 `LOGIN OK` + 真实发信成功。**教训：支付上线验收必须包含「注册→收到验证邮件」，否则新客卡在邮箱门禁无法下单。**

## 修复「付费按键点击后没有后续」（main → db1c840，2026-09-12）

- **现象**：正式环境点所有付费按键「没有后续」。日志显示其实**创建成功**（31 秒内 5 笔 pending 订单 + 前端轮询），问题在下单后的反馈。
- **根因**：`billing.html` 的 `confirmOrder()` 下单成功后只在**页面顶部**渲染 "Pending order" 卡片（含 Creem 链接），付费按钮却在套餐区、用户已滚到下方 300+px（实测 `cardTop=-329 / cardInViewport=false / scrollY=691`），卡片在视口外且**不做任何跳转**；按钮文案却是 "Proceed to payment"。
- **修复**：`window.location.assign(d.pay_url)` 同标签跳转收银台（await 后用 `window.open` 会被拦截）；保留 pending 卡片 + `scrollIntoView` + 轮询兜底。
- **验证**：全量 208 passed（测试服隔离库）；真浏览器复测 → 实际跳转到 `www.creem.io/checkout/...`，标题 `Creem`、内容为 Starter $4.99/月。
- **真浏览器验收方法（本机可用，值得复用）**：Playwright 在本沙箱因命名管道被拒（`WinError 5`），改用系统 Edge + CDP：
  `msedge.exe --headless=new --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=<临时目录>`
  再用 Python `websocket-client` 连 `http://127.0.0.1:9222/json` 取 page target 的 `webSocketDebuggerUrl`，发 `Page.navigate` / `Runtime.evaluate` / `Page.captureScreenshot` 驱动。
  ⚠️ 坑：①`--remote-allow-origins=*` 不加会 403 拒绝 WebSocket；②`$env:TEMP` 在放宽权限后会变回真实临时目录，脚本里要用绝对路径；③dashboard **登录表单必须勾选 `agree_terms`**，否则后端只重渲染登录页（`agree_terms != "on"`），会误判成「登录失败」。
- **教训**：pytest 全绿也测不出这类问题——**「下单成功」≠「用户被带到支付页」**；点击→跳转→收银台可达属于必须真浏览器验收的关键路径。
- **现场遗留**：负责人点击产生的 5 笔 pending 订单（10:51，均未支付、不发积分）保留未动。

## 落地页双栏对齐修复部署（main/dev → 50cb0d3，2026-09-12）

- **背景**：用户反馈落地页「One-command MCP setup」区块两栏不对齐——左栏两个代码块、右栏三张要点卡高度参差、顶底不齐，「Claude Code/Cursor」标签是带内联 style 的散落 `<p>` 与代码块脱节；feature 卡文字长短不一导致高度不等。顺带检查并优化落地页其它区块的内联样式。纯 HTML/CSS 模板改动，无 Python 逻辑、无迁移、无 `.env` 变更。
- **改动**：新增专用类替换内联 style——`.split`/`.split-col`（双栏 `align-items:stretch` 等高对齐）、`.code-label`（代码块标签）、`.feature-stack`（要点卡纵向 flex）、`.feature` 改 `display:flex;flex-direction:column` + `p{flex:1}`（卡片等高、正文撑满）、`.section-note`/`.plans-head`/`.faq-narrow`/`.per`/`.cta-band .cta-row`；响应式 `@media(max-width:900px)` 增加 `.split` 单列降级；MCP 区与「Designed for agents」区改用 `.split`；定价 `/mo` 后缀、订阅说明、FAQ 宽度、CTA 行的内联 style 全部移除；`app.css?v=` 缓存版本号 `20260914→20260915`（base_public.html + base.html）。涉及文件：`src/ai_search/dashboard/static/app.css`、`templates/landing.html`、`templates/base_public.html`、`templates/base.html`。
- **提交/推送**：在 `fix/billing-proceed-to-payment` 分支提交 `50cb0d3`（排除未跟踪的 `searchpipe-deploy.tar.gz` 部署包）→ fast-forward 合入 `main` 与 `dev`（三者原在同一 commit `f91a418`，干净快进）→ `git push origin main` + `git push origin dev` 均到 `50cb0d3`。生产跟随 `main`。
- **部署（Windows 无 rsync，tar+scp）**：本机 `git archive --prefix=searchpipe/ HEAD` 打包（自动排除 .venv/.git/__pycache__ 与未跟踪 tarball，且不含 .env）→ scp 到 `/tmp/` → 服务器先 `tar czf` 备份 `/opt/searchpipe`（排除 .env）→ 解压。**坑（重要）**：首次解压误用 `tar xzf ... -C /opt/ --strip-components=1`，把代码散落到 `/opt/src`、`/opt/tests` 等 `/opt/` 顶层（而非 `/opt/searchpipe/`）；且 `rm -rf /opt/searchpipe/*` 的通配符不删隐藏文件（.env/.dsh 幸免）。修正：重新 `tar xzf ... -C /opt/searchpipe/ --strip-components=1` 正确落位；散落在 `/opt/` 顶层的同名目录经时间戳核对为**更早一次部署的历史残留**（非本次产生，prod 一直从 `/opt/searchpipe/` 正常运行），未触碰以免误删。另：`tar --exclude` 必须放在归档目标之前否则报「has no effect」退出；远程脚本含反引号/`$()` 须写成 .sh 文件 scp 过去执行，避免 PowerShell 解析反引号。验证 `.env` 仍为 2856 字节原时间戳、`base_public.html` 含 `app.css?v=20260915`、`app.css` 含 `.split`（2 处）后 → `docker build --build-arg UV_COMPILE_BYTECODE=0`（31.7s）→ `docker compose -f docker-compose.prod.yml up -d app` → ~24s healthy，日志无 error、healthz 200。
- **验证（外网 HTTPS + 真浏览器截图全过）**：`https://searchpipe.tech/static/app.css?v=20260915` 200 且含 `.split`/`.code-label`/`.feature-stack`/`.per` 及响应式 `.split` 规则；`https://searchpipe.tech/` 200 全英文、各区块完整；headless Edge `--screenshot`（无需 websocket-client，比 CDP 法更省事）截全页确认 MCP 区两栏顶对齐、三张要点卡等高、「Designed for agents」同款对齐、pipeline/定价/FAQ 正常。无 DB/Redis 改动，未跑 pytest（纯模板改动 + 本 checkout 无 .venv）。
- **教训**：①tar 解压目标目录务必与 `--strip-components` 配套算清层级，先在测试服或 dry-run 核对落点再对生产 `rm -rf`；②`rm -rf dir/*` 不删 dotfile，要清隐藏文件用 `rm -rf dir/.??* dir/*` 或先 `find`；③PowerShell 调 ssh 远程 shell 时反引号/`$` 会被本地解析，复杂远程脚本一律落盘成 .sh 再 `bash` 执行。

### 追加：MCP setup 改为「代码块在上 + 3 要点卡横排一行」（main/dev → d0636c6，2026-09-12）

- **用户反馈**：上一版只是把左右两栏对齐工整了，但**左 2 行 / 右 3 行的行数不对称本身没解决**——这才是「不美观」的根源。经确认（ask_user_question）采用「要点卡横排成一行」方案，且**只改 MCP setup 这一块**，「Designed for agents」（1 代码块 + 2 卡）保持不动。
- **改动**：MCP 区块弃用 `.split` 双栏，改为 `.mcp-codes`（代码块居中堆叠在上，max-width 680px）+ `.mcp-features`（复用 `.feature-grid` 并固定 `grid-template-columns: repeat(3, 1fr)`，3 张卡横排成一行在下）；响应式 `@media(max-width:900px)` 下 `.mcp-features` 回落单列；CSS 缓存版本号 `20260915→20260915b`。涉及文件同上一版 4 个。
- **部署/验证**：同流程 tar+scp（这次按上条教训直接 `-C /opt/searchpipe/ --strip-components=1` 正确落位、无散落）→ build 25.6s → healthy；外网 `app.css?v=20260915b` 含 `.mcp-codes`/`.mcp-features`；headless Edge 截图确认两块代码在上、三张要点卡等高横排在下、左右行数不对称消除。提交 `d0636c6` 已推 main+dev。
- **教训**：**「对齐」≠「结构合理」**——用户说「不对齐/不美观」时要先判断是间距对齐问题还是行列结构失衡问题；后者光调对齐解决不了，得改 grid 结构。拿不准布局方向时先用 ask_user_question 给方案选项确认，别自己猜（本次第一版就猜错了方向）。
