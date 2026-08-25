"""阿里云内容安全文本检测 —— green.textScan。

阿里云内容安全 REST 接口需 AccessKey 签名（HMAC-SHA1 + Base64）。
完整 SDK 实现较重，此处用 httpx 直调 + 签名占位；部署前补全签名或换 alibabacloud-green20220302 SDK。

输入审核（query）+ 输出审核（answer）共用此 provider。
"""

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
import uuid

import httpx

from ..config import get_settings
from .provider import ModerationProvider, ModerationResult

logger = logging.getLogger(__name__)


class AliyunModerationProvider(ModerationProvider):
    """阿里云内容安全文本检测。"""

    ENDPOINT = "https://green.cn-shanghai.aliyuncs.com"
    SERVICE = "Green"
    VERSION = "2018-05-09"
    REGION = "cn-shanghai"

    def __init__(self) -> None:
        s = get_settings()
        self.access_key = s.moderation_access_key
        self.access_secret = s.moderation_secret_key

    @property
    def name(self) -> str:
        return "aliyun"

    def _sign(self, params: dict) -> str:
        """阿里云 RPC 签名：参数排序 → urlencode → HMAC-SHA1(secret + &) → Base64。"""
        sorted_items = sorted(params.items())
        canonical = urllib.parse.urlencode(sorted_items, quote_via=urllib.parse.quote)
        string_to_sign = "POST&" + urllib.parse.quote(
            "/", safe=""
        ) + "&" + urllib.parse.quote(canonical, safe="")
        key = (self.access_secret + "&").encode("utf-8")
        digest = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def check_text(self, text: str) -> ModerationResult:
        """检测文本。命中违规场景返回 passed=False。"""
        if not self.access_key or not self.access_secret:
            logger.warning("阿里云内容安全未配置，跳过审核（放行）")
            return ModerationResult(passed=True)

        # 阿里云 RPC 公共参数
        params = {
            "Format": "JSON",
            "Version": self.VERSION,
            "AccessKeyId": self.access_key,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Action": "TextScan",
            "RegionId": self.REGION,
            "Service": self.SERVICE,
        }
        # 业务参数：scenes + tasks
        params["Scenes.1"] = "antispam"  # 文本反垃圾
        params["Tasks.1.Content"] = text[:10000]  # 限长 10k

        try:
            params["Signature"] = self._sign(params)
        except Exception as e:  # noqa: BLE001
            logger.exception("阿里云审核签名失败: %s", e)
            return ModerationResult(passed=True, detail="签名失败，放行")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.ENDPOINT,
                    data=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.exception("阿里云审核请求失败（放行）: %s", e)
            # 审核服务故障时 fail-open（内测期），上线前应改 fail-close
            return ModerationResult(passed=True, detail=f"审核服务故障: {e}")

        # 解析结果：data.data.results[].suggestion (pass/review/block)
        try:
            results = data["Data"]["Results"]
            labels = []
            for r in results:
                if r.get("suggestion") == "block":
                    labels.append(r.get("label", "unknown"))
            if labels:
                return ModerationResult(
                    passed=False, labels=labels, detail=f"命中违禁: {labels}"
                )
            return ModerationResult(passed=True)
        except (KeyError, TypeError) as e:
            logger.warning("阿里云审核响应解析失败: %s data=%s", e, data)
            return ModerationResult(passed=True, detail="响应解析失败，放行")
