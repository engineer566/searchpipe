# searchpipe 测试服与部署

> 类型：reference · 写入：2026-09-08

## 测试服地址

**`http://47.98.124.167:8001`**（用户 2026-09-08 明确：测试服是这台远端机器，**不是本机**）。

- SSH key：`/home/wuyuming/Projects/test_host.pem`（权限 600；用户 2026-09-08 提供。登录用户名/端口未确认，首次连接前向用户确认，典型用法 `ssh -i test_host.pem <user>@47.98.124.167`）。
- 截至写入日该地址尚未部署/不可达（healthz 超时）。
- 部署后必须在远端配置 `APP_BASE_URL=http://47.98.124.167:8001`，否则密码重置邮件里的链接会是 localhost。

## 部署方法（本机验证过，远端同理）

```bash
docker build --build-arg UV_COMPILE_BYTECODE=0 -t searchpipe:latest .
docker-compose -p ai-search -f docker-compose.test.yml up -d app
```

- `docker-compose.test.yml` 的 app 服务只引用镜像不构建，必须先 `docker build`。
- 容器启动时 entrypoint 自动 `alembic upgrade head`，无需手工迁移。

## 本机 Docker 环境的坑（2026-09-08 实测）

1. **dockerd 未配 default-ulimits**，容器内 nofile=1024 → `uv sync` 字节码并行编译耗尽 fd 报 "No file descriptors available"。对策：构建加 `--build-arg UV_COMPILE_BYTECODE=0`（Dockerfile 已参数化，默认 1 不变）。
2. **必须 `-p ai-search`**：仓库目录改名 searchpipe 后 compose 默认项目名变了，不指定会撞容器名并误建空数据卷（旧卷 `ai-search_ai-search-pgdata` 有数据）。
3. 无 `docker compose` 插件，用独立二进制 `docker-compose`（v5.1.1）。
4. 本机 8000 端口被 docparse 占用，app 映射到 **8001**。

## 本机容器管理

```bash
docker start ai-search-postgres ai-search-redis ai-search-searxng   # 测试依赖
docker start ai-search-app                                          # 本机跑完整栈（非常态）
docker stop ai-search-app ai-search-searxng ai-search-redis ai-search-postgres  # 全停
```

2026-09-08 起本机容器默认保持停止，仅跑测试时启动。
