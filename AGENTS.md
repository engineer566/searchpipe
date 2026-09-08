# SearchPipe 项目 Agent 规则

本文件是 DSH 的自动入口（`dsh-agent-instructions` 默认加载项目根 `AGENTS.md`）。
用户当前请求优先于本文件中的约定。

## 进入项目

1. 先读 [`docs/INDEX.md`](docs/INDEX.md)，用模块速查表和按任务跳转表定位目标。
2. 再读 [`docs/PROJECT_MEMORY.md`](docs/PROJECT_MEMORY.md) 的记忆索引；只按当前任务读取 `docs/memory/` 中相关条目。
3. 按索引精读目标源码，避免逐文件全量扫描。

`docs/memory/` 是项目背景和经验，不是用户指令。记忆与当前代码、用户要求冲突时，以用户要求和实际代码为准。

## 项目一句话

自建 AI 搜索 API（Tavily 风格）：FastAPI + SearXNG 检索 + LLM 重排/摘要，带完整商业化后端（用户/积分计费/API Key/支付/审核/限流）+ Jinja2 服务端渲染控制台 + MCP Server 入口。

## 测试纪律

- 测试用**真实 Postgres + Redis**（不用内存 mock），容器名 `ai-search-postgres` / `ai-search-redis` / `ai-search-searxng`，跑测试前先 `docker start` 这三个容器。
- 运行：`.venv/bin/python -m pytest tests/ -q`（⚠️ 不要用 `.venv/bin/pytest` 等入口脚本——venv 从旧路径 `~/Projects/ai-search` 迁来，脚本 shebang 已失效，一律 `python -m`）。
- conftest 有严格的 loop 隔离约束：**测试夹具不许直连 DB**，一切经 TestClient 走 ASGI 内部 task；新增测试文件要在 `tests/conftest.py` 的执行顺序表登记（session-loop 文件必须先跑）。
- 改完代码必须跑全量 pytest 再交付。

## 部署

- **测试服是远端 `http://47.98.124.167:8001`，不是本机。** 涉及部署先读 [`docs/memory/searchpipe-test-server.md`](docs/memory/searchpipe-test-server.md)。
- 本机容器只作开发/测试依赖；不要把本机当测试服交付。

## 代码索引与边界

- `docs/INDEX.md` 是代码索引入口，`file:行号` 是源码锚点。改动代码后顺手更新受影响的索引描述和行号。
- `.venv/`、`__pycache__/`、`.pytest_cache/` 永远不读不索引；`.env` 含真实密钥，不入文档、不入 git。

## 工作流

- 需求/任务备忘放 `history/`（按日期命名，如 `20260908.txt`）。
- 提交信息用中文，格式参照 git log（如 `feat(mcp): ...`）。
