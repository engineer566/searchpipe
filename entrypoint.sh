#!/bin/sh
# 容器启动入口：先等 postgres 就绪 → 跑数据库迁移 → 起 uvicorn。
# alembic upgrade head 是幂等的——已应用的迁移会跳过，只执行新增的。
set -e

echo "[entrypoint] 等待 postgres 就绪 ..."
# 轮询调用独立探测脚本（避免 sh 内联 python -c 的续行陷阱）
for i in $(seq 1 30); do
    if python wait_for_db.py; then
        echo "[entrypoint] postgres 可连接"
        break
    fi
    echo "[entrypoint] postgres 尚未就绪，${i}/30 重试 ..."
    sleep 2
done

echo "[entrypoint] 执行 alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] 启动 uvicorn ..."
exec uvicorn ai_search.main:app --host 0.0.0.0 --port 8000
