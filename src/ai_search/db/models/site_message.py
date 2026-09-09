"""站内信模型。

- SiteMessage：管理员发给用户的站内信，一行一个收件人（广播 = 为每个活跃用户写一行，
  MVP 用户量小，简单可靠）。
- batch_id：同一次发送动作共享一个批次 UUID（定向发送为 1 行，广播为 N 行），
  管理端按批次聚合已读/未读统计。
- user_id 外键 users.id 级联删除；sender_id 外键 users.id，管理员注销后置空保留消息。
- read_at 为 NULL 即未读，点开详情时置为当前时间。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class MessageKind:
    """站内信发送类型（应用层常量，字符串列存储，与 role/status 同风格）。"""

    USER = "user"  # 定向发送给指定用户
    BROADCAST = "broadcast"  # 全局广播（每活跃用户一行）


class SiteMessage(Base, PkMixin, TimestampMixin):
    __tablename__ = "site_messages"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MessageKind.USER, server_default="user"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
