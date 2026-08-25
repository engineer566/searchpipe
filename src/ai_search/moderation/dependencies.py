"""审核依赖 —— /search 输入审核前置。

moderate_input：若 moderation_enabled，校验 req.query，命中违禁抛 400。
输出审核在 /search 端点拿到 answer 后调 check_output（见 main.py 端点薄层）。
"""

import logging

from fastapi import HTTPException, status

from .service import ModerationError, check_input

logger = logging.getLogger(__name__)


async def moderate_input(query: str) -> None:
    """审核搜索输入。命中违禁抛 HTTP 400。

    在 /search 端点内显式调用（拿到 req.query 后），不做成 Depends 以便扣费顺序控制：
    先审核输入 → 再扣费 → 再搜索。输入违禁不扣费。
    """
    try:
        await check_input(query)
    except ModerationError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"输入内容违规: {e.labels}",
        ) from e
