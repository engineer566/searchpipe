"""应用配置 —— 从 .env 读取，pydantic-settings 管理。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- 检索源：SearXNG ---
    searxng_url: str = "http://localhost:8081"
    searxng_proxy: str | None = None

    # --- LLM（OpenAI 兼容）---
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = "sk-your-key-here"
    llm_model: str = "deepseek-chat"

    # --- 搜索行为 ---
    max_results: int = 5
    fetch_top_n: int = 5
    request_timeout: int = 20      # SearXNG 检索超时（秒）
    fetch_timeout: int = 12        # 单页正文抓取超时（秒），收紧以压低抓取阶段长尾
    fetch_concurrency: int = 8     # 正文抓取并发数
    llm_timeout: int = 30

    # --- 缓存 ---
    redis_url: str = "redis://localhost:6379/0"
    enable_cache: bool = True      # 相同 query+参数的结果缓存（Redis 不可用时自动降级为不缓存）
    search_cache_ttl: int = 300    # 结果缓存 TTL（秒）

    # --- 商业化后端：数据库 ---
    database_url: str = "postgresql+asyncpg://ai:ai@localhost:5432/ai"
    database_echo: bool = False

    # --- 商业化后端：JWT / 会话 ---
    jwt_secret: str = "change-me"
    jwt_algo: str = "HS256"
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 30
    session_cookie_secret: str = "change-me-too"

    # --- API Key 明文可查看（api_keys.key_cipher 的对称加密主密钥）---
    # 留空则回退 session_cookie_secret 派生。生产建议显式配置并妥善备份：
    # 换掉该密钥后历史 Key 仍可正常鉴权（鉴权走 argon2 hash），但无法再看明文。
    key_encryption_secret: str = ""

    # --- 商业化后端：积分计费 ---
    free_tier_credits: int = 1000  # 内测免费额度/月
    credit_cost_basic: int = 1     # basic 搜索扣 1
    credit_cost_advanced: int = 2  # advanced 搜索扣 2

    # --- 商业化后端：OAuth ---
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_redirect_base: str = "http://localhost:8001"  # 回调基址

    # --- 商业化后端：支付（MoR 代收：Creem / Dodo Payments）---
    # 两家都是「托管收银台 redirect + JSON webhook 签名验证」模式，配置切换即可。
    # 国内版虎皮椒实现已在 archive/china-2026-09 tag 封存。
    payment_provider: str = "creem"      # creem | dodo
    creem_api_key: str = ""
    creem_webhook_secret: str = ""
    # test 模式用 https://test-api.creem.io
    creem_api_base: str = "https://api.creem.io"
    # 自定义金额充值用的「$1/单位」按量 product id（units=美元数）
    creem_credit_product_id: str = ""
    dodo_api_key: str = ""
    dodo_webhook_secret: str = ""
    # test 模式用 https://test.dodopayments.com
    dodo_api_base: str = "https://live.dodopayments.com"
    dodo_credit_product_id: str = ""

    # --- 商业化后端：充值/订阅规则 ---
    currency: str = "USD"                # 全站结算货币
    credit_price_rate: str = "0.005"     # $0.005 = 1 积分（自定义充值汇率）
    max_recharge_usd: int = 500          # 单笔充值上限（美元）

    # --- 商业化后端：内容审核（合规）---
    moderation_provider: str = "aliyun"
    moderation_access_key: str = ""
    moderation_secret_key: str = ""
    moderation_enabled: bool = False  # 内测先关，配好 key 再开

    # --- 商业化后端：限流 ---
    rate_limit_rpm: int = 100        # 每 key 每分钟
    rate_limit_burst: int = 20

    # --- 邮件（密码重置等事务邮件）---
    # smtp_host 为空表示未配置：mailer 降级为日志输出，不阻断流程。
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""              # 留空则用 smtp_user
    smtp_use_tls: bool = True        # 465 走 SSL；587 需 STARTTLS 时置 false 另配
    app_base_url: str = "http://localhost:8001"  # 对外基址，用于拼密码重置链接

    # --- SEO：站长平台验证（留空则不渲染对应 meta 标签）---
    google_site_verification: str = ""   # Google Search Console 的 HTML 标记值（content 内容）
    bing_site_verification: str = ""     # Bing Webmaster Tools 的 msvalidate.01

    # --- MCP server（第二协议入口）---
    # True：MCP tool 必须带 sp- API Key、走计费扣积分（对齐 Tavily，方案 A）。
    # False：本地 dev 旁路，裸调 run_search 不扣费（便于无 key 测试）。
    mcp_require_api_key: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
