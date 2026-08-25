"""数据库基础 —— DeclarativeBase + 异步 engine + session factory。

engine / session_factory 为模块级单例（get_settings 经 @lru_cache 保证全局唯一），
与 search_service.py 的搜索组件单例正交共存：搜索组件无状态，DB 按请求注入 AsyncSession。
"""

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _build_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        s.database_url,
        echo=s.database_echo,
        pool_pre_ping=True,
    )


engine: AsyncEngine = _build_engine()
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


async def dispose_engine() -> None:
    """应用关闭时释放连接池。"""
    await engine.dispose()
    logger.info("数据库 engine 已释放")
