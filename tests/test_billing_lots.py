"""积分批次服务级测试 —— 扣费顺序 / 过期清理 / 退款还原 / 未生效批次不可消费。

跑在 pytest session loop 上（与 test_mcp_server.py 同组，先于 TestClient 文件）。
原因：直接调 billing 服务函数需要 async_session_factory 绑定当前 loop；
setup 重建 engine 绑定 session loop，teardown 只 dispose 不重建（还给后续
TestClient 文件的 portal loop）。顺序在 conftest.py 的执行顺序表登记。
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

_TEST_PASSWORD = "test-pass-1234"


@pytest.fixture(scope="module", autouse=True)
async def _bind_session_loop():
    """重建 engine/Redis 单例绑定 session loop；teardown 只 dispose 不重建。"""
    from ai_search.db import base as db_base
    from ai_search.utils import cache as cache_mod

    db_base.engine = db_base._build_engine()
    db_base.async_session_factory.configure(bind=db_base.engine)
    cache_mod._cache = None
    yield
    await db_base.engine.dispose()
    if cache_mod._cache is not None:
        await cache_mod._cache.close()
        cache_mod._cache = None


async def _make_user() -> uuid.UUID:
    """直接建用户（本文件全程 session loop，无跨 loop 风险）。"""
    from ai_search.db.base import async_session_factory
    from ai_search.db.models import User

    async with async_session_factory() as db:
        user = User(email=f"lots-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        await db.commit()
        return user.id


async def _lots(user_id: uuid.UUID):
    from ai_search.db.base import async_session_factory
    from ai_search.db.models import CreditLot

    async with async_session_factory() as db:
        stmt = select(CreditLot).where(CreditLot.user_id == user_id).order_by(CreditLot.created_at)
        return list((await db.execute(stmt)).scalars().all())


async def test_deduct_prefers_expiring_lots():
    """扣费优先消耗限时（即将到期）批次，永久批次最后。"""
    from ai_search.billing.service import deduct_credits, get_balance_detail, grant_credits
    from ai_search.db.base import async_session_factory

    uid = await _make_user()
    future = datetime.now(timezone.utc) + timedelta(days=10)
    async with async_session_factory() as db:
        await grant_credits(db, uid, 100, tx_type="grant")  # 永久
        await grant_credits(
            db, uid, 50, tx_type="recharge", lot_source="subscribe", lot_expires_at=future
        )  # 限时
        balance = await deduct_credits(db, uid, 30, remark="search:t1")
        await db.commit()
    assert balance == Decimal("120.00")

    lots = await _lots(uid)
    permanent, expiring = lots[0], lots[1]
    assert permanent.remaining == Decimal("100.00")  # 永久未动
    assert expiring.remaining == Decimal("20.00")    # 限时先扣

    # 再扣 40：限时 20 耗尽后继续扣永久
    async with async_session_factory() as db:
        balance = await deduct_credits(db, uid, 40, remark="search:t2")
        detail = await get_balance_detail(db, uid)
        await db.commit()
    assert balance == Decimal("80.00")
    assert detail["expiring"] == Decimal("0.00")
    assert detail["permanent"] == Decimal("80.00")


async def test_sweep_expired_lots():
    """过期批次清零：余额扣减 + expire 流水。"""
    from ai_search.billing.service import get_balance, grant_credits, sweep_expired
    from ai_search.db.base import async_session_factory
    from ai_search.db.models import CreditTransaction

    uid = await _make_user()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    async with async_session_factory() as db:
        await grant_credits(db, uid, 100, tx_type="grant")
        await grant_credits(
            db, uid, 25, tx_type="recharge", lot_source="subscribe", lot_expires_at=past
        )
        await db.commit()
    # get_balance 惰性 sweep
    async with async_session_factory() as db:
        balance = await get_balance(db, uid)
        await db.commit()
    assert balance == Decimal("100.00")

    lots = await _lots(uid)
    assert lots[1].remaining == Decimal("0.00")

    async with async_session_factory() as db:
        stmt = select(CreditTransaction).where(
            CreditTransaction.user_id == uid, CreditTransaction.type == "expire"
        )
        tx = (await db.execute(stmt)).scalar_one_or_none()
    assert tx is not None and tx.delta == Decimal("-25.00")

    # 重复 sweep 幂等（批次已清零）
    async with async_session_factory() as db:
        swept = await sweep_expired(db, uid)
        await db.commit()
    assert swept == Decimal("0.00")


async def test_refund_restores_original_lots():
    """退款按 consume 流水的 lot_usage 还原到原批次（限时批次不被洗成永久）。"""
    from ai_search.billing.service import deduct_credits, grant_credits, refund_credits
    from ai_search.db.base import async_session_factory

    uid = await _make_user()
    future = datetime.now(timezone.utc) + timedelta(days=10)
    async with async_session_factory() as db:
        await grant_credits(
            db, uid, 50, tx_type="recharge", lot_source="subscribe", lot_expires_at=future
        )
        await deduct_credits(db, uid, 30, remark="search:r1")
        balance = await refund_credits(db, uid, 30, remark="refund:search:r1")
        await db.commit()
    assert balance == Decimal("50.00")

    lots = await _lots(uid)
    assert len(lots) == 1  # 还原到原批次，未新建永久批次
    assert lots[0].remaining == Decimal("50.00")


async def test_future_lot_not_usable():
    """续订排队批次（effective_at 在未来）未生效前不可消费。"""
    from ai_search.billing.service import (
        InsufficientCreditsError,
        deduct_credits,
        get_balance_detail,
        grant_credits,
    )
    from ai_search.db.base import async_session_factory

    uid = await _make_user()
    future = datetime.now(timezone.utc) + timedelta(days=30)
    async with async_session_factory() as db:
        await grant_credits(db, uid, 10, tx_type="grant")  # 永久可用 10
        await grant_credits(
            db, uid, 1000, tx_type="recharge", lot_source="renew",
            lot_effective_at=future, lot_expires_at=future + timedelta(days=30),
        )
        detail = await get_balance_detail(db, uid)
        assert detail["balance"] == Decimal("1010.00")
        assert detail["upcoming"] == Decimal("1000.00")
        # 只能扣到可用的 10，排队积分不可用
        with pytest.raises(InsufficientCreditsError):
            await deduct_credits(db, uid, 11, remark="search:f1")
        balance = await deduct_credits(db, uid, 10, remark="search:f2")
        assert balance == Decimal("1000.00")
        await db.commit()
