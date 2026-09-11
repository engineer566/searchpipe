"""OAuth 登录 —— GitHub + Google。

GitHub：authorize → access_token → /user，建/绑 OAuthAccount。
Google：authorize → oauth2 token → openid userinfo，建/绑 OAuthAccount。
国内版微信 stub 已随 archive/china-2026-09 封存删除。
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


class GoogleOAuth:
    """Google OAuth（OIDC）流程。"""

    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def __init__(self) -> None:
        s = get_settings()
        self.client_id = s.oauth_google_client_id
        self.client_secret = s.oauth_google_client_secret
        self.redirect_base = s.oauth_redirect_base.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str) -> str:
        redirect = f"{self.redirect_base}/auth/oauth/google/callback"
        return (
            f"{self.AUTHORIZE_URL}?client_id={self.client_id}"
            f"&redirect_uri={redirect}&response_type=code"
            f"&scope=openid%20email%20profile&state={state}"
        )

    async def fetch_user(self, code: str) -> OAuthUserInfo:
        redirect = f"{self.redirect_base}/auth/oauth/google/callback"
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. code → access_token
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError(f"Google 未返回 access_token: {token_data}")

            # 2. access_token → userinfo
            resp = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            u = resp.json()

        return OAuthUserInfo(
            provider="google",
            provider_uid=str(u.get("sub")),
            email=u.get("email"),
            name=u.get("name"),
        )
