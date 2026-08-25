"""日志配置。"""

import logging
import sys
from typing import TextIO


def setup_logging(level: str = "INFO", *, stream: TextIO = sys.stdout) -> None:
    """配置根 logger。

    stream 默认 stdout（FastAPI 场景）；MCP stdio server 必须传 stderr，
    否则日志会写入 stdout 的 JSON-RPC 协议流、破坏帧。
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=stream,
    )
