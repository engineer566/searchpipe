"""用量路由 —— /usage/* 。

- GET /usage?start=&end=&granularity=  用量曲线（按天/小时聚合）
- GET /usage/logs?page=&size=          明细日志
- GET /usage/export                    CSV 导出
全部 Depends(get_current_user)。
"""

import csv
import io
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import User
from ..db.session import get_db
from .service import list_logs, query_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/usage", tags=["usage"])


class UsagePoint(BaseModel):
    bucket: str | None
    count: int
    credits: int
    avg_latency: int


class UsageResponse(BaseModel):
    points: list[UsagePoint]
    granularity: str


class UsageLogItem(BaseModel):
    id: str
    query: str
    max_results: int
    search_depth: str
    credits_consumed: int
    latency_ms: int | None
    status: str
    error_msg: str | None
    created_at: datetime


class UsageLogListResponse(BaseModel):
    items: list[UsageLogItem]
    total: int
    page: int
    size: int


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


@router.get("", response_model=UsageResponse)
async def usage(
    start: str | None = Query(None),
    end: str | None = Query(None),
    granularity: str = Query("day", pattern="^(hour|day)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageResponse:
    points = await query_usage(
        db, user.id, start=_parse_dt(start), end=_parse_dt(end), granularity=granularity
    )
    return UsageResponse(
        points=[UsagePoint(**p) for p in points], granularity=granularity
    )


@router.get("/logs", response_model=UsageLogListResponse)
async def usage_logs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UsageLogListResponse:
    items, total = await list_logs(db, user.id, page=page, size=size)
    return UsageLogListResponse(
        items=[
            UsageLogItem(
                id=str(it.id),
                query=it.query,
                max_results=it.max_results,
                search_depth=it.search_depth,
                credits_consumed=it.credits_consumed,
                latency_ms=it.latency_ms,
                status=it.status,
                error_msg=it.error_msg,
                created_at=it.created_at,
            )
            for it in items
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/export")
async def usage_export(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """CSV 导出全部用量明细。"""
    items, _ = await list_logs(db, user.id, page=1, size=10000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["created_at", "query", "max_results", "search_depth", "credits_consumed",
         "latency_ms", "status", "error_msg"]
    )
    for it in items:
        writer.writerow(
            [it.created_at.isoformat(), it.query, it.max_results, it.search_depth,
             it.credits_consumed, it.latency_ms, it.status, it.error_msg or ""]
        )

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=usage.csv"},
    )
