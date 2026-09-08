# SearchPipe 项目记忆

`AGENTS.md` 是 Agent 行为规则入口；本文件和 `docs/memory/` 是项目背景资料。进入项目先读本索引，再按当前任务读取对应条目。

- 建立时间：2026-09-08
- 记忆条目：3 条

## 记忆条目

| 名称 | 类型 | 适用场景 |
|---|---|---|
| [`searchpipe-overview`](memory/searchpipe-overview.md) | project | 架构、商业化模块全景、测试与开发的硬约束 |
| [`searchpipe-auth-design`](memory/searchpipe-auth-design.md) | project | 邮箱注册/登录/忘记密码的设计决策与已知限制 |
| [`searchpipe-test-server`](memory/searchpipe-test-server.md) | reference | **测试服地址**、部署方法、本机 Docker 环境的坑 |

## 按任务读取

- 改任何代码前不了解项目结构：读 `searchpipe-overview`（或先看 `docs/INDEX.md`）。
- 改鉴权、注册登录、发邮件：读 `searchpipe-auth-design`。
- 部署、「上测试服」、本机起容器：读 `searchpipe-test-server`。

## 记忆维护约定

- 新沉淀的经验按主题单建文件放进 `docs/memory/`，并在本索引登记（名称/类型/适用场景/按任务读取）。
- 记忆定格于写入日期；与代码或现实冲突时，以实际为准并顺手修订条目。
