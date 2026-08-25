"""OAuth 登录 —— GitHub（完整）+ 微信（stub 占位）。

GitHub：authorize → access_token → /user，建/绑 OAuthAccount。
微信：待个体工商户后接微信开放平台，当前抛 NotImplementedError。
"""

import logging

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


class OAuthUserInfo:
    """各 provider 归一化后的用户信息。"""

    def __init__(self, provider: str, provider_uid: str, email: str | None = None,
                 name: str | None = None) -> None:
        self.provider = provider
        self.provider_uid = provider_uid
        self.email = email
        self.name = name


class GitHubOAuth:
    """GitHub OAuth App 流程。"""

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"

    def __init__(self) -> None:
        s = get_settings()
        self.client_id = s.oauth_github_client_id
        self.client_secret = s.oauth_github_client_secret
        self.redirect_base = s.oauth_redirect_base.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str) -> str:
        redirect = f"{self.redirect_base}/auth/oauth/github/callback"
        return (
            f"{self.AUTHORIZE_URL}?client_id={self.client_id}"
            f"&redirect_uri={redirect}&scope=read:user user:email"
            f"&state={state}"
        )

    async def fetch_user(self, code: str) -> OAuthUserInfo:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. code → access_token
            resp = await client.post(
                self.TOKEN_URL,
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            token_data = resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError(f"GitHub 未返回 access_token: {token_data}")

            # 2. access_token → user
            resp = await client.get(
                self.USER_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            u = resp.json()

        return OAuthUserInfo(
            provider="github",
            provider_uid=str(u.get("id")),
            email=u.get("email"),
            name=u.get("login"),
        )


class WeChatOAuth:
    """微信扫码登录 —— stub。

    接微信开放平台需企业/个体户资质 + 网站应用审核。待个体工商户注册后接入，
    届时实现 authorize_url / fetch_user（sns/oauth2/access_token + sns/userinfo）。
    """

    def __init__(self) -> None:
        s = get_settings()
        self.app_id = s.oauth_wechat_app_id
        self.app_secret = s.oauth_wechat_app_secret

    @property
    def enabled(self) -> bool:
        return False  # 永久 stub，直至接入

    def authorize_url(self, state: str) -> str:
        raise NotImplementedError("微信登录待个体工商户后接入微信开放平台")

    async def fetch_user(self, code: str) -> OAuthUserInfo:
        raise NotImplementedError("微信登录待个体工商户后接入微信开放平台")
