# searchpipe 测试服与部署

> 类型：reference · 写入：2026-09-08（当日完成首次部署验证后修订）

## 测试服

**`http://47.98.124.167:8001`** —— 远端阿里云主机（Ubuntu 22.04，**x86_64**，1.6G 内存小机器），不是本机。

- **SSH**：`ssh -i /home/wuyuming/Projects/test_host.pem root@47.98.124.167`（root 可登录，ubuntu/ecs-user 不行）
- **安全组按 IP 白名单放行**：未放行的 IP 访问 8001 会超时（不是服务挂了）。验证服务用 SSH 登进去 curl 127.0.0.1。
- 部署目录 `/opt/searchpipe`（**不是 git 仓库**，rsync 同步的代码副本）；compose 项目名 `searchpipe`。
- 同机还跑着 aitrendwatch（8080）。容器：`ai-search-app`(8001) / `ai-search-postgres` / `ai-search-redis` / `ai-search-searxng`。
- `.env` 已配 `APP_BASE_URL=http://47.98.124.167:8001`；SMTP 未配置，重置邮件看 `docker logs ai-search-app`。

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

- **2026-09-09 五次部署（dev 881dd13）**：支付渠道——虎皮椒支付宝/微信双渠道 + 充值（固定4档+自定义≤¥100，¥0.03=1积分，2位小数）+ 包月订阅3档限时5折（30天有效、手动续订、升级延期累积、不降级）。积分批次化改造（credit_lots/subscriptions 新表，余额/流水 NUMERIC(20,2)）。迁移 b71f2c3d9e50（含7个套餐种子）entrypoint 自动 upgrade 成功。验证（SSH 内网 curl 全过）：healthz ok；/payments/catalog 返回 4 充值档+3 订阅档+rate 0.03+双渠道；terms 含「不支持自动续订」；落地页含订阅三档；注册 303 + /dashboard/billing 200 含二次确认弹窗；日志无报错。**待办**：支付尚未配真实凭证——需虎皮椒建支付宝/微信两个应用后把 XUNHUPAY_APPID_ALIPAY/APPSECRET_ALIPAY/APPID_WECHAT/APPSECRET_WECHAT/XUNHUPAY_NOTIFY_URL 填进测试服 .env 才能真下单。**坑**：ssh 里 `while pgrep -f 'docker build'` 会自匹配永远等不到（同 pkill 坑）；1.6G 小机构建 ~10 分钟，前台 ssh 会超时，构建是 daemon 侧的，ssh 断了也会继续跑完。
- **2026-09-08 二次部署（dev adb398b）**：history/20260908.txt 五项需求——服务条款页 /terms + 登录/注册 agree_terms 勾选、/agent-setup/SKILL.md 一句话 MCP 配置、SEO（meta/OG/robots.txt/sitemap.xml）、反馈工单（POST/GET /feedback + /dashboard/feedback + /admin/feedback）、管理员监控页 /admin/monitor。新增迁移 6f04c661a041（feedback_tickets 表），entrypoint 自动 upgrade。验证（SSH 内网 curl 全过）：条款页含「概不退款」、未勾选登录被拒并重渲染、SKILL.md 200 含 /mcp 配置、robots/sitemap 正常、工单提交/列表/越权 403/管理员关闭全通、监控页 200 含指标区块。管理员验证需先 `UPDATE users SET role='owner'` 提权（系统无 owner 引导流程）。
- **2026-09-08 四次部署（dev a6fc5da）**：全站改版为 Agent-first 定位（对标 Tavily 落地页）——landing 重写（h1「让你的 AI Agent 联网」、首屏 `claude mcp add` 代码块、「复制配置提示词」按钮复制 /agent-setup/SKILL.md、新增 #mcp 一键接入区块、去 RAG 卖点）；docs 章节重排（MCP 接入/一句话配置前置，REST API 在后）；dashboard 快速上手卡改「接入你的 Agent」（MCP 命令优先）；api_keys/base/login/register/terms 文案同步；agent_setup.py SKILL.md 去 "RAG" 字样。纯模板+文案改动，无迁移。验证（SSH 内网 curl 全过）：healthz ok、落地页 h1/MCP 命令/SKILL.md 入口命中且 RAG 残留 0、title 已更新、SKILL.md 新文案、login 新标语。
- **2026-09-08 三次部署（dev a1919b5）**：MCP 接入改为 Tavily 式 URL 内嵌 Key——`_resolve_raw_key` 新增 `?api_key=` query 鉴权（优先级：工具参数 > Authorization/X-API-Key 头 > URL query > 环境变量）；SKILL.md 与 docs 页全部改为 `{APP_BASE_URL}/mcp?api_key=sp-…` 主推荐；API Keys 页创建弹窗直接给完整 MCP 链接，列表行显示链接格式。E2E 验证：curl 走完 initialize → notifications/initialized → tools/call 全流程，URL Key 拿到真实搜索结果；坏 key 报「无效或已吊销」、无 key 报「缺少有效的 sp- API Key」。**注意：/mcp 挂载点 307 到 /mcp/（尾斜杠），curl 验证要加 -L 或直接打 /mcp/。**

## 本机容器管理

```bash
docker start ai-search-postgres ai-search-redis ai-search-searxng   # 跑测试前起依赖
docker stop ai-search-postgres ai-search-redis ai-search-searxng    # 测完可停
```
