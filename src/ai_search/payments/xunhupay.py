"""虎皮椒（XunHuPay）支付 —— 个人开发者过渡方案。

灰区说明：虎皮椒是第三方聚合支付，无需商户资质即可收个人收款，但无发票、
B2B 受阻、存在跑路/冻卡风险。个体工商户注册后应尽快换微信/支付宝官方商户。

双渠道：虎皮椒后台按渠道各建一个应用（支付宝/微信各一套 appid/appsecret）。
下单按 pay_channel 选凭证；回调两渠道共用同一地址，验签时两套 secret 各验一次。

协议要点（虎皮椒 API v4）：
- 下单：POST https://api.xunhupay.com/payment/do.html
  参数 appid + 金额 + 订单号 + 通知 URL，签名 hash = MD5(参数排序 + appsecret)
- 回调：GET/POST 通知带 trade_no/trade_order_id/total_fee，校验 hash 签名
- 本实现：下单返回支付页 URL；回调验签。签名按官方文档 key 排序 + MD5。
"""

import hashlib
import logging

import httpx

from ..config import get_settings
from .provider import PaymentProvider

logger = logging.getLogger(__name__)


class XunHuPayProvider(PaymentProvider):
    """虎皮椒支付（支付宝/微信双渠道）。"""

    API_BASE = "https://api.xunhupay.com/payment/do.html"

    def __init__(self) -> None:
        s = get_settings()
        self.notify_url = s.xunhupay_notify_url
        # 渠道专属凭证，留空回退到通用配置（单渠道兼容）
        self._creds = {
            "alipay": (
                s.xunhupay_appid_alipay or s.xunhupay_appid,
                s.xunhupay_appsecret_alipay or s.xunhupay_appsecret,
            ),
            "wechat": (
                s.xunhupay_appid_wechat or s.xunhupay_appid,
                s.xunhupay_appsecret_wechat or s.xunhupay_appsecret,
            ),
        }

    @property
    def name(self) -> str:
        return "xunhupay"

    def available_channels(self) -> list[str]:
        """已配置凭证的渠道（虎皮椒按渠道各建应用，可能只开通其一）。"""
        return [ch for ch, (appid, secret) in self._creds.items() if appid and secret]

    @staticmethod
    def _sign(params: dict, appsecret: str) -> str:
        """虎皮椒签名：参数按 key 字典序排序 → 原始值拼接 key1=val1&key2=val2
        （不做 URL 编码）→ 末尾直接追加 appsecret → MD5 小写。"""
        # 过滤空值与 hash 本身
        items = sorted((k, str(v)) for k, v in params.items() if v not in (None, "") and k != "hash")
        raw = "&".join(f"{k}={v}" for k, v in items) + appsecret
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    async def create_order(
        self, order_no: str, amount_cents: int, subject: str, pay_channel: str = "alipay"
    ) -> str:
        """下单 → 返回支付页 URL（H5/扫码）。amount_cents 转 元 字符串。"""
        appid, appsecret = self._creds.get(pay_channel, (None, None))
        if not appid or not appsecret:
            raise RuntimeError(f"虎皮椒未配置 {pay_channel} 渠道 appid/appsecret")

        params = {
            "version": "1.1",
            "appid": appid,
            "trade_order_id": order_no,
            "total_fee": f"{amount_cents / 100:.2f}",
            "title": subject,
            "time": str(int(__import__("time").time())),
            "notify_url": self.notify_url,
            "nonce_str": order_no,
            "type": "WAP",  # WAP（H5 收银台，渠道由应用决定）
        }
        params["hash"] = self._sign(params, appsecret)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self.API_BASE, data=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errcode") != 0:
            raise RuntimeError(f"虎皮椒下单失败: {data.get('errmsg')}")
        url = data.get("url") or data.get("qr_code") or ""
        if not url:
            raise RuntimeError(f"虎皮椒未返回支付链接: {data}")
        logger.info("虎皮椒下单成功 order=%s channel=%s", order_no, pay_channel)
        return url

    def verify_callback(self, params: dict) -> bool:
        """校验回调签名。两渠道共用回调地址，用各渠道 secret 各验一次。"""
        received = params.get("hash") or params.get("sign")
        if not received:
            return False
        secrets = {secret for _, secret in self._creds.values() if secret}
        return any(
            received.lower() == self._sign(params, secret).lower() for secret in secrets
        )
