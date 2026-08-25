"""密码哈希 —— argon2（passlib）。

存储用 argon2，内存中明文密码只存在请求生命周期内。
"""

from passlib.context import CryptContext

# argon2 优先，bcrypt 兜底（兼容历史数据迁移场景）
_pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return _pwd_context.verify(plain, hashed)
