"""团队空间用例。

## 设计要点

**1. 角色是「层级」而不是「标签」**
    `owner > admin > editor > viewer`。所有鉴权都折算成「当前角色是否
    达到某个最低层级」，避免每加一个操作就写一条 if。

**2. 防越权是硬要求，不是礼貌**
    - admin **不能**把别人提升为 admin、也不能撤销其他 admin ——
      否则任何一个被邀请的管理员都能把整个团队变成自己的。
    - owner 不能被移除、不能被改角色，只能通过 `delete_team` 结束团队。
    - 不能移除自己（想退出应先转让或删除），避免「一键把自己锁在门外」。

**3. 对非成员统一返回 404**
    团队成员列表本身是敏感信息。对「团队不存在」与「你不是成员」返回
    同一个 404，可以避免用枚举探测哪些团队存在。

**4. 能力矩阵面向未来的资源隔离**
    `permissions()` 返回的是**能力字符串**而不是角色名，未来把文档、
    会话按 team 隔离时，端点只需 `require(team, user, "content:write")`，
    不必到处判断 role 字符串。

分层：本模块属 services 层，可依赖 repositories 与 core。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.repositories import team_repo as repo

logger = get_logger(__name__)

__all__ = [
    "ROLE_RANK",
    "ROLE_CAPABILITIES",
    "TeamService",
    "migrate_legacy_teams",
]

#: 角色层级。数字越大权限越高。
ROLE_RANK: dict[str, int] = {
    "viewer": 1,
    "editor": 2,
    "admin": 3,
    "owner": 4,
}

#: 各角色具备的能力。刻意用「能力」而非「角色」表达，
#: 让未来的资源鉴权不依赖具体角色名。
ROLE_CAPABILITIES: dict[str, set[str]] = {
    "viewer": {"team:read", "content:read", "project:read"},
    "editor": {
        "team:read",
        "content:read",
        "content:write",
        "project:read",
        "project:write",
    },
    "admin": {
        "team:read",
        "team:update",
        "team:manage_members",
        "content:read",
        "content:write",
        "project:read",
        "project:write",
        "project:publish",
        "project:manage_members",
    },
    "owner": {
        "team:read",
        "team:update",
        "team:manage_members",
        "team:delete",
        "team:transfer",
        "content:read",
        "content:write",
        "project:read",
        "project:write",
        "project:publish",
        "project:manage_members",
        "project:delete",
    },
}

#: 可由管理员/所有者授予的角色（owner 只能通过创建/转让产生）
_ASSIGNABLE_ROLES = ("admin", "editor", "viewer")

_NAME_MAX = 80
_DESC_MAX = 500
_USERNAME_MAX = 64


def _rank(role: str) -> int:
    return ROLE_RANK.get(role, 0)


def _resolve_uid(username: str) -> str:
    """username → 不可变 user_id（旧数据/测试会惰性建影子账号）。"""
    from app.services.identity_service import resolve_user_id

    return resolve_user_id(username)


class TeamService:
    def __init__(self, *, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    #  内部工具
    # ------------------------------------------------------------------

    def _require_team(self, team_id: str) -> dict[str, Any]:
        team = repo.get_team(team_id)
        if not team:
            raise NotFoundError("团队不存在或无权访问")
        return team

    def _member_or_404(self, team_id: str, username: str) -> dict[str, Any]:
        member = repo.get_member_by_user_id(team_id, _resolve_uid(username))
        if not member:
            # 兼容尚未回填 user_id 的旧行
            member = repo.get_member(team_id, username)
        if not member:
            # 与「团队不存在」同码，避免枚举探测
            raise NotFoundError("团队不存在或无权访问")
        return member

    def _require_role(
        self, team_id: str, username: str, min_role: str
    ) -> dict[str, Any]:
        """要求当前用户在团队内至少达到 min_role，返回其成员记录。"""
        team = self._require_team(team_id)
        member = self._member_or_404(team_id, username)
        if _rank(member["role"]) < _rank(min_role):
            raise PermissionDeniedError(
                f"需要至少 {min_role} 权限，当前角色为 {member['role']}"
            )
        # 团队对象一并返回，避免调用方再查一次
        member = dict(member)
        member.setdefault("team", team)
        return member

    @staticmethod
    def _clean_name(name: str) -> str:
        clean = (name or "").strip()
        if not clean:
            raise ValidationError("团队名称不能为空")
        if len(clean) > _NAME_MAX:
            raise ValidationError(f"团队名称不能超过 {_NAME_MAX} 个字符")
        return clean

    @staticmethod
    def _clean_description(description: str) -> str:
        clean = (description or "").strip()
        if len(clean) > _DESC_MAX:
            raise ValidationError(f"团队描述不能超过 {_DESC_MAX} 个字符")
        return clean

    @staticmethod
    def _clean_username(username: str) -> str:
        clean = (username or "").strip()
        if not clean:
            raise ValidationError("用户名不能为空")
        if len(clean) > _USERNAME_MAX:
            raise ValidationError(f"用户名不能超过 {_USERNAME_MAX} 个字符")
        return clean

    # ------------------------------------------------------------------
    #  视图
    # ------------------------------------------------------------------

    @staticmethod
    def _out(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "name": item["name"],
            "description": item.get("description") or "",
            "owner": item["owner"],
            "my_role": item.get("my_role") or "",
            "member_count": int(item.get("member_count") or 0),
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }

    @staticmethod
    def _member_out(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": item["username"],
            "user_id": item.get("user_id") or "",
            "role": item["role"],
            "joined_at": item["joined_at"],
        }

    # ------------------------------------------------------------------
    #  团队
    # ------------------------------------------------------------------

    def create_team(
        self, *, name: str, description: str, owner: str
    ) -> dict[str, Any]:
        owner = self._clean_username(owner)
        owner_id = _resolve_uid(owner)
        team = repo.create_team(
            name=self._clean_name(name),
            description=self._clean_description(description),
            owner=owner,
            owner_id=owner_id,
        )
        # 建团队与写入所有者必须成对出现：失败则回滚团队，不留无主团队
        try:
            repo.upsert_member(team["id"], owner, "owner", user_id=owner_id)
        except Exception:
            repo.delete_team(team["id"])
            raise
        logger.info(f"团队已创建: {team['id']} name={team['name']} owner={owner}")
        return self.get_team(team["id"], owner)

    def list_teams(self, username: str) -> list[dict[str, Any]]:
        return [
            self._out(item)
            for item in repo.list_teams_for_user_id(_resolve_uid(username))
        ]

    def get_team(self, team_id: str, username: str) -> dict[str, Any]:
        team = self._require_team(team_id)
        member = self._member_or_404(team_id, username)
        data = self._out({**team, "my_role": member["role"]})
        data["members"] = [
            self._member_out(m) for m in repo.list_members(team_id)
        ]
        return data

    def update_team(
        self,
        team_id: str,
        username: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_role(team_id, username, "admin")
        patch: dict[str, Any] = {}
        if name is not None:
            patch["name"] = self._clean_name(name)
        if description is not None:
            patch["description"] = self._clean_description(description)
        if not patch:
            return self.get_team(team_id, username)
        updated = repo.update_team(team_id, **patch)
        if not updated:
            raise NotFoundError("团队不存在")
        logger.info(f"团队已更新: {team_id} 字段={sorted(patch)}")
        return self.get_team(team_id, username)

    def delete_team(self, team_id: str, username: str) -> dict[str, Any]:
        team = self._require_team(team_id)
        self._require_role(team_id, username, "owner")
        repo.delete_team(team_id)
        logger.info(f"团队已删除: {team_id} name={team['name']}")
        return {"success": True, "id": team_id, "name": team["name"]}

    # ------------------------------------------------------------------
    #  成员
    # ------------------------------------------------------------------

    def list_members(self, team_id: str, username: str) -> list[dict[str, Any]]:
        self._require_role(team_id, username, "viewer")
        return [self._member_out(m) for m in repo.list_members(team_id)]

    def add_member(
        self, team_id: str, actor: str, *, target: str, role: str
    ) -> dict[str, Any]:
        target = self._clean_username(target)
        role = (role or "viewer").strip().lower()
        if role not in _ASSIGNABLE_ROLES:
            raise ValidationError(
                f"角色只能是 {' / '.join(_ASSIGNABLE_ROLES)}；owner 通过创建或转让产生"
            )

        actor_member = self._require_role(team_id, actor, "admin")
        existing = repo.get_member(team_id, target)

        if existing and existing["role"] == "owner":
            raise PermissionDeniedError("不能修改团队所有者的角色")

        # 关键防越权：admin 不能新建/撤销 admin —— 否则等于可以批量提权。
        # 注意「撤销」不仅是移除，还包括**降级**：把同级 admin 改成 editor
        # 同样能让对方失去管理权，必须一并拦住。
        if actor_member["role"] == "admin" and (
            role == "admin" or (existing and existing["role"] == "admin")
        ):
            raise PermissionDeniedError(
                "管理员只能邀请编辑或查看者，不能授予或撤销管理员"
            )

        member = repo.upsert_member(
            team_id, target, role, user_id=_resolve_uid(target)
        )
        action = "已更新" if existing else "已加入"
        logger.info(f"团队成员{action}: team={team_id} {target} role={role} by={actor}")
        return self._member_out(member)

    def remove_member(
        self, team_id: str, actor: str, target: str
    ) -> dict[str, Any]:
        target = self._clean_username(target)
        actor_member = self._require_role(team_id, actor, "admin")
        existing = repo.get_member(team_id, target)
        if not existing:
            raise NotFoundError("该用户不是团队成员")
        if existing["role"] == "owner":
            raise PermissionDeniedError("不能移除团队所有者")
        if target == actor:
            raise PermissionDeniedError("不能移除自己；如需退出请先转让或删除团队")
        if actor_member["role"] == "admin" and existing["role"] == "admin":
            raise PermissionDeniedError("管理员不能移除其他管理员")

        target_id = _resolve_uid(target)
        if not repo.remove_member_by_user_id(team_id, target_id):
            repo.remove_member(team_id, target)
        logger.info(f"团队成员已移除: team={team_id} {target} by={actor}")
        return {"success": True, "team_id": team_id, "username": target}

    # ------------------------------------------------------------------
    #  所有权转让 / 团队邀请
    # ------------------------------------------------------------------

    def transfer_owner(self, team_id: str, actor: str, target: str) -> dict[str, Any]:
        """显式转让：原 owner 降为 admin，目标成员升为 owner。"""
        self._require_role(team_id, actor, "owner")
        target = self._clean_username(target)
        if target == actor:
            raise ValidationError("不能把所有权转让给自己")

        target_member = self._member_or_404(team_id, target)
        actor_member = self._member_or_404(team_id, actor)
        target_id = target_member.get("user_id") or _resolve_uid(target)
        actor_id = actor_member.get("user_id") or _resolve_uid(actor)

        repo.transfer_owner(
            team_id=team_id,
            owner_id=target_id,
            owner_username=target,
            actor_user_id=actor_id,
        )
        logger.info(f"团队所有权已转让: team={team_id} {actor} -> {target}")
        return self.get_team(team_id, actor)

    def create_team_invitation(
        self,
        team_id: str,
        actor: str,
        *,
        role: str = "viewer",
        ttl_hours: int = 72,
    ) -> dict[str, Any]:
        """生成团队邀请链接（登录后加入），需要 admin 及以上。"""
        self._require_role(team_id, actor, "admin")
        role = (role or "viewer").strip().lower()
        if role not in _ASSIGNABLE_ROLES:
            raise ValidationError(
                f"角色只能是 {' / '.join(_ASSIGNABLE_ROLES)}"
            )
        from app.services import identity_service

        return identity_service.create_invitation(
            kind="team",
            team_id=team_id,
            role=role,
            created_by=actor,
            ttl_hours=ttl_hours,
        )

    # ------------------------------------------------------------------
    #  权限
    # ------------------------------------------------------------------

    def permissions(self, team_id: str, username: str) -> dict[str, Any]:
        member = self._require_role(team_id, username, "viewer")
        caps = ROLE_CAPABILITIES.get(member["role"], set())
        return {
            "team_id": team_id,
            "role": member["role"],
            # 排序保证响应稳定，便于前端做 diff 与快照测试
            "permissions": sorted(caps),
        }


def migrate_legacy_teams() -> int:
    """把旧的 username 关联回填成 user_id（幂等）。

    只需在启动时跑一次：旧库的 teams.owner / team_members.username
    没有 user_id，权限校验会退化到按 username 查，虽然能用但无法改名。
    """
    from app.services.identity_service import resolve_user_id

    updated = 0
    for team in repo.list_all_teams():
        if not team.get("owner_id"):
            repo.set_team_owner_id(team["id"], resolve_user_id(team["owner"]))
            updated += 1
    for member in repo.list_all_members():
        if not member.get("user_id"):
            repo.set_member_user_id(
                member["team_id"],
                member["username"],
                resolve_user_id(member["username"]),
            )
            updated += 1
    return updated
