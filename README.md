# ai-search

自建 AI 搜索 API（Tavily 风格）—— 基于自建 SearXNG + LLM 重排，面向 LLM/RAG 场景。

## 架构

```
POST /search
  → 检索编排（SearXNG 多引擎聚合）
  → 抓取清洗（trafilatura 提取正文）
  → LLM 重排 + 摘要（DeepSeek 等 OpenAI 兼容模型）
  → Tavily 风格结构化结果
```

## 快速开始

### 1. 启动 SearXNG + Redis

```bash
docker compose up -d
# 验证 SearXNG JSON API
curl 'http://localhost:8080/search?q=test&format=json' | head -c 200
```

### 2. 配置环境

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（DeepSeek 等）
```

### 3. 安装依赖并启动

```bash
uv sync                # 或: pip install -e ".[dev]"
uv run uvicorn ai_search.main:app --reload --port 8000
```

### 4. 测试

```bash
# 健康检查
curl http://localhost:8000/healthz

# 搜索
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Tavily 是什么","max_results":5,"include_answer":true}'

# 跑测试
uv run pytest
```

## 响应结构（对齐 Tavily）

```json
{
  "query": "Tavily 是什么",
  "answer": "Tavily 是一个面向 AI agent 的搜索 API...",
  "results": [
    {
      "url": "https://...",
      "title": "...",
      "content": "摘要片段...",
      "score": 0.92,
      "raw_content": "完整正文（仅 include_raw_content=true 时）"
    }
  ]
}
```

## 配置项

见 `.env.example`。关键项：

| 变量 | 说明 |
|---|---|
| `SEARXNG_URL` | SearXNG 实例地址 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容 LLM 配置 |
| `MAX_RESULTS` | 默认返回结果数 |
| `FETCH_TOP_N` | 抓取正文的 URL 数 |

## 后续迭代路线

- [ ] 多源兜底（公共 SearXNG 实例池 + 商业 API）
- [ ] Redis 缓存实装
- [ ] 住宅代理防 CAPTCHA
- [ ] `/extract` `/crawl` `/map` 端点
- [ ] MCP server + Claude Skill 封装
- [ ] 商业化（鉴权 / 计费 / 限流）
