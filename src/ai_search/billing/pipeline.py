"""协议无关的计费管线 —— 纯 DB 操作，不触碰 request.state。

HTTP /search（billing.dependencies.charge_search）与 MCP tool
（mcp_server.py）共用此模块，保证扣费/退款逻辑单一真相源、不漂移。

charge_credits / refund_credits_for 只做 DB 操作，不带 HTTP Request；
HTTP 依赖层在调用后自行 stamp_request 把信息挂 request.state 供中间件用。
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.core import AuthContext
from ..config import get_settings
from .service import InsufficientCreditsError, deduct_credits, refund_credits


def credit_cost(search_depth: str) -> int:
    """按 search_depth 返回本次搜索积分成本。"""
    settings = get_settings()
    if search_depth == "advanced":
        return settings.credit_cost_advanced
    return settings.credit_cost_basic


@dataclass
class ChargeResult:
    """单次扣费结果。供 HTTP（stamp request.state）和 MCP（直接用）共用。"""

    cost: int
    balance_after: int
    req_ref: str  # 退款用同一 ref
    user_id: uuid.UUID
    api_key_id: uuid.UUID | None


async def charge_credits(
    db: AsyncSession,
    ctx: AuthContext,
    search_depth: str,
) -> ChargeResult:
    """扣费（纯 DB）。余额不足抛 InsufficientCreditsError。

    生成 req_ref 供失败退款对账（remark 一致）。
    """
    cost = credit_cost(search_depth)
    req_ref = f"search:{uuid.uuid4().hex[:12]}"
    balance = await deduct_credits(db, ctx.user_id, cost, remark=req_ref)
    return ChargeResult(
        cost=cost,
        balance_after=balance,
        req_ref=req_ref,
        user_id=ctx.user_id,
        api_key_id=ctx.api_key_id,
    )


async def refund_credits_for(
    db: AsyncSession,
    charge: ChargeResult,
) -> int:
    """按 ChargeResult 退还（纯 DB）。返回退款后余额。

    搜索失败 / 输出审核违规时调用。remark 复用 charge.req_ref 便于对账。
    """
    if charge.cost <= 0:
        return charge.balance_after
    return await refund_credits(
        db,
        charge.user_id,
        charge.cost,
        remark=f"refund:{charge.req_ref}",
    )
