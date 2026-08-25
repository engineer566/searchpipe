"""请求/响应模型 —— 对齐 Tavily 的 /search 结构。"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., description="搜索查询")
    max_results: int = Field(5, ge=1, le=20, description="返回结果数")
    search_depth: str = Field("basic", description="basic / advanced（MVP 暂不区分）")
    include_answer: bool = Field(False, description="是否生成 LLM 摘要")
    include_raw_content: bool = Field(
        False, description="是否返回完整正文（消耗更多 token）"
    )


class SearchResult(BaseModel):
    url: str
    title: str
    content: str = Field("", description="摘要片段")
    score: float = Field(0.0, description="LLM 相关性评分 0-1")
    raw_content: str | None = Field(None, description="完整正文（可选）")


class SearchResponse(BaseModel):
    query: str
    answer: str | None = Field(None, description="LLM 生成的摘要（可选）")
    results: list[SearchResult]
    ai_generated: bool = Field(
        False, description="是否含 LLM 生成内容（深度合成规定第16-17条 AI 标识）"
    )
