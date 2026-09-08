# searchpipe 测试服与部署

> 类型：reference · 写入：2026-09-08（当日完成首次部署验证后修订）

## 测试服

**`http://47.98.124.167:8001`** —— 远端阿里云主机（Ubuntu 22.04，**x86_64**，1.6G 内存小机器），不是本机。

- **SSH**：`ssh -i /home/wuyuming/Projects/test_host.pem root@47.98.124.167`（root 可登录，ubuntu/ecs-user 不行）
- **安全组按 IP 白名单放行**：未放行的 IP 访问 8001 会超时（不是服务挂了）。验证服务用 SSH 登进去 curl 127.0.0.1。
- 部署目录 `/opt/searchpipe`（**不是 git 仓库**，rsync 同步的代码副本）；compose 项目名 `searchpipe`。
- 同机还跑着 aitrendwatch（8080）。容器：`ai-search-app`(8001) / `ai-search-postgres` / `ai-search-redis` / `ai-search-searxng`。
- `.env` 已配 `APP_BASE_URL=http://47.98.124.167:8001`；SMTP 未配置，重置邮件看 `docker logs ai-search-app`。

## 部署流程（2026-09-08 实测通过）

```bash
# 1. 同步源码（⚠️ 必须排除 .env 和 docker-compose.yml，会覆盖远端配置！）
rsync -az --delete \
  --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache \
  --exclude=node_modules --exclude=.dsh --exclude=.claude --exclude=history \
  --exclude=.env --exclude=docker-compose.yml \
  -e "ssh -i /home/wuyuming/Projects/test_host.pem" \
  ./ root@47.98.124.167:/opt/searchpipe/

# 2. 服务器上原生构建（见下方架构坑）
ssh -i ... root@47.98.124.167 "cd /opt/searchpipe && docker build --build-arg UV_COMPILE_BYTECODE=0 -t searchpipe:latest ."

# 3. 重建 app（用 test 文件：app 服务是纯 image 引用；postgres 数据卷不动）
ssh -i ... root@47.98.124.167 "cd /opt/searchpipe && docker compose -f docker-compose.test.yml up -d app"
```

entrypoint 自动 `alembic upgrade head`；`docker compose` 插件可用（v5.5.0）。

## 坑（全是 2026-09-08 实测踩出来的）

1. **架构不同：本机 aarch64，测试服 x86_64。** 本机 `docker save | ssh docker load` 传的镜像会 `exec format error` 崩溃循环——**只能在服务器上原生构建**。
2. **rsync 会覆盖远端 `.env` / `docker-compose.yml`**（本地版本不含 APP_BASE_URL，且 app 服务带 build 段指向 ai-search:latest，会导致 compose 去 pull 不存在的镜像卡死）。部署必须加 `--exclude=.env --exclude=docker-compose.yml`。
3. 服务器 1.6G 内存：原生构建实测可用（uv sync 期间可用 ~500Mi），无需 swap；若将来 OOM 先加 swapfile。
4. `pkill -f 'docker compose'` 会匹配到自身 ssh 远程命令行把自己杀掉，别用。
5. 本机 dockerd 的坑（nofile=1024 需 UV_COMPILE_BYTECODE=0、compose 用 `-p ai-search`、venv shebang 失效用 `python -m`）仅适用于本机容器场景，本机默认不跑这套栈，只起依赖容器跑测试。

## 本机容器管理

```bash
docker start ai-search-postgres ai-search-redis ai-search-searxng   # 跑测试前起依赖
docker stop ai-search-postgres ai-search-redis ai-search-searxng    # 测完可停
```
