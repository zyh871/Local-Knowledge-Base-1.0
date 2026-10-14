"""
认证与密码工具（JWT + bcrypt）。

与旧实现的差异：
  * 用户存储从 `main.py` 的模块级 `fake_users_db` 收敛到这里
  * bcrypt 哈希改为**延迟计算**（首次校验时），避免每次进程启动都做一次昂贵的
    哈希（旧实现在 import 期就 `hashpw`，增加了启动耗时）
  * 密钥来源统一走 `Settings.security`，不再直接读 `os.getenv`
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import SecuritySettings, get_security_settings
from app.core.exceptions import InvalidTokenError, UnauthorizedError


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


class User(BaseModel):
    username: str
    disabled: Optional[bool] = None


class UserInDB(User):
    hashed_password: str


# ---------------------------------------------------------------------------
#  用户存储（单管理员；后续接入真实用户表时替换本模块）
# ---------------------------------------------------------------------------

_admin_hash_cache: Optional[str] = None


def _admin_hashed_password(cfg: SecuritySettings) -> str:
    global _admin_hash_cache
    if _admin_hash_cache is None:
        _admin_hash_cache = bcrypt.hashpw(
            cfg.admin_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
    return _admin_hash_cache


def get_users_db() -> dict[str, dict]:
    """返回用户表快照（bcrypt 哈希延迟生成）。"""
    cfg = get_security_settings()
    return {
        cfg.admin_username: {
            "username": cfg.admin_username,
            "hashed_password": _admin_hashed_password(cfg),
            "disabled": False,
        }
    }


def verify_password(plain_password: str, hashed_password: str | bytes) -> bool:
    pw_bytes = plain_password.encode("utf-8")
    hp_bytes = (
        hashed_password.encode("utf-8")
        if isinstance(hashed_password, str)
        else hashed_password
    )
    try:
        return bcrypt.checkpw(pw_bytes, hp_bytes)
    except (ValueError, TypeError):
        return False


def get_user(username: str) -> Optional[UserInDB]:
    record = get_users_db().get(username)
    return UserInDB(**record) if record else None


def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    user = get_user(username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


# ---------------------------------------------------------------------------
#  JWT
# ---------------------------------------------------------------------------


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    cfg: Optional[SecuritySettings] = None,
) -> str:
    cfg = cfg or get_security_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=cfg.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


def decode_access_token(token: str, cfg: Optional[SecuritySettings] = None) -> dict:
    cfg = cfg or get_security_settings()
    try:
        return jwt.decode(token, cfg.jwt_secret_key, algorithms=[cfg.jwt_algorithm])
    except JWTError as exc:
        raise InvalidTokenError("令牌无效或已过期") from exc


def token_remaining_seconds(payload: dict) -> int:
    """令牌剩余有效秒数，用于 JWT 黑名单 TTL。"""
    exp = payload.get("exp")
    if not exp:
        return 0
    try:
        remaining = int(exp) - int(datetime.now(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0
    return max(remaining, 0)


def require_active(user: UserInDB) -> UserInDB:
    if user.disabled:
        raise UnauthorizedError("用户已被禁用")
    return user
