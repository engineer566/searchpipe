"""JWT 签发与校验 —— python-jose HS256。

access token 短期（30min），refresh token 长期（30d）。
payload 带 sub(user_id str)、type、exp。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from ..config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, _settings.jwt_secret, algorithm=_settings.jwt_algo)


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_settings.access_token_ttl_min)
    return _encode({"sub": user_id, "type": "access", "exp": expire})


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_settings.refresh_token_ttl_days)
    return _encode({"sub": user_id, "type": "refresh", "exp": expire})


def decode_token(token: str) -> dict[str, Any] | None:
    """解 token；过期/非法返回 None（不抛，由调用方决定 401）。"""
    try:
        return jwt.decode(token, _settings.jwt_secret, algorithms=[_settings.jwt_algo])
    except JWTError as e:
        logger.debug("JWT 解码失败: %s", e)
        return None
