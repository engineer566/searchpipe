# searchpipe 出海迁移要点（overseas 分支）

> 类型：reference · 写入：2026-09-14（出海重构完成后定稿）
> 适用场景：改支付 provider / webhook / 套餐映射、配 Creem/Dodo 后台、排查订阅到账问题、理解 USD 货币与英文化边界

## 分支与封存

- 开发分支：`overseas`。国内版已封存：`main` 打了 tag `archive/china-2026-09`，并建冻结分支 `china-archive`（均已推送 origin）。虎皮椒（`payments/xunhupay.py`）、`/payments/callback`、微信 OAuth stub、支付宝/微信渠道只存在于封存分支。
- 出海决策依据：`docs/overseas-migration-analysis.md`（ADR）。

## 支付层：MoR（Merchant of Record）模式

Creem / Dodo Payments 二选一（`PAYMENT_PROVIDER`），都是「托管收银台 redirect + JSON webhook 签名验证」，本系统不碰卡。

### 归一化事件接口（`payments/provider.py`）

所有 provider webhook 解析为 `PaymentEvent`，类型：

| 事件 | 发放动作 |
|---|---|
| `one_time_paid` | 充值发积分 |
| `subscription_checkout` | 只绑订阅（Creem checkout.completed），**不发积分** |
| `subscription_activated` | 绑订阅 + 发首期积分（Dodo subscription.active） |
| `subscription_paid` | 续期发积分（Creem subscription.paid / Dodo subscription.renewed） |
| `subscription_canceled` | 本地标记 canceled（已发积分不回收） |
| `ignored` | 与本系统无关的事件（如 Dodo 订阅单的 payment.succeeded），直接 ack |

### 两段式编排约定（容易踩）

- **Creem 两段式**：`checkout.completed` 只创建/绑定订阅，**首期积分由 `subscription.paid` 发放**——不要看到 checkout.completed 就发积分，会双发。
- **Dodo**：首期由 `subscription.active` 发放，续期是 `subscription.renewed`（`payment.succeeded` 对订阅单一律 ignored）。
- **幂等**：续期以 webhook 的 `event_id` 建 renew 订单，重复事件命中唯一约束不重复发积分。
- **归属校验**：`subscription_activated/subscription_paid` 必须校验事件里的 provider_subscription_id 与本地订阅归属一致，防串用户（`ee8cc5e` 修过这个洞）。
- **Webhook 路由**：`POST /payments/webhooks/{provider}`，raw body 验签；验签失败 400、处理异常 500（平台按此重试）。路由按 provider 名分发并优先匹配激活单例，测试可注入 FakeProvider 走 `/payments/webhooks/fakepay`。

### 验签差异

- Creem：单头 `creem-signature` = HMAC-SHA256 hex。
- Dodo：Standard Webhooks 规范三头 `webhook-id` / `webhook-timestamp` / `webhook-signature`（HMAC-SHA256 + base64）。

### Customer Portal

`GET /payments/portal` 生成自助管理链接（改支付方式/取消订阅）：Creem `POST /v1/customers/billing-portal`，Dodo `POST /customers/{id}/customer-portal/session`。

## 套餐 ↔ product 映射：plans.provider_products

- DB 迁移 `g1a2b3c4d5e6`：`plans.provider_products` JSONB（`{"creem": "prod_...", "dodo": "..."}`）；`subscriptions` 加 `provider` / `provider_subscription_id` / `provider_customer_id`。
- **部署新环境的动作**：在各平台后台手动建 product（价格、币种与种子档一致）→ 把 product id 回填进 `plans.provider_products` → 在平台后台把 webhook 端点配为 `{APP_BASE_URL}/payments/webhooks/{provider}`。代码里没有自动建 product 的逻辑。
- 自定义金额充值用「$1×units」的按量 product：`CREEM_CREDIT_PRODUCT_ID` / `DODO_CREDIT_PRODUCT_ID`（config），units = 美元数。
- 当前 USD 种子档：充值 $5/1000、$10/2100、$20/4400；订阅 Starter $4.99/1000、Pro $9.99/3000、Max $19.99/10000（划线价 = 2 倍现价）。旧 7 个 CNY 档已下架（不在 catalog 出现）。

## 货币与字段命名约定

- 全站 `currency="USD"`，价格内部存 `price_cents`（int），API 出参除以 100 为 float。
- API 字段出海化重命名：`price_yuan→price`、`original_price_yuan→original_price`、`credit_yuan_rate→credit_price_rate`、`max_recharge_yuan→max_recharge_amount`，新增 `currency`。旧字段名已删，接入方需同步改。
- 自定义充值汇率 `credit_price_rate="0.005"`（$0.005 = 1 积分），单笔上限 `max_recharge_usd=500`。
- `PayChannel` 枚举：`card` / `paypal`（托管收银台实际渠道由平台决定，下单时留空用支付方首个已声明渠道）。

## 英文化边界

- 用户可见文案全英文：dashboard 模板（除 `admin_*`）、公开页、SEO 元信息/JSON-LD（`priceCurrency=USD`）、OpenAPI 描述、LLM rerank prompt、moderation 违规消息、terms.html（英文 ToS）、新增 `/privacy` 页。
- **保留中文**：`admin_*` 管理端模板、源码内部注释与 docstring、文档（docs/、history/）。
- 站长验证只剩 Google / Bing（百度配置项已删）。

## 测试基线

- 全量 `.venv/bin/python -m pytest tests/ -q` = **203 passed, 5 skipped**。
- `tests/test_payments.py` 重写：FakeProvider 走 `/payments/webhooks/fakepay` 注入归一化事件。
- `tests/test_payment_webhooks.py`（Creem/Dodo 真实签名验签单元测试）已登记进 `tests/conftest.py` 执行顺序表第 14 位——**新增测试文件必须登记该顺序表**。
