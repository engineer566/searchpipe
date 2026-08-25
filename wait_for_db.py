"""容器启动前探测 postgres 是否可连接。

entrypoint.sh 轮询调用：返回 0 表示可连接，非 0 表示尚未就绪。
独立成文件，避免 sh 内联 python -c 的续行/缩进陷阱。
"""

import asyncio
import sys

import asyncpg

from ai_search.config import get_settings


async def _probe() -> None:
    url = get_settings().database_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(url)
    await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(_probe())
    except Exception:  # noqa: BLE001
        sys.exit(1)
    sys.exit(0)
