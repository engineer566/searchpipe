"""请求/响应模型 —— 对齐 Tavily 的 /search 结构。"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., description="The search query")
    max_results: int = Field(5, ge=1, le=20, description="Maximum number of results to return")
    search_depth: str = Field("basic", description="Search depth: basic (1 credit) / advanced (2 credits)")
    include_answer: bool = Field(False, description="Whether to generate an LLM answer with citations")
    include_raw_content: bool = Field(
        False, description="Whether to return full fetched page content"
    )


class SearchResult(BaseModel):
    url: str
    title: str
    content: str = Field("", description="Content snippet")
    score: float = Field(0.0, description="LLM relevance score, 0-1")
    raw_content: str | None = Field(None, description="Full page content (optional)")


class SearchResponse(BaseModel):
    query: str
    answer: str | None = Field(None, description="LLM-generated answer (optional)")
    results: list[SearchResult]
    ai_generated: bool = Field(
        False, description="Whether the response contains LLM-generated content"
    )
