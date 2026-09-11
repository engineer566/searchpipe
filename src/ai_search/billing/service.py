"""积分计费服务 —— 预付费 + 免费额度 + 行锁防超扣 + 批次有效期。

核心：
- deduct_credits：先惰性 sweep 过期批次 → SELECT ... FOR UPDATE 行锁 → 按
  expires_at 升序（NULL 永久批次最后）消耗批次，限时积分优先用掉；余额不足抛
  InsufficientCreditsError。consume 流水 lot_usage (JSONB) 记录批次消耗明细。
- grant_credits：充值/订阅/赠送/退款入账，同时新建积分批次（永久或带有效期）。
- refund_credits：按原 consume 流水的 lot_usage 把积分还原到原批次（批次仍有效
  且未超原始额度），还原不了的部分进永久批次——防限时积分经退款洗成永久。
- sweep_expired：过期批次 remaining→0、写 expire 流水、余额同步扣减。
  get_balance/deduct 前惰性执行；另有 lifespan 后台定时 sweep 兜底。
- 不变量：批次与余额的一切变动都发生在该用户账户行锁内。
- 金额一律 NUMERIC(20,2)，量化 0.01（ROUND_HALF_UP）。
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CreditAccount, CreditLot, CreditTransaction, CreditTxType

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _q(amount: Decimal | int | float | str) -> Decimal:
    """量化到 0.01（ROUND_HALF_UP）。"""
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


class InsufficientCreditsError(Exception):
    """余额不足。由调用方转 HTTP 402。"""

    def __init__(self, balance: Decimal, required: Decimal) -> None:
        self.balance = balance
        self.required = required
        super().__init__(f"Insufficient credits: balance {balance}, required {required}")


async def _lock_account(db: AsyncSession, user_id: uuid.UUID) -> CreditAccount:
    """取账户并加行锁；不存在则建（注册流程应已建，此为兜底）。

    批次与余额的一切变动都必须在此锁内进行。
    """
    acc = await db.get(CreditAccount, user_id, with_for_update=True)
    if acc is None:
        acc = CreditAccount(user_id=user_id, balance=ZERO)
        db.add(acc)
        await db.flush()
        acc = await db.get(CreditAccount, user_id, with_for_update=True)
        assert acc is not None  # noqa: S101
    return acc


async def _usable_lots(
    db: AsyncSession, user_id: uuid.UUID, now: datetime
) -> list[CreditLot]:
    """当前可用批次（已生效、未过期、有剩余），按到期时间升序（永久最后）。"""
    stmt = (
        select(CreditLot)
        .where(
            CreditLot.user_id == user_id,
            CreditLot.remaining > 0,
            CreditLot.effective_at <= now,
            (CreditLot.expires_at.is_(None)) | (CreditLot.expires_at > now),
        )
        .order_by(CreditLot.expires_at.asc().nulls_last(), CreditLot.effective_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def sweep_expired(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> Decimal:
    """清理过期批次：remaining 归零、写 expire 流水、余额同步扣减。返回清零总量。"""
    now = now or datetime.now(timezone.utc)
    stmt = select(CreditLot).where(
        CreditLot.user_id == user_id,
        CreditLot.remaining > 0,
        CreditLot.expires_at.is_not(None),
        CreditLot.expires_at <= now,
    )
    lots = list((await db.execute(stmt)).scalars().all())
    if not lots:
        return ZERO

    acc = await _lock_account(db, user_id)
    total = ZERO
    for lot in lots:
        total += lot.remaining
        lot.remaining = ZERO
    acc.balance -= total
    db.add(
        CreditTransaction(
            user_id=user_id,
            delta=-total,
            type=CreditTxType.EXPIRE.value,
            balance_after=acc.balance,
            remark=f"Subscription credits expired: {total} cleared",
        )
    )
    await db.flush()
    logger.info("过期清零 user=%s expired=%s balance_after=%s", user_id, total, acc.balance)
    return total


async def sweep_all_expired(db: AsyncSession) -> int:
    """后台任务：清理全库过期批次。返回清理批次数。"""
    now = datetime.now(timezone.utc)
    stmt = (
        select(CreditLot.user_id)
        .where(
            CreditLot.remaining > 0,
            CreditLot.expires_at.is_not(None),
            CreditLot.expires_at <= now,
        )
        .distinct()
    )
    user_ids = list((await db.execute(stmt)).scalars().all())
    for uid in user_ids:
        await sweep_expired(db, uid, now=now)
    return len(user_ids)


async def get_balance(db: AsyncSession, user_id: uuid.UUID) -> Decimal:
    """读当前余额（惰性 sweep 过期批次）。账户不存在视为 0。"""
    await sweep_expired(db, user_id)
    acc = await db.get(CreditAccount, user_id)
    return acc.balance if acc else ZERO


async def get_balance_detail(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """余额 + 明细：永久余额 / 限时余额 / 未生效余额（续订排队）/ 最近到期时间。"""
    balance = await get_balance(db, user_id)
    now = datetime.now(timezone.utc)
    lots = await _usable_lots(db, user_id, now)
    permanent = sum((lot.remaining for lot in lots if lot.expires_at is None), ZERO)
    expiring = sum((lot.remaining for lot in lots if lot.expires_at is not None), ZERO)
    next_expiry = min(
        (lot.expires_at for lot in lots if lot.expires_at is not None and lot.remaining > 0),
        default=None,
    )
    # 已入账但未生效的批次（续订排队积分）
    stmt = select(CreditLot).where(
        CreditLot.user_id == user_id,
        CreditLot.remaining > 0,
        CreditLot.effective_at > now,
    )
    upcoming = sum(
        (lot.remaining for lot in (await db.execute(stmt)).scalars().all()), ZERO
    )
    return {
        "balance": balance,
        "permanent": permanent,
        "expiring": expiring,
        "upcoming": upcoming,
        "next_expiry": next_expiry,
    }


async def deduct_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal | int,
    *,
    remark: str | None = None,
) -> Decimal:
    """扣费（行锁 + 批次消耗）。返回扣费后余额。余额不足抛 InsufficientCreditsError。

    消耗顺序：到期时间升序（限时先用），永久批次最后。
    必须在活动事务内调用（由 get_db 依赖管理 commit）。
    """
    amount = _q(amount)
    if amount <= 0:
        raise ValueError("Deduction amount must be positive")

    now = datetime.now(timezone.utc)
    await sweep_expired(db, user_id, now=now)
    acc = await _lock_account(db, user_id)

    # 可用余额 = 已生效且未过期批次之和（续订批次未生效前不可消费）
    lots = await _usable_lots(db, user_id, now)
    usable = sum((lot.remaining for lot in lots), ZERO)
    if usable < amount:
        raise InsufficientCreditsError(usable, amount)

    # 按批次消耗并记录明细
    remaining_to_deduct = amount
    lot_usage: list[dict] = []
    for lot in lots:
        if remaining_to_deduct <= 0:
            break
        take = min(lot.remaining, remaining_to_deduct)
        lot.remaining -= take
        remaining_to_deduct -= take
        lot_usage.append({"lot_id": str(lot.id), "amount": str(take)})

    acc.balance -= amount
    db.add(
        CreditTransaction(
            user_id=user_id,
            delta=-amount,
            type=CreditTxType.CONSUME.value,
            balance_after=acc.balance,
            lot_usage=lot_usage,
            remark=remark,
        )
    )
    await db.flush()
    logger.info("扣费 user=%s amount=%s balance_after=%s", user_id, amount, acc.balance)
    return acc.balance


async def grant_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal | int,
    *,
    tx_type: str = CreditTxType.GRANT.value,
    ref_order_id: int | None = None,
    remark: str | None = None,
    lot_source: str | None = None,
    lot_order_id: uuid.UUID | None = None,
    lot_effective_at: datetime | None = None,
    lot_expires_at: datetime | None = None,
    create_lot: bool = True,
) -> Decimal:
    """入账（充值/订阅/赠送/退款）。返回入账后余额。

    amount 为正数。默认新建永久批次；订阅类由调用方传 lot_effective_at /
    lot_expires_at 指定生效与到期时刻。幂等性由调用方保证（回调用订单 status 去重）。
    """
    amount = _q(amount)
    if amount <= 0:
        raise ValueError("Grant amount must be positive")

    acc = await _lock_account(db, user_id)
    acc.balance += amount

    if create_lot:
        db.add(
            CreditLot(
                user_id=user_id,
                order_id=lot_order_id,
                source=lot_source or tx_type,
                amount=amount,
                remaining=amount,
                effective_at=lot_effective_at or datetime.now(timezone.utc),
                expires_at=lot_expires_at,
            )
        )

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
        "入账 user=%s amount=%s type=%s balance_after=%s",
        user_id, amount, tx_type, acc.balance,
    )
    return acc.balance


async def refund_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal | int,
    *,
    remark: str | None = None,
) -> Decimal:
    """退款（搜索失败退还）。按原 consume 流水的 lot_usage 还原到原批次。

    原批次仍有效且未超原始额度的部分直接还原；批次已过期或已满的溢出部分
    进永久批次（避免退款丢失）。remark 形如 "refund:<req_ref>"，原扣费 remark=<req_ref>。
    """
    amount = _q(amount)
    if amount <= 0:
        raise ValueError("Refund amount must be positive")

    now = datetime.now(timezone.utc)

    # 找原 consume 流水的批次消耗明细
    orig_remark = remark.removeprefix("refund:") if remark else None
    lot_usage: list[dict] = []
    if orig_remark:
        stmt = (
            select(CreditTransaction)
            .where(
                CreditTransaction.user_id == user_id,
                CreditTransaction.type == CreditTxType.CONSUME.value,
                CreditTransaction.remark == orig_remark,
            )
            .order_by(CreditTransaction.id.desc())
            .limit(1)
        )
        orig_tx = (await db.execute(stmt)).scalar_one_or_none()
        if orig_tx and orig_tx.lot_usage:
            lot_usage = orig_tx.lot_usage

    # 还原到原批次；批次已过期或已满的溢出部分进永久批次
    overflow = amount
    for usage in lot_usage:
        if overflow <= 0:
            break
        lot = await db.get(CreditLot, uuid.UUID(usage["lot_id"]))
        if not lot:
            continue
        # 批次已过期则不再还原（避免退款刚入账又被清零），转永久
        if lot.expires_at is not None and lot.expires_at <= now:
            continue
        restore = min(Decimal(usage["amount"]), overflow)
        headroom = lot.amount - lot.remaining
        restore = min(restore, max(headroom, ZERO))
        if restore > 0:
            lot.remaining += restore
            overflow -= restore

    return await _grant_refund(db, user_id, amount, overflow, remark)


async def _grant_refund(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal,
    overflow: Decimal,
    remark: str | None,
) -> Decimal:
    """退款入账：余额 +amount，溢出部分建永久批次（还原部分批次已在 refund_credits 内恢复）。"""
    acc = await _lock_account(db, user_id)
    acc.balance += amount
    if overflow > 0:
        db.add(
            CreditLot(
                user_id=user_id,
                order_id=None,
                source="refund",
                amount=overflow,
                remaining=overflow,
                effective_at=datetime.now(timezone.utc),
                expires_at=None,
            )
        )
    db.add(
        CreditTransaction(
            user_id=user_id,
            delta=amount,
            type=CreditTxType.REFUND.value,
            balance_after=acc.balance,
            remark=remark,
        )
    )
    await db.flush()
    logger.info("退款 user=%s amount=%s balance_after=%s", user_id, amount, acc.balance)
    return acc.balance


async def list_transactions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    size: int = 20,
) -> tuple[list[CreditTransaction], int]:
    """分页查询流水。返回 (items, total)。"""
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
