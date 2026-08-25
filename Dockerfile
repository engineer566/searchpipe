# syntax=docker/dockerfile:1
# 基于 uv 的 Python 应用镜像 —— 多阶段构建，最终镜像不含构建工具与缓存。

ARG PYTHON_VERSION=3.13

# ---- 构建阶段：装依赖到 .venv ----
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 先拷依赖清单与 lockfile，利用层缓存（代码改动不重装依赖）
COPY pyproject.toml uv.lock ./
# README 不存在时用占位（hatchling 元数据解析可能引用它）
COPY README.md* ./
# 需要源码才能解析 package metadata（hatchling 按 packages=["src/ai_search"] 定位）
COPY src/ ./src/

# 同步依赖到 .venv（--frozen 保证按 lockfile 精确安装，不改写）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- 运行阶段：精简运行镜像 ----
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

# 运行时仅需 .venv 与源码
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 让 pydantic-settings 找到 .env（容器内 cwd=/app）
    HF_HOME=/tmp/.cache

WORKDIR /app

# 复制虚拟环境
COPY --from=builder /app/.venv /app/.venv
# 复制源码与项目元数据
COPY pyproject.toml ./
COPY README.md* ./
COPY src/ ./src/
# 迁移脚本与配置（启动前自动建表/升级）
COPY alembic.ini ./
COPY alembic/ ./alembic/
# postgres 就绪探测脚本（entrypoint 轮询调用）
COPY wait_for_db.py ./
# 容器入口：先 alembic upgrade head，再起 uvicorn
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# 健康检查：/healthz 端点（start-period 留足迁移时间）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
