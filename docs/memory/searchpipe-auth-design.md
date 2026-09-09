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

## 已知限制（后续可改）

- session cookie / JWT 是无状态签名，**重置密码后旧登录态不能强制踢下线**；要做得给 User 加 token 版本号。
- 重置成功跳转用 `/dashboard/login?reset=1` 带提示；邮件是纯文本，未做 HTML 模板。
- **未验证邮箱的用户仍可登录和使用基本功能**，仅 dashboard 显示警告提示；如需严格限制，可在鉴权依赖中检查 `email_verified`。

## 测试范式

`tests/test_auth.py`：monkeypatch `ai_search.utils.mailer.send_mail` 捕获邮件（发信在请求 task 内，无跨 loop 问题）；dashboard 表单测试用 `data=` 提交 + `follow_redirects=False` 断言 303 与 cookie。
