"""短信验证码 —— 抽象接口 + 阿里云/腾讯云实现。

实名制登录核心：手机号 + 短信验证码（网络安全法第24条 + 深度合成规定第9条）。
验证码存 Redis（sms:code:{phone}，TTL 5min），同号 60s 频控（sms:lock:{phone}）。
"""

import logging
import secrets
from abc import ABC, abstractmethod

from ..config import get_settings
from ..utils.cache import get_cache

logger = logging.getLogger(__name__)

CODE_TTL_SEC = 300        # 验证码 5 分钟有效
SEND_LOCK_SEC = 60        # 同号 60s 内只能发一次
CODE_DIGITS = 6


class SMSSender(ABC):
    """短信发送接口。实现类负责对接具体云厂商 API。"""

    @abstractmethod
    async def send(self, phone: str, code: str) -> None: ...


class AliyunSMSSender(SMSSender):
    """阿里云短信。需配置 SMS_ACCESS_KEY / SMS_SECRET_KEY / SMS_SIGN_NAME / SMS_TEMPLATE_CODE。

    实际签名用 aliyun-python-sdk-core；此处用 httpx 直调 REST 接口（轻量，免装 SDK）。
    模板须含 ${code} 变量。
    """

    ENDPOINT = "https://dysmsapi.aliyuncs.com"

    async def send(self, phone: str, code: str) -> None:
        import httpx

        s = get_settings()
        # 生产应实现完整阿里云签名（HMAC-SHA1 + 参数排序）；此处记录待办，
        # 真实部署补全签名逻辑或换 aliyun-python-sdk-core。
        if not s.sms_access_key or not s.sms_template_code:
            logger.warning("阿里云短信未配置完整，跳过真实发送（phone=%s code=%s）", phone, code)
            return
        # TODO: 实现阿里云短信 REST 签名（按 AccessKeySecret HMAC-SHA1）。
        # 当前为占位：避免引入额外 SDK 依赖，部署前补全。
        logger.info("阿里云短信发送（占位实现）: phone=%s", phone)


class TencentSMSSender(SMSSender):
    """腾讯云短信（占位，同阿里云思路）。"""

    async def send(self, phone: str, code: str) -> None:
        logger.warning("腾讯云短信未实装（phone=%s code=%s）", phone, code)


def get_sms_sender() -> SMSSender:
    """按配置返回短信发送器单例。"""
    s = get_settings()
    if s.sms_provider == "tencent":
        return TencentSMSSender()
    return AliyunSMSSender()


_sender: SMSSender | None = None


def _get_sender() -> SMSSender:
    global _sender
    if _sender is None:
        _sender = get_sms_sender()
    return _sender


async def send_code(phone: str) -> None:
    """生成 6 位验证码，存 Redis，频控，发短信。60s 内重复发返回错误（由调用方转 HTTP）。"""
    cache = get_cache()
    lock_key = f"sms:lock:{phone}"
    if await cache.exists(lock_key):
        raise ValueError("发送过于频繁，请稍后再试")

    code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
    await cache.set(f"sms:code:{phone}", code, ttl=CODE_TTL_SEC)
    await cache.set(lock_key, "1", ttl=SEND_LOCK_SEC)

    await _get_sender().send(phone, code)
    logger.info("短信验证码已发送: phone=%s", phone)


async def verify_code(phone: str, code: str) -> bool:
    """校验验证码。成功后删除（一次性）。"""
    cache = get_cache()
    stored = await cache.get(f"sms:code:{phone}")
    if not stored or stored != code:
        return False
    await cache.delete(f"sms:code:{phone}")
    return True
