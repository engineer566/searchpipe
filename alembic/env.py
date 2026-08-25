"""Alembic env —— async engine + autogenerate。

迁移用真实 DATABASE_URL（从 Settings 读，覆盖 alembic.ini 的占位）。
target_metadata 指向 Base.metadata，autogenerate 对比 DB 现状与模型定义。
"""

import asyncio
import logging
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from ai_search.config import get_settings
from ai_search.db.base import Base
# 导入所有模型，确保 metadata 注册（autogenerate 才能发现表）
from ai_search.db import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 用应用配置的真实 DATABASE_URL 覆盖 ini 占位
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata
log = logging.getLogger("alembic.env")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
