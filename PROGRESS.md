# ai-search 项目进度与计划

> 最后更新：2026-08-22（MCP Server 封装完成）
> 项目路径：`/home/wuyuming/Projects/ai-search/`
> 定位：自建 AI 搜索服务（Tavily 风格），长期目标是封装成 MCP + Skill 对外开放

---

## 一、项目背景与定位

### 要解决的问题
没有现成搜索 API 时，如何搭建一个**稳定、可商用、面向 LLM/RAG** 的搜索引擎，再封装成工具给 AI Agent 用。

### 调研结论
- **Tavily 的本质**：不自建 500TB 全网索引，而是做「聚合别人的搜索结果 + AI 清洗重排」这一层。一次 API 调用完成搜索 → 抓取 → 过滤 → 提取 → 排序，直接给 LLM 喂结构化内容。
- **合规性**：Brave Search / SerpApi 等商业源 TOS 禁止转售，自建转售有法律风险。
- **合规路线**：自建 **SearXNG**（AGPL 协议，可商用、零 API 费）作为检索源，是唯一干净的可商用方案。

### 技术选型
| 层 | 选型 | 理由 |
|---|---|---|
| 检索源 | 自建 SearXNG（Docker） | 唯一合规可商用、零 API 费 |
| 抓取清洗 | trafilatura（纯 Python） | 轻量、无需浏览器，MVP 足够；JS 重页面后续接 Crawl4AI |
| LLM 重排/摘要 | DeepSeek（OpenAI 兼容 SDK） | 国内网络友好、成本低 |
| 后端框架 | FastAPI + httpx（异步） | 标准、轻量 |
| 依赖管理 | uv | 快，符合用户环境 |

---

## 二、架构

```
POST /search
  → 检索编排（SearXNG 多引擎聚合：bing + baidu）
  → 抓取清洗（trafilatura 提取 Top-N 正文）
  → LLM 重排（DeepSeek 打相关性分 0-1，按分排序）
  → 可选摘要（include_answer 时基于结果生成带引用的 answer）
  → Tavily 风格结构化结果
```

### 目录结构

```
ai-search/
├── docker-compose.yml          # SearXNG + Redis 一键起
├── searxng/settings.yml        # SearXNG 配置（已开 JSON API、启用 bing+baidu）
├── pyproject.toml              # uv 管理依赖
├── .env / .env.example         # 配置（含真实 LLM key）
├── src/ai_search/
│   ├── main.py                 # FastAPI 入口，/search 路由
│   ├── config.py               # pydantic-settings，get_settings() 单例
│   ├── schemas.py              # SearchRequest / SearchResult / SearchResponse
│   ├── search/
│   │   ├── orchestrator.py     # SearchOrchestrator.gather_candidates()
│   │   └── searxng_client.py   # SearXNGClient（异步 httpx）
│   ├── extract/fetcher.py      # Fetcher.fetch_batch() + fetch_url_text()
│   ├── rerank/llm_reranker.py  # LLMReranker.rerank() + generate_answer()
│   └── utils/{cache,logger}.py # 日志 + 缓存占位
└── tests/test_search.py        # 端到端冒烟测试
```

### 核心接口（可被 MCP 复用）

| 组件 | 类 | 公开方法 |
|---|---|---|
| 检索 | `SearchOrchestrator(settings)` | `async gather_candidates(query, *, max_results) -> list[SearchResult]` |
| 抓取 | `Fetcher(settings)` | `async fetch_batch(urls, *, concurrency=5) -> dict[url, 正文/None]` |
| 重排 | `LLMReranker(settings)` | `async rerank(query, candidates) -> list[SearchResult]` |
|  |  | `async generate_answer(query, results) -> str/None` |
| 配置 | `get_settings()` | 单例 `Settings` |

`main.py:search()` 端点的编排流程就是上述四步的串联，MCP 层可直接抽成独立 async 函数复用，无需走 HTTP。

### 配置项（`.env`）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SEARXNG_URL` | `http://localhost:8081` | SearXNG 实例地址 |
| `SEARXNG_PROXY` | （空） | 出站代理，后续防 CAPTCHA |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点 |
| `LLM_API_KEY` | — | DeepSeek key |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `MAX_RESULTS` | 5 | 默认返回结果数 |
| `FETCH_TOP_N` | 5 | 抓取正文的 URL 数 |
| `REQUEST_TIMEOUT` | 20 | 单次请求超时（秒） |
| `LLM_TIMEOUT` | 30 | LLM 调用超时（秒） |
| `REDIS_URL` | `redis://localhost:6379/0` | 缓存（暂未启用） |
| `ENABLE_CACHE` | false | MVP 未实装 |

---

## 三、已完成进度（MVP）

### ✅ 全链路跑通验证（2026-08-21）

| 环节 | 状态 | 备注 |
|---|---|---|
| SearXNG JSON API (`:8081`) | ✅ 200 | |
| 启用引擎 bing + baidu | ✅ 19 条结果 | google/ddg/wikipedia 国内超时，已禁用 |
| trafilatura 抓取正文 | ✅ 3/5 成功 | 知乎 403 属正常反爬 |
| DeepSeek LLM 重排 | ✅ 200 | top score=1.0 |
| DeepSeek LLM 摘要 | ✅ 200 | 带引用标注 `[0][2][3]` |
| pytest 冒烟测试 | ✅ 3 passed | healthz / 结构校验 / 空查询 422 |

