# searchpipe 邮箱注册/登录/密码重置设计

> 类型：project · 写入：2026-09-08（该功能当次会话实现）

## 范围决策（用户拍板）

- 只做**邮箱**注册/登录；GitHub/微信 OAuth **后端接口保留，前端一律不展示**（login.html 已移除全部第三方入口）。
- **注册需邮箱验证**：注册后发送验证邮件，用户点击链接激活账号（防虚假注册骗积分）。
- 密码策略仅最短 8 位。
- 忘记密码走邮件重置链接（不做短信）。

## 实现要点

- **API**（`auth/routes.py`）：`/auth/register`（201，注册送免费额度+发验证邮件）、`/auth/login`、`/auth/refresh`、`/auth/forgot-password`、`/auth/reset-password`、`/auth/verify-email`（GET，验证邮箱）、`/auth/resend-verification`（POST，重发验证邮件）、`/auth/me`。邮箱一律 `strip().lower()` 规范化；register 捕获 `IntegrityError` 兜底并发竞态 → 409。
- **控制台**（`dashboard/routes.py`）：表单失败**重渲染页面带中文错误 + 回填邮箱**，不裸抛 4xx；注册成功后跳转到登录页提示查收验证邮件；未验证用户访问 dashboard 显示警告卡片+重发按钮。
- **重置 token**：Redis 一次性 token（`pwdreset:token:*`，TTL 1h，消费即删）；每邮箱 60s 发信冷却（`pwdreset:cooldown:*`）；forgot-password 对存在/不存在的邮箱返回**同一话术**（防邮箱枚举）。
- **验证 token**：Redis 一次性 token（`verify:token:*`，TTL 24h，消费即删）；每邮箱 60s 发信冷却（`verify:cooldown:*`）；User 模型新增 `email_verified` 布尔字段（默认 false，验证后 true）。
- **邮件**（`utils/mailer.py`）：stdlib smtplib + `asyncio.to_thread`，零新依赖；**SMTP 未配置时降级为日志输出**，永不抛错阻断流程。配置项 `SMTP_*` + `APP_BASE_URL`（拼重置/验证链接）。
- **免费额度发放**走 `billing.service.grant_credits` **惰性导入**（防 billing→auth 循环依赖），dashboard 与 API 两处同模式。

## API Key 存储与「可查看」设计（2026-09-12 需求 #4）

- **三层存储**：`key_prefix`（前 8 位，列表展示 + 鉴权粗筛）／`key_hash`（argon2，**唯一鉴权凭据**）／`key_cipher`（Fernet 密文，只为「查看明文 / 生成 MCP 链接」）。鉴权链路一行未改，密文泄露但没有主密钥也解不出明文。
- **主密钥派生**：`settings.key_encryption_secret` → 留空回退 `session_cookie_secret`，经 sha256 → urlsafe base64 得 Fernet key。**换掉主密钥后历史 Key 无法再查看明文（鉴权仍正常）**，所以生产若要轮换 cookie 密钥，先显式配置 `KEY_ENCRYPTION_SECRET`。
- **读取面收窄**：明文只经 `GET /api-keys/reveal`（默认 Key）与 `GET /api-keys/{id}/reveal`（指定 Key）返回，且这两个端点用 `get_current_user`——带 `sp-` Key 的请求会被当 cookie 兜底失败 → 401，即**Key 不能自举读 Key**；越权/已吊销/非法 id 统一 404；老 Key 无密文 409 提示重建。前端默认打码渲染，明文只在用户点击「显示」后进入 DOM。
- **默认 Key**：`is_default` 标记。邮箱验证通过（`auth/routes.verify_email`）即 `ensure_default_key()`，用户不用先去控制台建 Key 就能一句话接入 MCP；`/dashboard` 与 `/dashboard/api-keys` 对已验证老用户惰性补齐；吊销默认 Key 自动把剩下最新一把提升为默认；全部吊销后再取默认会新建一把。
- **已知取舍**：默认 Key 无独立轮换流程（吊销即换）；`is_default` 是单标记而非唯一索引，并发建首把 Key 理论上可能两把都标记默认（控制台按「最新」展示，影响面小）。

## 已知限制（后续可改）

- session cookie / JWT 是无状态签名，**重置密码后旧登录态不能强制踢下线**；要做得给 User 加 token 版本号。
- 重置成功跳转用 `/dashboard/login?reset=1` 带提示；邮件是纯文本，未做 HTML 模板。
- **未验证邮箱的用户可以登录，但 /search 与 MCP 调用会被 403 拒绝**（2026-09-12 起 `auth/core.require_email_verified`，admin/owner 豁免）；dashboard 另显示警告卡片 + 重发按钮。

## 测试范式

`tests/test_auth.py`：monkeypatch `ai_search.utils.mailer.send_mail` 捕获邮件（发信在请求 task 内，无跨 loop 问题）；dashboard 表单测试用 `data=` 提交 + `follow_redirects=False` 断言 303 与 cookie。
