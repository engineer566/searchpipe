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

| 项目 | 值 |
|------|-----|
| **传输协议** | streamable-http（远程 MCP） |
| **MCP URL** | `{base_url}/mcp?api_key=sp-你的API密钥` |
| **工具名** | `ai_search_search` |

SearchPipe 是**远程 MCP Server**：API Key 直接内嵌在 MCP URL 的 `api_key` 参数里
（与 Tavily 的 `?tavilyApiKey=` 同款），任何支持 streamable-http 的客户端都能直接用，
不需要自定义请求头。

## API Key 与 MCP 链接获取步骤

1. 访问 `{base_url}/dashboard/register` 注册账号（注册即送免费额度）。
2. 登录后进入「API Keys」页面：`{base_url}/dashboard/api-keys`。
3. 点击「创建」生成 `sp-` 开头的 API Key——创建成功弹窗里会直接给出完整的
   **MCP 链接**（`{base_url}/mcp?api_key=sp-...`），复制即可。
4. 把该 MCP 链接配置到客户端（见下方配置示例）。

## MCP 客户端配置示例

### Claude Code

```bash
claude mcp add --transport http searchpipe "{base_url}/mcp?api_key=sp-你的API密钥"
```

或手工编辑 `.mcp.json`：

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "type": "http",
      "url": "{base_url}/mcp?api_key=sp-你的API密钥"
    }}
  }}
}}
```

### Cursor

在 Cursor Settings → MCP 中添加：

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "url": "{base_url}/mcp?api_key=sp-你的API密钥"
    }}
  }}
}}
```

### 其他支持 streamable-http 的 MCP 客户端

通用配置格式（URL 内嵌 Key）：

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "transport": "streamable-http",
      "url": "{base_url}/mcp?api_key=sp-你的API密钥"
    }}
  }}
}}
```

如果客户端支持自定义请求头，也可以用 Header 鉴权（与 URL 方式二选一）：

```json
{{
  "mcpServers": {{
    "searchpipe": {{
      "url": "{base_url}/mcp",
      "headers": {{
        "Authorization": "Bearer sp-你的API密钥"
      }}
    }}
  }}
}}
```

## 可用工具

| 工具 | 描述 |
|------|------|
| `ai_search_search` | 网络搜索（聚合多引擎 → 正文抓取 → LLM 重排 → 可选摘要） |

## 参数说明

### ai_search_search

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | 是 | - | 搜索查询内容 |
| `max_results` | 否 | 5 | 最大返回结果数量（1–20） |
| `include_answer` | 否 | false | 是否生成带引用的 AI 摘要 |
| `include_raw_content` | 否 | false | 是否返回抓取的完整正文 |
| `search_depth` | 否 | basic | 搜索深度：`basic`（扣 1 积分）/ `advanced`（扣 2 积分） |

## 输出格式

返回 JSON 格式结构化结果：

```json
{{
  "query": "搜索内容",
  "answer": "AI 摘要答案（含引用标注 [0][1]）",
  "ai_generated": true,
  "results": [
    {{
      "url": "https://example.com",
      "title": "页面标题",
      "content": "正文摘要片段",
      "score": 0.95,
      "raw_content": "完整正文（仅 include_raw_content=true 时）"
    }}
  ]
}}
```

## 配置验证

配置完成后，向 Agent 发送测试指令：

> 请使用 searchpipe 工具搜索 "FastAPI 部署最佳实践"。

Agent 应能成功调用 `ai_search_search` 并返回结构化搜索结果。

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| "API Key 无效或已吊销" | Key 不存在或已被删除 | 到控制台重新创建 API Key |
| "积分不足" | 免费额度用完 | 到 `{base_url}/dashboard/billing` 充值 |
| "检索源失败" | SearXNG 检索异常 | 已自动退款，可安全重试 |
| "输入/输出内容违规" | 触发内容审核 | 调整查询内容；输出违规已自动退款 |

---

*SearchPipe — 为 AI Agent 而生的联网搜索 · {base_url}*
"""


def render_skill_md() -> str:
    """渲染 SKILL.md，APP_BASE_URL 从配置读取。"""
    settings = get_settings()
    base_url = settings.app_base_url.rstrip("/")
    return SKILL_TEMPLATE.format(base_url=base_url)