### 验证中修复的问题

1. **SearXNG 默认禁用 JSON 格式** → `settings.yml` 加 `- json`
2. **默认引擎全超时**（google/duckduckgo/wikipedia 国内不可达）→ 启用 `bing`（走 `cn.bing.com`）+ `baidu`
3. **LLM 401** → 写入真实 DeepSeek key 到 `.env`，重启后通过
4. **SearXNG 容器端口冲突** → compose 映射 `8081:8080`（宿主 8080 已被占用）
5. **dev 依赖未装** → `uv sync --extra dev` 装 pytest

### 实际输出示例

查询 `"Tavily 是什么"`，`include_answer=true`：
- **answer**：`Tavily 是一个专为 AI Agent 和大型语言模型（LLM）优化的搜索引擎 API...[0][2][3]`
- **results**：5 条，score 分别 1.0 / 1.0 / 0.9 / 0.8 / 0.8

---

## 四、MCP Server 封装（已完成 2026-08-22）

### 成果
把 `/search` 能力封装成 fastmcp stdio server，暴露 `ai_search_search` tool，Claude Code / Cursor 等 MCP 客户端可直接调用，无需走 HTTP。

### 改动
1. **抽取共享编排** `src/ai_search/core/search_service.py`：把 `main.py` 的五步流程 + 组件单例搬到 `run_search(req) -> SearchResponse`，FastAPI 端点与 MCP tool 共用同一套逻辑（单一真相源）。
2. **`main.py`** 瘦身为端点薄包装：`try: run_search(req)` + `except → HTTPException(502)`。
3. **`src/ai_search/mcp_server.py`**：`FastMCP("ai-search")` + `@mcp.tool() ai_search_search`，参数对齐 `SearchRequest`，返回 `SearchResponse`（fastmcp 自动序列化为 text + structuredContent + output schema）。日志走 stderr（stdio 下 stdout 是协议流）。
4. **`utils/logger.py`** `setup_logging` 加 `stream` 参数（默认 stdout 兼容现状，MCP 传 stderr）。
5. **`pyproject.toml`**：加 `fastmcp>=2.14,<4`（传递性带入官方 `mcp` SDK）+ `[project.scripts]` 入口 `ai-search-mcp = "ai_search.mcp_server:main"`。
6. **`.mcp.json`**：项目级注册，`uv run --directory <proj> ai-search-mcp`。

### 验证
- `uv sync` 通过；导入 `ai_search.mcp_server` / `ai_search.core.search_service` 正常。
- `uv run pytest`：2 passed + 1 skipped（重构未破坏端点）。
- fastmcp in-process client：`tools/list` 看到 `ai_search_search`，input schema = `{query, max_results, include_answer, include_raw_content}`，output schema 匹配 `SearchResponse`。
- **端到端真实调用**（SearXNG + DeepSeek 在线）：`query="Tavily search API"` → `isError=False`，3 条结果（score 1.0/0.9/0.7），带引用摘要，抓取 3/3 成功。
- 检索源失败时 `run_search` 抛异常 → fastmcp 自动转 MCP tool error。

### 接入 Claude Code
项目内启动 `claude` → `/mcp` 应看到 `ai-search` 已连接 → 让 Claude 用 `ai_search_search` 搜索即可（`.mcp.json` 已就位）。

### 设计选择记录
- SDK 用 **fastmcp**（装饰器风格、自动 schema 生成）。
- `search_depth` 暂不暴露给 MCP tool（MVP no-op），等 `advanced` 实现再加。
- transport 仅 stdio（本地客户端足够）。

---

## 五、后续迭代路线（暂未实现）

按优先级：

1. **稳定性**：多源兜底 + 公共 SearXNG 实例池（单实例易被封）
2. **反爬**：住宅代理 / Crawl4AI 处理 JS 重页面（知乎这类 403）
3. **性能**：Redis 缓存实装（`utils/cache.py` 已留占位）
4. **更多端点**：`/extract` `/crawl` `/map`（对齐 Tavily 完整 API）
5. **封装**：MCP server（进行中）→ Claude Skill
6. **商业化**：鉴权 / 计费 / 限流

---

## 六、运维速查

### 启动服务
```bash
cd /home/wuyuming/Projects/ai-search
docker compose up -d                          # SearXNG + Redis
uv run uvicorn ai_search.main:app --port 8000 # FastAPI
```

### 验证
```bash
curl http://localhost:8000/healthz
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Tavily 是什么","max_results":5,"include_answer":true}'
uv run pytest
```

### 容器
```bash
docker ps --filter name=ai-search
# ai-search-searxng  :8081->8080
# ai-search-redis    :6379
```

### 已知限制
- 知乎等反爬站点 403，trafilatura 抓不到正文（需后续 Crawl4AI / 代理）
- google/duckduckgo/wikipedia 引擎国内超时，当前仅 bing+baidu 出结果
- SearXNG `settings.yml` 在容器内以 root 修改并持久化到宿主机挂载卷（宿主文件属主 root）
