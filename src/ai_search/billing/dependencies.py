"""计费依赖与辅助。

设计：/search 端点先鉴权（get_current_user_or_api_key），拿到 SearchRequest 后
按 search_depth 算真实成本 → 调 charge_search 扣费 → 搜索失败时 refund_search 退还。
不把扣费做成 Depends（依赖层拿不到 body），而是在端点内显式调用，逻辑清晰且成本准确。
"""

import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.core import AuthContext
from .pipeline import ChargeResult, charge_credits, credit_cost
from .service import InsufficientCreditsError, deduct_credits, refund_credits

logger = logging.getLogger(__name__)


def stamp_request(request: Request, ctx: AuthContext, cost: int, balance: int) -> None:
    """把计费/鉴权信息挂到 request.state，供 UsageLog 中间件读取。"""
    request.state.user_id = ctx.user_id
    request.state.api_key_id = ctx.api_key_id
    request.state.credits_consumed = cost
    request.state.credits_refunded = False
    request.state.balance_after = balance


async def charge_search(
    request: Request,
    db: AsyncSession,
    ctx: AuthContext,
    search_depth: str,
) -> int:
    """扣费并把信息挂 request.state。返回扣费后余额。

    委托纯核 billing.pipeline.charge_credits 做 DB 操作，本层只补
    request.state 盖章（供 UsageLog 中间件）。余额不足抛 InsufficientCreditsError。
    """
    charge = await charge_credits(db, ctx, search_depth)
    stamp_request(request, ctx, charge.cost, charge.balance_after)
    request.state.search_req_ref = charge.req_ref  # 退款用同一 ref
    return charge.balance_after


async def refund_search(request: Request, db: AsyncSession) -> None:
    """搜索失败时退还本次扣费（幂等：credits_refunded 标志防重复退）。"""
    if getattr(request.state, "credits_refunded", True):
        return
    cost = getattr(request.state, "credits_consumed", 0)
    user_id = getattr(request.state, "user_id", None)
    ref = getattr(request.state, "search_req_ref", "search:refund")
    if cost > 0 and user_id is not None:
        await refund_credits(db, user_id, cost, remark=f"refund:{ref}")
        request.state.credits_refunded = True
        request.state.credits_consumed = 0
