"""邮件发送 —— 标准库 smtplib + asyncio.to_thread（不新增依赖）。

供密码重置等事务邮件使用。SMTP 未配置（smtp_host 为空）时降级为日志输出，
不阻断业务流程：内测/dev 环境可直接从日志取重置链接。
发送失败只记日志返回 False，不向调用方抛错（忘记密码接口需防邮箱枚举，
任何情况下都返回统一话术）。
"""

import asyncio
import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from ..config import get_settings

logger = logging.getLogger(__name__)


def _send_sync(to: str, subject: str, text: str) -> None:
    """同步发送（run in thread）。TLS 用 SMTP_SSL，否则明文 + 可选登录。"""
    settings = get_settings()
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to

    if settings.smtp_use_tls:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=15
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
    with server:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(msg["From"], [to], msg.as_string())


async def send_mail(to: str, subject: str, text: str) -> bool:
    """发送纯文本邮件。成功 True；未配置/失败降级并返回 False。"""
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning(
            "SMTP 未配置，邮件降级为日志输出 → %s\n主题: %s\n%s", to, subject, text
        )
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, text)
    except Exception:  # noqa: BLE001 —— 发信失败不应 500，调用方话术不变
        logger.exception("邮件发送失败 → %s", to)
        return False
    logger.info("邮件已发送 → %s（%s）", to, subject)
    return True
