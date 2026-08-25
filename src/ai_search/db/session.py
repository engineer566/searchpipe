"""DB session —— FastAPI 依赖，按请求注入 AsyncSession。

用法：`db: AsyncSession = Depends(get_db)`。请求结束自动 commit/rollback/close。
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from .base import async_session_factory

logger = logging.getLogger(__name__)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """请求级 DB session。

    正常返回时提交；抛异常时回滚。始终关闭。
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
