"""鉴权域异常 —— 协议无关。

HTTP 依赖层捕获 AuthError 转 HTTPException(401)；
MCP tool 捕获转 fastmcp ToolError。
"""


class AuthError(Exception):
    """鉴权失败（凭据无效 / 已吊销 / 用户已停用）。

    携带 HTTP 状态码（默认 401）供 HTTP 依赖层直接转 HTTPException；
    MCP 层忽略状态码，统一转 tool error。
    """

    def __init__(self, message: str, status_code: int = 401) -> None:
        self.status_code = status_code
        super().__init__(message)
