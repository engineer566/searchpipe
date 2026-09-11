"""API Key 明文可查看 —— Fernet 对称加密（api_keys.key_cipher 列）。

需求背景（history/20260912.txt #4）：Key 要「平时隐藏、需要时点击显示」，不能
只在创建时一次性展示。argon2 hash 不可逆，因此在 key_hash 之外再存一份密文：

- 鉴权链路**完全不变**：仍按 key_prefix 粗筛 + argon2 verify(key_hash)，hash 是
  唯一鉴权凭据；key_cipher 只服务「查看明文 / 生成 MCP 链接」，即使密文泄露，
  没有主密钥也无法解出明文。
- 主密钥派生：settings.key_encryption_secret（留空回退 session_cookie_secret）
  经 sha256 → urlsafe base64 得到 Fernet key（32 字节）。
- 解密失败（主密钥轮换 / 密文损坏 / 旧数据）返回 None，由调用方降级为
  「该 Key 无法查看明文，请重建」，绝不抛错阻断业务。
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    """按当前配置构造 Fernet（不缓存：配置在测试中可能被 monkeypatch）。"""
    settings = get_settings()
    secret = settings.key_encryption_secret or settings.session_cookie_secret
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(raw: str) -> str:
    """加密明文 Key → 存 key_cipher。"""
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_key(cipher: str | None) -> str | None:
    """解密 key_cipher → 明文 Key；无密文或解密失败返回 None。"""
    if not cipher:
        return None
    try:
        return _fernet().decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as e:
        logger.warning("API Key 解密失败（主密钥轮换或密文损坏）：%s", e)
        return None
