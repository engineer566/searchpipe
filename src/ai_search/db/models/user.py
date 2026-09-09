"""用户与 OAuth 账号模型。

- User：核心用户实体，支持手机号 / 邮箱 + 密码 / OAuth 多种登录方式（phone、email 均可空，
  只要至少一种身份标识；密码登录才需要 password_hash）。
- OAuthAccount：第三方账号绑定，UNIQUE(provider, provider_uid) 防重复绑定。

role / status 用 String 列存枚举值（"owner"/"admin"/"member"/"user"、"active"/"suspended"），
避免 SQLAlchemy Enum 类型在 async 场景的重建开销与迁移摩擦，约束由应用层 + DB CHECK 保证。
user_id 在 OAuth/ApiKey 等关联表用 String(36) 存 UUID 文本，跨库可移植、便于排查。
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    USER = "user"

    @classmethod
    def is_admin(cls, value: str) -> bool:
        return value in (cls.OWNER.value, cls.ADMIN.value)


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "users"

    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserRole.USER.value, server_default="user"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=UserStatus.ACTIVE.value,
        server_default="active",
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthProvider(str, enum.Enum):
    GITHUB = "github"
    WECHAT = "wechat"


class OAuthAccount(Base, PkMixin, TimestampMixin):
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_uid", name="uq_provider_uid"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_uid: Mapped[str] = mapped_column(String(255), nullable=False)
