"""Agent Setup —— 一句话 MCP 配置 SKILL.md 生成。

端点 GET /agent-setup/SKILL.md 返回给 AI Agent 的配置指南，
指导 Agent 如何帮用户一键配置 SearchPipe MCP（streamable-http）。
内容用模板字符串渲染，APP_BASE_URL 从 config 读取。
"""

from .config import get_settings


SKILL_TEMPLATE = """\
---
name: searchpipe-search
description: Web search, online search, real-time search, internet search, search the web, lookup online, find information, research, 查询, 搜索, 联网搜索, 实时搜索, 网络查询, 资料查找, 信息检索. SearchPipe real-time web search API built for AI agents via MCP.
---

# SearchPipe Search Skill (MCP-based)

This skill utilizes the SearchPipe MCP server, providing clean, real-time web search results purpose-built for AI agents.

## MCP Endpoint

| Item | Value |
|------|-------|
| **Transport** | streamable-http (remote MCP) |
| **MCP URL** | `{base_url}/mcp?api_key=sp-YOUR_API_KEY` |
| **Tool name** | `ai_search_search` |

SearchPipe is a **remote MCP server**: the API key is embedded directly in the MCP URL via
the `api_key` parameter (same pattern as Tavily's `?tavilyApiKey=`). Any client that
supports streamable-http can use it out of the box — no custom request headers required.

## How to Obtain an API Key and MCP Link

**Check the user's prompt first**: if it already contains an **MCP link** (in the form
`{base_url}/mcp?api_key=sp-...`) or an **API key** (starting with `sp-`), use it to
complete the configuration directly. Do not ask the user for credentials again, and do
not ask the user to operate the dashboard manually. Only walk through the steps below
when neither a key nor a link is present in the prompt:

1. Visit `{base_url}/dashboard/register` to create an account (free credits included
   upon registration). Once email verification is complete, the system will
   **automatically generate a default API key** — no manual creation needed.
2. After logging in, open the "API Keys" page: `{base_url}/dashboard/api-keys` — keys
   are masked by default; click "Show" to reveal the plaintext. The "MCP Configuration"
   card on that page lets you switch keys and copy the MCP link or the
   **one-line setup prompt** (includes the key, ready to paste to your agent).
3. Configure the MCP link in your client (see the examples below).

## MCP Client Configuration Examples

### Claude Code

```bash
claude mcp add --transport http searchpipe "{base_url}/mcp?api_key=sp-YOUR_API_KEY"
```

Or edit `.mcp.json` manually:

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "type": "http",
      "url": "{base_url}/mcp?api_key=sp-YOUR_API_KEY"
    }}
  }}
}}
```

### Cursor

Add the following in Cursor Settings → MCP:

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "url": "{base_url}/mcp?api_key=sp-YOUR_API_KEY"
    }}
  }}
}}
```

### Other streamable-http MCP Clients

Generic configuration format (key embedded in URL):

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "transport": "streamable-http",
      "url": "{base_url}/mcp?api_key=sp-YOUR_API_KEY"
    }}
  }}
}}
```

If the client supports custom request headers, you can also use header-based
authentication (choose one of the two approaches):

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "url": "{base_url}/mcp",
      "headers": {{
        "Authorization": "Bearer sp-YOUR_API_KEY"
      }}
    }}
  }}
}}
```

## Available Tools

| Tool | Description |
|------|-------------|
| `ai_search_search` | Web search (aggregates multiple engines → fetches page content → LLM reranking → optional AI answer) |

## Parameters

### ai_search_search

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `query` | Yes | - | The search query |
| `max_results` | No | 5 | Maximum number of results to return (1–20) |
| `include_answer` | No | false | Whether to generate an AI answer with citations |
| `include_raw_content` | No | false | Whether to return the full fetched page content |
| `search_depth` | No | basic | Search depth: `basic` (costs 1 credit) / `advanced` (costs 2 credits) |

## Output Format

Returns structured results in JSON:

```json
{{
  "query": "your search query",
  "answer": "AI-generated answer with citations [0][1]",
  "ai_generated": true,
  "results": [
    {{
      "url": "https://example.com",
      "title": "Page title",
      "content": "Content snippet",
      "score": 0.95,
      "raw_content": "Full page content (only when include_raw_content=true)"
    }}
  ]
}}
```

## Verification

After configuration, send a test instruction to the agent:

> Please use the searchpipe tool to search for "FastAPI deployment best practices".

The agent should successfully call `ai_search_search` and return structured results.

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| "Invalid or revoked API key" | Key does not exist or has been revoked | Create a new API key in the dashboard |
| "Insufficient credits" | Free credits exhausted | Top up at `{base_url}/dashboard/billing` |
| "Search backend failure" | SearXNG search backend error | Credits are automatically refunded; safe to retry |
| "Input/Output content violation" | Content moderation triggered | Rephrase the query; output violations are automatically refunded |

---

*SearchPipe — real-time web search built for AI agents · {base_url}*
"""


def render_skill_md() -> str:
    """渲染 SKILL.md，APP_BASE_URL 从配置读取。"""
    settings = get_settings()
    base_url = settings.app_base_url.rstrip("/")
    return SKILL_TEMPLATE.format(base_url=base_url)


def mcp_url(base_url: str, api_key: str) -> str:
    """按站点基址 + API Key 拼 MCP 链接（Key 内嵌 URL，Tavily 式）。"""
    return f"{base_url.rstrip('/')}/mcp?api_key={api_key}"


def build_agent_prompt(base_url: str, api_key: str) -> str:
    """生成「一句话配置」提示词：直接粘给 AI Agent 即可自动完成 MCP 配置。

    需求（history/20260912.txt #5）：这句话默认带上用户的 API Key 与 MCP 链接，
    Agent 读到即可一键配置，无需再来回索要凭据。
    控制台只提供复制按钮、页面不展示内容（见 api_keys.html / dashboard.html），
    故由服务端（/api-keys/reveal）按登录用户渲染，而不是抄在模板里。
    """
    base = base_url.rstrip("/")
    url = mcp_url(base, api_key)
    return (
        f"Please read {base}/agent-setup/SKILL.md and follow the instructions there to "
        f"configure the SearchPipe MCP server for me — you do not need to ask me for any "
        f"further information: the MCP URL is {url}, my API key is {api_key} "
        f"(tool name: ai_search_search). After configuration, verify it by running a "
        f"search for \"FastAPI deployment best practices\"."
    )
