# SearchPipe

Self-hosted AI search API (Tavily-style) — built on self-hosted SearXNG + LLM reranking, designed for LLM/RAG agents.

One endpoint, `POST /search`, runs the full pipeline: multi-engine retrieval → trafilatura content extraction → LLM rerank + answer. On top of that sits a complete commercial backend: user accounts, credit-based billing (batched, 2-decimal), API keys, MoR payments (Creem / Dodo Payments — card & PayPal, hosted checkout + native subscription auto-renewal), content moderation, rate limiting, and usage logs. Plus a Jinja2 server-rendered dashboard (`/dashboard`) and an MCP server entry (`/mcp`).

## Architecture

```
POST /search
  → retrieval orchestration (SearXNG multi-engine aggregation)
  → content extraction (trafilatura)
  → LLM rerank + answer (DeepSeek or any OpenAI-compatible model)
  → Tavily-style structured results

Auth: JWT / sp- API Key / session cookie → unified AuthContext
Billing: batched credit lots (grant/deduct/refund/expiry sweep)
Payments: Creem or Dodo (MoR) — hosted checkout, signed webhooks,
          normalized PaymentEvent, customer portal
```

## Quickstart

### 1. Start SearXNG + Redis + Postgres

```bash
docker compose up -d
# verify SearXNG JSON API
curl 'http://localhost:8080/search?q=test&format=json' | head -c 200
```

### 2. Configure environment

```bash
cp .env.example .env
# edit .env: LLM_API_KEY (DeepSeek etc.), DATABASE_URL, JWT_SECRET,
# and either Creem or Dodo credentials (PAYMENT_PROVIDER=creem|dodo)
```

### 3. Install and run

```bash
uv sync                # or: pip install -e ".[dev]"
uv run uvicorn ai_search.main:app --reload --port 8000
```

### 4. Try it

```bash
# health
curl http://localhost:8000/healthz

# search (register an account first to get an API key)
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sp-your-api-key" \
  -d '{"query":"what is Tavily","max_results":5,"include_answer":true}'

# run tests (real Postgres + Redis required)
.venv/bin/python -m pytest tests/ -q
```

## Response shape (Tavily-compatible)

```json
{
  "query": "what is Tavily",
  "answer": "Tavily is a search API for AI agents...",
  "results": [
    {
      "url": "https://...",
      "title": "...",
      "content": "snippet...",
      "score": 0.92,
      "raw_content": "full text (only when include_raw_content=true)"
    }
  ]
}
```

## MCP access

Any MCP client (Claude Desktop, Cursor, etc.) can connect to `{APP_BASE_URL}/mcp?api_key=sp-...`. The dashboard generates a one-line setup prompt and a copyable MCP link per API key; `GET /agent-setup/SKILL.md` returns a ready-to-paste skill file for agents.

## Configuration

See `.env.example` for the full list. Key groups:

| Variable | Description |
|---|---|
| `SEARXNG_URL` | SearXNG instance address |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI-compatible LLM for rerank/answer |
| `PAYMENT_PROVIDER` | `creem` or `dodo` (MoR; USD settlement) |
| `CREEM_*` / `DODO_*` | API key, webhook secret, product ids per provider |
| `CURRENCY` / `CREDIT_PRICE_RATE` / `MAX_RECHARGE_USD` | billing rules (default USD, $0.005 = 1 credit) |
| `OAUTH_GITHUB_*` / `OAUTH_GOOGLE_*` | optional social login |
| `APP_BASE_URL` | public base URL (links, canonical, sitemap) |

Plan ↔ provider product mapping lives in the DB (`plans.provider_products`); create products in the Creem/Dodo dashboard after deploying and fill in the ids there.

## Documentation

- `docs/INDEX.md` — code index and module map (start here for development)
- `docs/REGRESSION_CHECKLIST.md` — full regression checklist before each release
- `docs/memory/` — project background and operational notes

---

*中文说明：SearchPipe 是一个面向海外用户的自建 AI 搜索 API 项目（出海版，USD 结算，Creem/Dodo 支付）；国内版已封存于 `archive/china-2026-09` tag 与 `china-archive` 分支。*
