"""积分账户、流水与批次模型。

- CreditAccount：user_id 主键（1:1），balance 当前余额（NUMERIC(20,2)，支持 2 位
  小数积分——自定义充值按 ¥0.03=1 积分换算产生小数）。扣费时 SELECT ... FOR UPDATE 行锁防超扣。
- CreditTransaction：不可变流水（每次余额变动一条），type 区分充值/消费/赠送/退款/过期。
  balance_after 记录变动后快照，便于对账与审计。consume 流水的 lot_usage (JSONB)
  记录本次消耗的批次明细 [{lot_id, amount}]，退款按此还原到原批次。
- CreditLot：积分批次。每笔入账生成一批：充值/赠送 → expires_at=NULL（永久）；
  订阅/续订/升级 → 30 天有效期。effective_at 控制生效时刻（续订批次=下一周期起点）。
  扣费按 expires_at 升序（NULL 最后）消耗，限时积分优先用掉。
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mixins import PkMixin, TimestampMixin


class CreditTxType(str, enum.Enum):
    RECHARGE = "recharge"  # 充值/订阅到账
    CONSUME = "consume"    # 搜索消费
    GRANT = "grant"        # 赠送（免费额度/管理员发放）
    REFUND = "refund"      # 搜索失败退款
    EXPIRE = "expire"      # 订阅积分到期清零


class CreditAccount(Base, TimestampMixin):
    __tablename__ = "credit_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False, default=0, server_default="0"
    )


class CreditTransaction(Base, TimestampMixin):
    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    delta: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)  # +/-
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    ref_order_id: Mapped[int | None] = mapped_column(BigInteger)  # 关联 Order.id（充值/退款）
    lot_usage: Mapped[list | None] = mapped_column(JSONB)  # consume 批次消耗明细
    remark: Mapped[str | None] = mapped_column(String(255))


class CreditLot(Base, PkMixin, TimestampMixin):
    __tablename__ = "credit_lots"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column()  # 关联 Order.id（逻辑外键，弱约束）
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # recharge/subscribe/renew/upgrade/grant/refund
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)   # 初始额度
    remaining: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)  # 剩余额度
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # NULL=永久
