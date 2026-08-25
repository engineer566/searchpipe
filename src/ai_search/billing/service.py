"""积分计费服务 —— 预付费 + 免费额度 + 行锁防超扣。

核心：
- deduct_credits：SELECT ... FOR UPDATE 行锁扣费，余额不足抛 InsufficientCreditsError。
- grant_credits：充值/赠送/退款入账（幂等靠调用方保证 ref 唯一性）。
- get_balance：读余额（无锁）。
- CreditTransaction 不可变流水，balance_after 快照便于对账。
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CreditAccount, CreditTransaction, CreditTxType

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """余额不足。由调用方转 HTTP 402。"""

    def __init__(self, balance: int, required: int) -> None:
        self.balance = balance
        self.required = required
        super().__init__(f"积分不足：余额 {balance}，需要 {required}")


async def _get_or_create_account(db: AsyncSession, user_id: uuid.UUID) -> CreditAccount:
    """取账户；不存在则建（注册流程应已建，此为兜底）。"""
    acc = await db.get(CreditAccount, user_id)
    if acc:
        return acc
    acc = CreditAccount(user_id=user_id, balance=0)
    db.add(acc)
    await db.flush()
    return acc


async def get_balance(db: AsyncSession, user_id: uuid.UUID) -> int:
    """读当前余额。账户不存在视为 0。"""
    acc = await db.get(CreditAccount, user_id)
    return acc.balance if acc else 0


async def deduct_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    *,
    remark: str | None = None,
) -> int:
    """扣费（行锁）。返回扣费后余额。余额不足抛 InsufficientCreditsError。

    必须在活动事务内调用（由 get_db 依赖管理 commit）。
    """
    if amount <= 0:
        raise ValueError("扣费金额必须为正")

    # with_for_update 行锁，防并发超扣
    acc = await db.get(
        CreditAccount,
        user_id,
        with_for_update=True,
    )
    if acc is None:
        # 账户不存在 → 建并锁定（极少见，注册时已建）
        acc = CreditAccount(user_id=user_id, balance=0)
        db.add(acc)
        await db.flush()
        acc = await db.get(CreditAccount, user_id, with_for_update=True)
        assert acc is not None  # noqa: S101

    if acc.balance < amount:
        raise InsufficientCreditsError(acc.balance, amount)

    acc.balance -= amount
    db.add(
        CreditTransaction(
            user_id=user_id,
            delta=-amount,
            type=CreditTxType.CONSUME.value,
            balance_after=acc.balance,
            remark=remark,
        )
    )
    await db.flush()
    logger.info("扣费 user=%s amount=%d balance_after=%d", user_id, amount, acc.balance)
    return acc.balance


async def grant_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    *,
    tx_type: str = CreditTxType.GRANT.value,
    ref_order_id: int | None = None,
    remark: str | None = None,
) -> int:
    """入账（充值/赠送/退款）。返回入账后余额。

    amount 为正数。退款也走此函数（delta=+amount）。
    幂等性由调用方保证：充值回调用 ref_order_id 去重，退款用 remark(req_id) 去重。
    """
    if amount <= 0:
        raise ValueError("入账金额必须为正")

    acc = await _get_or_create_account(db, user_id)
    # 入账也加行锁，保证 balance_after 快照一致
    acc = await db.get(CreditAccount, user_id, with_for_update=True)
    assert acc is not None  # noqa: S101

    acc.balance += amount
    db.add(
        CreditTransaction(
            user_id=user_id,
            delta=amount,
            type=tx_type,
            balance_after=acc.balance,
            ref_order_id=ref_order_id,
            remark=remark,
        )
    )
    await db.flush()
    logger.info(
        "入账 user=%s amount=%d type=%s balance_after=%d",
        user_id, amount, tx_type, acc.balance,
    )
    return acc.balance


async def refund_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    *,
    remark: str | None = None,
) -> int:
    """退款（搜索失败退还）。语义糖，内部走 grant_credits(type=refund)。"""
    return await grant_credits(
        db, user_id, amount, tx_type=CreditTxType.REFUND.value, remark=remark
    )


async def list_transactions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    size: int = 20,
) -> tuple[list[CreditTransaction], int]:
    """分页查询流水。返回 (items, total)。"""
    # total
    from sqlalchemy import func

    total = (
        await db.execute(
            select(func.count()).select_from(CreditTransaction).where(
                CreditTransaction.user_id == user_id
            )
        )
    ).scalar_one()

    stmt = (
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = (await db.execute(stmt)).scalars().all()
    return list(items), total
