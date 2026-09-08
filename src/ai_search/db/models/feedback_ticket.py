"""反馈工单模型。

- FeedbackTicket：用户提交的工单，category 区分问题类型，status 控制生命周期。
- user_id 外键 users.id，级联删除（用户注销时一并清理工单）。
"""

import enum
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class TicketCategory(str, enum.Enum):
    BUG = "bug"
    FEATURE = "feature"
    BILLING = "billing"
    OTHER = "other"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class FeedbackTicket(Base, PkMixin, TimestampMixin):
    __tablename__ = "feedback_tickets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TicketCategory.OTHER.value
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TicketStatus.OPEN.value,
        server_default="open",
    )
