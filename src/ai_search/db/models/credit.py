"""积分账户与流水模型。

- CreditAccount：user_id 主键（1:1），balance 当前余额。扣费时 SELECT ... FOR UPDATE 行锁防超扣。
- CreditTransaction：不可变流水（每次余额变动一条），type 区分充值/消费/赠送/退款。
  balance_after 记录变动后快照，便于对账与审计。
"""

import enum
import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import TimestampMixin


class CreditTxType(str, enum.Enum):
    RECHARGE = "recharge"  # 充值到账
    CONSUME = "consume"    # 搜索消费
    GRANT = "grant"        # 赠送（免费额度/管理员发放）
    REFUND = "refund"      # 搜索失败退款


class CreditAccount(Base, TimestampMixin):
    __tablename__ = "credit_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    balance: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class CreditTransaction(Base, TimestampMixin):
    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)  # +/-
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ref_order_id: Mapped[int | None] = mapped_column(BigInteger)  # 关联 Order.id（充值/退款）
    remark: Mapped[str | None] = mapped_column(String(255))
