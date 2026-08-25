"""积分计费包 —— 预付费 + 免费额度 + 行锁防超扣。

对 /search 端点：charge_search 扣费 → 失败 refund_search 退还。
对用户：/billing 查余额/流水/套餐。
"""

from .dependencies import charge_search, credit_cost, refund_search
from .pipeline import ChargeResult, charge_credits, refund_credits_for
from .service import (
    InsufficientCreditsError,
    deduct_credits,
    get_balance,
    grant_credits,
    refund_credits,
)

__all__ = [
    "ChargeResult",
    "InsufficientCreditsError",
    "charge_credits",
    "charge_search",
    "credit_cost",
    "deduct_credits",
    "get_balance",
    "grant_credits",
    "refund_credits",
    "refund_credits_for",
    "refund_search",
]
