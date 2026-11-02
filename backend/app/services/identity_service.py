"""身份用例：账号、邀请、旧数据解析。

设计要点：
  * **user_id 不可变**，username 仅登录名；旧数据（团队 owner/member 存 username）
    通过 `resolve_user_id()` 惰性映射，避免一次性大迁移阻塞启动。
  * 邀请令牌只存 sha256 哈希；明文仅在创建响应里返回一次。
  * 账号创建权收口在部署管理员；团队 owner 只能邀请已存在账号。
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any, Optional

from app.core.config import get_security_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.repositories import user_repo as repo

logger = get_logger(__name__)

__all__ = [
    "accept_invitation",
    "bootstrap_identity",
    "create_invitation",
    "create_user",
    "get_user_record",
    "list_invitations",
    "list_users",
    "resolve_user_id",
    "update_user",
]

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_GLOBAL_ROLES = ("admin", "user")
_INVITE_KINDS = ("account", "team")
_PASSWORD_MIN = 8
_TTL_HOURS_MAX = 168


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_username(username: str) -> str:
    clean = (username or "").strip()
    if not _USERNAME_RE.match(clean):
        raise ValidationError("用户名需为 3-32 位字母、数字、下划线、点或短横线")
    return clean


def _clean_password(password: str) -> str:
    if len(password or "") < _PASSWORD_MIN:
        raise ValidationError(f"密码至少 {_PASSWORD_MIN} 位")
    return password


# ---------------------------------------------------------------------------
#  启动引导 / 查询
# ---------------------------------------------------------------------------


def bootstrap_identity() -> dict[str, Any]:
    """确保 env 管理员在 users 表里存在（幂等）。"""
    cfg = get_security_settings()
    username = cfg.admin_username
    existing = repo.get_by_username(username)
    if existing:
        # 环境变量改了用户名时，把 usr_admin 这个固定 id 的用户改名过去
        legacy = repo.get_by_id("usr_admin")
        if legacy and legacy["username"] != username:
            try:
                repo.update_user(
                    "usr_admin",
                    display_name=legacy.get("display_name") or "管理员",
                )
            except Exception:  # noqa: BLE001
                pass
        return existing

    user = repo.create_user(
        user_id="usr_admin",
        username=username,
        password_hash=hash_password(cfg.admin_password),
        display_name="管理员",
        global_role="admin",
    )
    logger.info(f"已初始化部署管理员: {username} (usr_admin)")
    return user


def get_user_record(identifier: str) -> Optional[dict[str, Any]]:
    """供 core.security 的 provider 调用：按 id 或 username 查。

    仓库层字段是 `password_hash`，认证模型要的是 `hashed_password`，
    在这里做一次显式映射，避免把仓储细节泄漏到 core。
    """
    rec = repo.get_by_id(identifier)
    if rec is None:
        rec = repo.get_by_username(identifier)
    if rec is None:
        return None
    return {
        "id": rec["id"],
        "username": rec["username"],
        "hashed_password": rec["password_hash"],
        "display_name": rec.get("display_name") or rec["username"],
        "global_role": rec.get("global_role") or "user",
        "disabled": bool(rec.get("disabled")),
    }


def resolve_user_id(username: str) -> str:
    """username → user_id；不存在时创建禁用状态的影子账号。

    影子账号用于兼容旧团队数据与测试，不能登录（status=disabled）。
    """
    rec = repo.get_by_username(username)
    if rec:
        return rec["id"]
    try:
        shadow = repo.create_user(
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            display_name=username,
            global_role="user",
            status="disabled",
        )
        return shadow["id"]
    except Exception:  # noqa: BLE001 - 并发下可能已被他人创建
        rec = repo.get_by_username(username)
        if rec:
            return rec["id"]
        raise


def public_user(rec: dict[str, Any]) -> dict[str, Any]:
    """去掉密码哈希，供 API 返回。"""
    return {
        "id": rec["id"],
        "username": rec["username"],
        "display_name": rec.get("display_name") or rec["username"],
        "global_role": rec.get("global_role") or "user",
        "status": rec.get("status") or "active",
        "created_at": rec.get("created_at") or 0.0,
    }


# ---------------------------------------------------------------------------
#  账号
# ---------------------------------------------------------------------------


def create_user(
    *,
    username: str,
    password: str,
    display_name: str = "",
    global_role: str = "user",
) -> dict[str, Any]:
    username = _clean_username(username)
    _clean_password(password)
    if global_role not in _GLOBAL_ROLES:
        raise ValidationError("global_role 只能是 admin 或 user")
    if repo.get_by_username(username):
        raise ConflictError("用户名已存在")
    try:
        rec = repo.create_user(
            username=username,
            password_hash=hash_password(password),
            display_name=(display_name or "").strip() or username,
            global_role=global_role,
        )
    except Exception as exc:  # noqa: BLE001 - UNIQUE 竞态
        raise ConflictError("用户名已存在") from exc
    logger.info(f"账号已创建: {username} role={global_role}")
    return public_user(rec)


def list_users() -> list[dict[str, Any]]:
    return [public_user(r) for r in repo.list_users()]


def update_user(
    user_id: str,
    *,
    display_name: Optional[str] = None,
    global_role: Optional[str] = None,
    status: Optional[str] = None,
    password: Optional[str] = None,
) -> dict[str, Any]:
    rec = repo.get_by_id(user_id)
    if not rec:
        raise NotFoundError("用户不存在")
    patch: dict[str, Any] = {}
    if display_name is not None:
        patch["display_name"] = display_name.strip() or rec["username"]
    if global_role is not None:
        if global_role not in _GLOBAL_ROLES:
            raise ValidationError("global_role 只能是 admin 或 user")
        patch["global_role"] = global_role
    if status is not None:
        if status not in ("active", "disabled"):
            raise ValidationError("status 只能是 active 或 disabled")
        if user_id == "usr_admin" and status == "disabled":
            raise ValidationError("不能禁用部署管理员")
        patch["status"] = status
    if password is not None:
        patch["password_hash"] = hash_password(_clean_password(password))

    updated = repo.update_user(user_id, **patch)
    if not updated:
        raise NotFoundError("用户不存在")
    return public_user(updated)


# ---------------------------------------------------------------------------
#  邀请
# ---------------------------------------------------------------------------


def create_invitation(
    *,
    kind: str,
    created_by: str,
    team_id: Optional[str] = None,
    role: str = "",
    ttl_hours: int = 72,
) -> dict[str, Any]:
    if kind not in _INVITE_KINDS:
        raise ValidationError("邀请类型只能是 account 或 team")
    if kind == "team" and not team_id:
        raise ValidationError("团队邀请必须指定 team_id")
    ttl = max(1, min(int(ttl_hours or 72), _TTL_HOURS_MAX))

    token = secrets.token_urlsafe(32)
    inv = repo.create_invitation(
        token_hash=_hash_token(token),
        kind=kind,
        team_id=team_id,
        role=(role or "viewer").strip().lower(),
        created_by=created_by,
        expires_at=time.time() + ttl * 3600,
    )
    logger.info(f"邀请已创建: kind={kind} team={team_id} by={created_by}")
    return {
        **inv,
        "token": token,
        "invite_path": f"/invite/{token}",
    }


def list_invitations(limit: int = 100) -> list[dict[str, Any]]:
    return repo.list_invitations(limit)


def accept_invitation(
    *, token: str, username: str, password: str, display_name: str = ""
) -> dict[str, Any]:
    """接受邀请：创建账号（或复用已有账号）并标记令牌已用。"""
    token_hash = _hash_token(token or "")
    inv = repo.get_invitation_by_hash(token_hash)
    if not inv:
        raise NotFoundError("邀请不存在或已失效")
    if inv["used_at"]:
        raise ConflictError("邀请已被使用")
    if inv["expires_at"] < time.time():
        raise ConflictError("邀请已过期")

    username = _clean_username(username)
    existing = repo.get_by_username(username)
    if existing:
        # 已有账号：必须提供正确密码，避免用邀请令牌接管他人账号
        if not verify_password(password, existing["password_hash"]):
            raise ValidationError("用户名已存在，请使用原密码")
        rec = existing
    else:
        _clean_password(password)
        rec = repo.create_user(
            username=username,
            password_hash=hash_password(password),
            display_name=(display_name or "").strip() or username,
            global_role="user",
        )

    if not repo.mark_invitation_used(inv["id"], rec["id"]):
        raise ConflictError("邀请已被使用")

    logger.info(f"邀请已接受: kind={inv['kind']} user={username}")
    return {
        "user": public_user(rec),
        "invitation": inv,
    }


# 供 API 层按用户名查（团队邀请）
def find_by_username(username: str) -> Optional[dict[str, Any]]:
    return repo.get_by_username(username)
