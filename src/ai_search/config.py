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

    # --- 商业化后端：积分计费 ---
    free_tier_credits: int = 1000  # 内测免费额度/月
    credit_cost_basic: int = 1     # basic 搜索扣 1
    credit_cost_advanced: int = 2  # advanced 搜索扣 2

    # --- 商业化后端：OAuth ---
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_wechat_app_id: str = ""
    oauth_wechat_app_secret: str = ""
    oauth_redirect_base: str = "http://localhost:8001"  # 回调基址

    # --- 商业化后端：支付（虎皮椒过渡）---
    # 虎皮椒按渠道各建一个应用：支付宝、微信各一套 appid/appsecret。
    # 渠道专属配置留空时回退到通用 XUNHUPAY_APPID/APPSECRET（单渠道兼容）。
    payment_provider: str = "xunhupay"
    xunhupay_appid: str = ""
    xunhupay_appsecret: str = ""
    xunhupay_appid_alipay: str = ""
    xunhupay_appsecret_alipay: str = ""
    xunhupay_appid_wechat: str = ""
    xunhupay_appsecret_wechat: str = ""
    xunhupay_notify_url: str = ""

    # --- 商业化后端：充值/订阅规则 ---
    credit_yuan_rate: str = "0.03"   # ¥0.03 = 1 积分（自定义充值汇率）
    max_recharge_yuan: int = 100     # 单笔充值上限

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

    # --- MCP server（第二协议入口）---
    # True：MCP tool 必须带 sp- API Key、走计费扣积分（对齐 Tavily，方案 A）。
    # False：本地 dev 旁路，裸调 run_search 不扣费（便于无 key 测试）。
    mcp_require_api_key: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
