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

## 坑/注意

1. **境外引擎现实**：机房 IP 下 brave 爬搜常 429（自动 Suspended 180s 降级）、wikidata init 403、google cse 未配 key 天然失败——兜底靠 bing+wikipedia，实测召回仍够（30 候选）。要提质有两条路：配 Brave Search API key（free tier）或 Google CSE key 填进 searxng/settings.yml 对应引擎。
2. 1.6G 内存跑全栈 + aitrendwatch：available 常年 ~450Mi，和测试服同等吃紧；OOM 先加 swapfile。
3. app 绑 127.0.0.1:8001 后，**外部无法直连 8001 排障**，一律 SSH 上去 curl 127.0.0.1:8001。
4. ~~www.searchpipe.tech 证书已含但 nginx 只做 https 跳转不做归一~~ **2026-09-13 已修**：www 独立 server 块 301 归一到主域（证书是同一张，含 www SAN）。
5. 支付回调/邮件链接全部依赖 APP_BASE_URL，换域名只改 .env 三处（APP_BASE_URL/OAUTH_REDIRECT_BASE/XUNHUPAY_NOTIFY_URL）。
