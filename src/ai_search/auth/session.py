"""浏览器 session cookie —— Dashboard 登录态的签发与校验。

供两处使用：
- dashboard/routes.py：登录成功后写 cookie、页面守卫读 cookie。
- auth/dependencies.py：API 端点的 cookie 兜底（无 Bearer 头时），
  让控制台内 JS 可直接调 /search、/api-keys 等接口。

cookie 为 itsdangerous 签名的 user_id，httponly + samesite=lax。
"""

import logging
import uuid

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import get_settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ai_search_session"
SESSION_TTL = 7 * 24 * 3600  # 7 天


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        get_settings().session_cookie_secret, salt="dashboard-session"
    )


def set_session_cookie(resp: Response, user_id: str) -> None:
    """登录成功后调用：签发签名 cookie。"""
    token = _serializer().dumps(user_id)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,  # 海外 HTTP 内测；上线配 HTTPS 后改 True
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE)


def read_session_cookie(request: Request) -> uuid.UUID | None:
    """从请求 cookie 解出 user_id。无效/过期返回 None。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return uuid.UUID(_serializer().loads(token, max_age=SESSION_TTL))
    except (BadSignature, SignatureExpired, ValueError):
        return None
