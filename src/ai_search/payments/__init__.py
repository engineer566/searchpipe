"""支付充值包 —— 虎皮椒过渡（后续换官方商户只换 provider 实现）。"""

from .provider import PaymentProvider
from .routes import router
from .service import (
    create_order_from_plan,
    fulfill_order,
    get_order,
    get_provider,
    handle_callback,
)
from .xunhupay import XunHuPayProvider

__all__ = [
    "PaymentProvider",
    "XunHuPayProvider",
    "create_order_from_plan",
    "fulfill_order",
    "get_order",
    "get_provider",
    "handle_callback",
    "router",
]
