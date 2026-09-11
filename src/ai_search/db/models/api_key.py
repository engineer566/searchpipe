"""API Key 模型。

- key_prefix：明文前 8 位，用于列表展示与按前缀粗筛候选（hash 非可逆，无法反查）。
- key_hash：完整 key 的 argon2 hash，**唯一鉴权凭据**，静态存储，泄露不暴露明文。
- key_cipher：完整 key 的 Fernet 密文（见 api_keys/crypto.py），仅供「查看明文 /
  生成 MCP 链接」，不可用于鉴权（2026-09-12 需求 #4：Key 平时隐藏、点击可查看）。
- is_default：默认 Key 标记。邮箱验证通过即自动生成一把默认 Key，控制台 MCP 配置
  与一句话配置默认使用它。
- revoked_at：软吊销（非物理删除），保留审计痕迹。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class ApiKey(Base, PkMixin, TimestampMixin):
    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    key_cipher: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False, index=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
