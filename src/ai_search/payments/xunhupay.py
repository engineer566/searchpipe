"""虎皮椒（XunHuPay）支付 —— 个人开发者过渡方案。

灰区说明：虎皮椒是第三方聚合支付，无需商户资质即可收个人收款，但无发票、
B2B 受阻、存在跑路/冻卡风险。个体工商户注册后应尽快换微信/支付宝官方商户。

协议要点（虎皮椒 API v4）：
- 下单：POST https://api.xunhupay.com/payment/do.html
  参数 appid + 金额 + 订单号 + 通知 URL + 回调 URL，签名 sign = MD5(参数排序 + appsecret)
- 回调：GET/POST 通知带 trade_no/out_trade_no/total_fee/paid_at，校验 hash 签名
- 本实现：下单返回支付页 URL；回调验签。签名按官方文档 key 排序 + MD5。
"""

import hashlib
import logging
from urllib.parse import urlencode

import httpx

from ..config import get_settings
from .provider import PaymentProvider

logger = logging.getLogger(__name__)


class XunHuPayProvider(PaymentProvider):
    """虎皮椒支付。"""

    API_BASE = "https://api.xunhupay.com/payment/do.html"

    def __init__(self) -> None:
        s = get_settings()
        self.appid = s.xunhupay_appid
        self.appsecret = s.xunhupay_appsecret
        self.notify_url = s.xunhupay_notify_url

    @property
    def name(self) -> str:
        return "xunhupay"

    def _sign(self, params: dict) -> str:
        """虎皮椒签名：参数按 key 排序 → key1=val1&key2=val2 → 追加 appsecret → MD5 小写。"""
        # 过滤空值与 sign 本身
        items = sorted((k, v) for k, v in params.items() if v not in (None, "") and k != "hash")
        raw = urlencode(items) + self.appsecret
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def create_order(
        self, order_no: str, amount_cents: int, subject: str
    ) -> str:
        """下单 → 返回支付页 URL（H5/扫码）。amount_cents 转 元 字符串。"""
        if not self.appid or not self.appsecret:
            raise RuntimeError("虎皮椒未配置 appid/appsecret")

        params = {
            "version": "1.1",
            "appid": self.appid,
            "trade_order_id": order_no,
            "total_fee": f"{amount_cents / 100:.2f}",
            "title": subject,
            "time": str(int(__import__("time").time())),
            "notify_url": self.notify_url,
            "nonce_str": order_no,
            "type": "WAP",  # WAP（H5）；可按端扩展 WX/ALIPAY
        }
        params["hash"] = self._sign(params)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self.API_BASE, data=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"虎皮椒下单失败: {data.get('errmsg')}")
        url = data.get("url") or data.get("qr_code") or ""
        if not url:
            raise RuntimeError(f"虎皮椒未返回支付链接: {data}")
        logger.info("虎皮椒下单成功 order=%s", order_no)
        return url

    def verify_callback(self, params: dict) -> bool:
        """校验回调签名。params 含 hash 字段。"""
        received = params.get("hash") or params.get("sign")
        if not received:
            return False
        expected = self._sign(params)
        return received.lower() == expected.lower()
