"""鉴权包 —— 4 种登录 + JWT + API Key 依赖。

- 密码登录：email + password
- 手机验证码登录：phone + sms code（实名制主登录）
- GitHub OAuth
- 微信 OAuth（stub，待个体工商户后接入）
"""

from .core import API_KEY_PREFIX, AuthContext, resolve_api_key, resolve_jwt
from .dependencies import (
    get_current_admin,
    get_current_user,
    get_current_user_or_api_key,
    require_api_key,
)
from .errors import AuthError
from .jwt_handler import create_access_token, create_refresh_token, decode_token
from .password import hash_password, verify_password
from .routes import router
from .sms import send_code, verify_code

__all__ = [
    "API_KEY_PREFIX",
    "AuthContext",
    "AuthError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_admin",
    "get_current_user",
    "get_current_user_or_api_key",
    "hash_password",
    "require_api_key",
    "resolve_api_key",
    "resolve_jwt",
    "router",
    "send_code",
    "verify_code",
    "verify_password",
]
