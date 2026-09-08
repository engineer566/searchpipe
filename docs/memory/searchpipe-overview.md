# searchpipe 项目全景与开发约定

> 类型：project · 写入：2026-09-08

## 架构要点

- FastAPI 单体，同一进程三类入口：`/search`（JSON API）、`/dashboard`（Jinja2 SSR 控制台，session cookie 登录态）、`/mcp`（FastMCP streamable-http 子应用，lifespan 合并进主 app，见 `main.py`）。
- 检索管线：SearXNG（自建，bing+baidu）→ trafilatura 抓正文 → DeepSeek（OpenAI 兼容 SDK）重排/摘要。检索源选型 SearXNG 是合规结论（AGPL 可商用、Brave/SerpApi TOS 禁止转售），不要轻易换商业源。
- 商业化：积分制计费（注册送 `FREE_TIER_CREDITS`，basic/advanced 按次扣费，失败幂等退款）、虎皮椒支付过渡、阿里云内容审核（`MODERATION_ENABLED=false` 内测默认关）、Redis 滑动窗口限流（admin/owner 豁免）。
- 鉴权三通道：JWT（30min access + 30d refresh）、`sp-` 前缀 API Key、session cookie（7 天）——统一汇入 `AuthContext`（`auth/dependencies.py`）。

## 测试硬约束（违反会踩坑）

1. **真实 Postgres + Redis**，不做内存 mock。测试前 `docker start ai-search-postgres ai-search-redis ai-search-searxng`。
2. **夹具绝不直连 DB**——一切经 TestClient 走 ASGI app 内部 task，否则 asyncpg 报 "Future attached to a different loop"。
3. 新增测试文件必须在 `tests/conftest.py` 的 `pytest_collection_modifyitems` 顺序表登记；httpx.ASGITransport 类（session loop）文件必须排在 TestClient 类（portal loop）之前。
4. venv 从旧路径 `~/Projects/ai-search` 迁来，**入口脚本 shebang 全部失效**；一律用 `.venv/bin/python -m pytest|uvicorn ...`，不要直接调 `.venv/bin/pytest`。

## 常用命令

```bash
# 起依赖
docker start ai-search-postgres ai-search-redis ai-search-searxng
# 测试
.venv/bin/python -m pytest tests/ -q
# 本地起服务
.venv/bin/python -m uvicorn ai_search.main:app --host 127.0.0.1 --port 8001 --app-dir src
# 迁移
.venv/bin/python -m alembic upgrade head
```

## 项目沿革

- 仓库曾名 `ai-search`（`~/Projects/ai-search`），后改名 `searchpipe`；compose 项目名仍是 `ai-search`（容器/卷都带此前缀，部署时 `-p ai-search`）。
- 线上域名 searchpipe.tech（MCP 客户端经 https://searchpipe.tech/mcp 接入）。
