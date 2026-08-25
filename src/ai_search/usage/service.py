"""用量追踪服务 —— 落库查询 + 聚合统计。

UsageLog 记录每次搜索：user_id/api_key_id/query/max_results/search_depth/
credits_consumed/latency_ms/status/error_msg。
日志留存 ≥6 个月（网安法第21条），retention_clean 删 6 个月前记录。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import UsageLog

logger = logging.getLogger(__name__)

RETENTION_DAYS = 183  # ≥6 个月


async def record_usage(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    api_key_id: uuid.UUID | None,
    query: str,
    max_results: int,
    search_depth: str,
    credits_consumed: int,
    latency_ms: int,
    status: str,
    error_msg: str | None = None,
) -> UsageLog:
    """落一条用量日志。"""
    log = UsageLog(
        user_id=user_id,
        api_key_id=api_key_id,
        query=query,
        max_results=max_results,
        search_depth=search_depth,
        credits_consumed=credits_consumed,
        latency_ms=latency_ms,
        status=status,
        error_msg=error_msg,
    )
    db.add(log)
    await db.flush()
    return log


async def query_usage(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    granularity: str = "day",
) -> list[dict]:
    """聚合用量统计（按天/小时分组）。返回 [{bucket, count, credits, avg_latency}]。"""
    end = end or datetime.now(timezone.utc)
    start = start or (end - timedelta(days=30))

    trunc = func.date_trunc(granularity, UsageLog.created_at).label("bucket")
    stmt = (
        select(
            trunc,
            func.count().label("count"),
            func.coalesce(func.sum(UsageLog.credits_consumed), 0).label("credits"),
            func.coalesce(func.avg(UsageLog.latency_ms), 0).label("avg_latency"),
        )
        .where(
            UsageLog.user_id == user_id,
            UsageLog.created_at >= start,
            UsageLog.created_at < end,
        )
        .group_by(trunc)
        .order_by(trunc)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "bucket": r.bucket.isoformat() if r.bucket else None,
            "count": r.count,
            "credits": int(r.credits),
            "avg_latency": int(r.avg_latency),
        }
        for r in rows
    ]


async def list_logs(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    size: int = 20,
) -> tuple[list[UsageLog], int]:
    """分页查明明细。"""
    total = (
        await db.execute(
            select(func.count()).select_from(UsageLog).where(UsageLog.user_id == user_id)
        )
    ).scalar_one()

    stmt = (
        select(UsageLog)
        .where(UsageLog.user_id == user_id)
        .order_by(UsageLog.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = (await db.execute(stmt)).scalars().all()
    return list(items), total


async def retention_clean(db: AsyncSession) -> int:
    """删除 6 个月前的用量日志（合规留存下限的清理）。返回删除条数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    stmt = delete(UsageLog).where(UsageLog.created_at < cutoff)
    result = await db.execute(stmt)
    deleted = result.rowcount or 0
    if deleted:
        logger.info("清理 %d 条过期用量日志（>=%d 天前）", deleted, RETENTION_DAYS)
    return deleted
