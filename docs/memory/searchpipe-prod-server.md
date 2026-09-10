# searchpipe 生产服与部署

> 类型：reference · 写入：2026-09-10（生产首发部署完成后定稿）

## 生产服

**`https://searchpipe.tech`** —— 远端阿里云境外主机（Ubuntu 22.04，x86_64，1.6G 内存小机器，与测试服同规格）。

- **SSH**：`ssh -i /home/wuyuming/Projects/work.pem root@47.89.243.229`（root 可登录；work.pem 是生产机钥匙，test_host.pem 是测试机的，别混）
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

## 首发验证记录（2026-09-10）

- 迁移建表 5 段全过（init→email_verified）；healthz ok（内网+经 nginx HTTPS 双路）
- 落地页/SKILL.md/robots 正常，**SKILL.md 引用已是 https://searchpipe.tech**（APP_BASE_URL 生效）
- 全链路冒烟：注册 201 → 验证邮件实发（阿里企业邮箱境外可达）→ 登录 → 建 sp- Key → /search 200（8s，SearXNG 返 30 候选）→ 扣费 1 积分流水对账 → MCP initialize 过 nginx SSE 握手正常（裸 GET /mcp/ 406 属正常，要带 Accept: application/json, text/event-stream）
- 中英文多 query 召回正常（7-11s/次）
- 已清理冒烟用户（users 归零）

## 坑/注意

1. **境外引擎现实**：机房 IP 下 brave 爬搜常 429（自动 Suspended 180s 降级）、wikidata init 403、google cse 未配 key 天然失败——兜底靠 bing+wikipedia，实测召回仍够（30 候选）。要提质有两条路：配 Brave Search API key（free tier）或 Google CSE key 填进 searxng/settings.yml 对应引擎。
2. 1.6G 内存跑全栈 + aitrendwatch：available 常年 ~450Mi，和测试服同等吃紧；OOM 先加 swapfile。
3. app 绑 127.0.0.1:8001 后，**外部无法直连 8001 排障**，一律 SSH 上去 curl 127.0.0.1:8001。
4. www.searchpipe.tech 证书已含但 nginx 只做 https 跳转不做归一，对外统一宣传 searchpipe.tech。
5. 支付回调/邮件链接全部依赖 APP_BASE_URL，换域名只改 .env 三处（APP_BASE_URL/OAUTH_REDIRECT_BASE/XUNHUPAY_NOTIFY_URL）。
