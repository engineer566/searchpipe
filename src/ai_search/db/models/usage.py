"""用量日志模型 —— 每次搜索一条，6 个月留存（网安法第21条）。

created_at + user_id 复合索引支撑「按时间范围 + 用户」聚合查询（用量曲线、明细）。
status 区分成功/失败，error_msg 记失败原因（检索源 502、审核拦截等）。
"""

import uuid

from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class UsageLog(Base, PkMixin, TimestampMixin):
    __tablename__ = "usage_logs"
    __table_args__ = (
        Index("ix_usage_logs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    search_depth: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")
    credits_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  # ok/error
    error_msg: Mapped[str | None] = mapped_column(Text)
