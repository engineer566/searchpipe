"""API Key 管理包 —— sp- 前缀、hash-at-rest、吊销。"""

from .routes import router
from .service import create_key, list_keys, revoke_key

__all__ = ["create_key", "list_keys", "revoke_key", "router"]
