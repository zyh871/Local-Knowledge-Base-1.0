"""社区用例：项目展示、贡献者、互动。

权限模型：
  * 团队能力（project:read/write/publish/manage_members/delete）决定空间级操作
  * 项目内 maintainer 可编辑/发布/管理贡献者；contributor 只读+评论
  * 草稿仅团队成员可见；发布后实例内所有登录用户可见
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.repositories import community_repo as repo
from app.services import identity_service
from app.services.team_service import TeamService

logger = get_logger(__name__)

__all__ = ["CommunityService"]

_PROJECT_ROLES = ("maintainer", "contributor")


def _uid(username: str) -> str:
    return identity_service.resolve_user_id(username)


def _team_permissions(team_id: str, username: str) -> Optional[set[str]]:
    try:
        data = TeamService(settings=None).permissions(team_id, username)
    except NotFoundError:
        return None
    return set(data["permissions"])


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not base:
        base = f"project-{uuid.uuid4().hex[:8]}"
    return base[:60]


class CommunityService:
    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    #  内部校验
    # ------------------------------------------------------------------

    @staticmethod
    def _team_caps(team_id: str, username: str) -> set[str]:
        caps = _team_permissions(team_id, username)
        if caps is None:
            raise NotFoundError("团队不存在或无权访问")
        return caps

    def _project_or_404(self, project_id: str) -> dict[str, Any]:
        project = repo.get_project(project_id)
        if not project:
            raise NotFoundError("项目不存在")
        return project

    def _is_team_member(self, team_id: str, username: str) -> bool:
        return _team_permissions(team_id, username) is not None

    def _can_read(self, project: dict[str, Any], username: str) -> bool:
        if project["visibility"] == "published":
            return True
        if self._is_team_member(project["team_id"], username):
            return True
        # 非成员统一 404，避免枚举草稿
        raise NotFoundError("项目不存在")

    def _project_role(self, project_id: str, username: str) -> str:
        member = repo.get_member(project_id, _uid(username))
        return member["role"] if member else ""

    def _can_manage(self, project: dict[str, Any], username: str) -> bool:
        caps = _team_permissions(project["team_id"], username) or set()
        if "project:write" in caps:
            return True
        return self._project_role(project["id"], username) == "maintainer"

    def _can_publish(self, project: dict[str, Any], username: str) -> bool:
        caps = _team_permissions(project["team_id"], username) or set()
        if "project:publish" in caps:
            return True
        return self._project_role(project["id"], username) == "maintainer"

    def _can_manage_members(self, project: dict[str, Any], username: str) -> bool:
        caps = _team_permissions(project["team_id"], username) or set()
        if "project:manage_members" in caps:
            return True
        return self._project_role(project["id"], username) == "maintainer"

    # ------------------------------------------------------------------
    #  视图
    # ------------------------------------------------------------------

    @staticmethod
    def _decorate(project: dict[str, Any], username: str) -> dict[str, Any]:
        uid = _uid(username)
        data = dict(project)
        published = project["visibility"] == "published"
        data["is_starred"] = bool(published and repo.has_star(project["id"], uid))
        data["is_watching"] = bool(published and repo.has_watch(project["id"], uid))
        member = repo.get_member(project["id"], uid)
        data["my_project_role"] = member["role"] if member else ""
        return data

    # ------------------------------------------------------------------
    #  项目
    # ------------------------------------------------------------------

    def list_projects(
        self,
        username: str,
        *,
        q: str = "",
        language: str = "",
        tag: str = "",
        sort: str = "recent",
        limit: int = 50,
        offset: int = 0,
        mine: bool = False,
    ) -> list[dict[str, Any]]:
        if mine:
            # 草稿属于「自己的工作台」，不参与搜索/排序筛选
            items = repo.list_projects_for_user(
                _uid(username), limit=limit, offset=offset
            )
        else:
            items = repo.list_projects(
                q=q,
                language=language,
                tag=tag,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        return [self._decorate(item, username) for item in items]

    def get_project(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        return self._decorate(project, username)

    def create_project(
        self,
        team_id: str,
        actor: str,
        *,
        name: str,
        summary: str = "",
        description: str = "",
        language: str = "",
        license: str = "",
        repo_url: str = "",
        homepage: str = "",
        tags: Optional[list[str]] = None,
        slug: str = "",
    ) -> dict[str, Any]:
        caps = self._team_caps(team_id, actor)
        if "project:write" not in caps:
            raise PermissionDeniedError("需要团队编辑权限才能创建项目")

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationError("项目名称不能为空")
        if len(clean_name) > 80:
            raise ValidationError("项目名称不能超过 80 个字符")
        for label, value in (("仓库地址", repo_url), ("主页", homepage)):
            if value and not re.match(r"^https?://", value):
                raise ValidationError(f"{label}必须以 http:// 或 https:// 开头")
        if len(tags or []) > 8:
            raise ValidationError("标签最多 8 个")

        base_slug = (slug or "").strip() or _slugify(clean_name)
        candidate = base_slug
        index = 2
        while repo.get_project_by_slug(team_id, candidate):
            candidate = f"{base_slug}-{index}"
            index += 1

        project = repo.create_project(
            team_id=team_id,
            slug=candidate,
            name=clean_name,
            summary=(summary or "").strip()[:300],
            description=description or "",
            language=(language or "").strip()[:40],
            license=(license or "").strip()[:40],
            repo_url=(repo_url or "").strip(),
            homepage=(homepage or "").strip(),
            tags=[t.strip() for t in (tags or []) if t.strip()],
            created_by=actor,
            created_by_id=_uid(actor),
        )
        # 创建者自动成为 maintainer，避免"建完项目却无权发布"
        repo.upsert_member(
            project["id"],
            user_id=_uid(actor),
            username=actor,
            role="maintainer",
        )
        logger.info(f"社区项目已创建: {project['id']} team={team_id} by={actor}")
        return self._decorate(project, actor)

    def update_project(
        self, project_id: str, username: str, **fields: Any
    ) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        if not self._can_manage(project, username):
            raise PermissionDeniedError("需要项目维护者或团队编辑权限")

        patch: dict[str, Any] = {}
        if "name" in fields and fields["name"] is not None:
            name = str(fields["name"]).strip()
            if not name:
                raise ValidationError("项目名称不能为空")
            patch["name"] = name
        for key in ("summary", "description", "language", "license", "homepage"):
            if key in fields and fields[key] is not None:
                patch[key] = str(fields[key])
        if "repo_url" in fields and fields["repo_url"] is not None:
            url = str(fields["repo_url"]).strip()
            if url and not re.match(r"^https?://", url):
                raise ValidationError("仓库地址必须以 http:// 或 https:// 开头")
            patch["repo_url"] = url
        if "tags" in fields and fields["tags"] is not None:
            tags = [str(t).strip() for t in fields["tags"] if str(t).strip()]
            if len(tags) > 8:
                raise ValidationError("标签最多 8 个")
            patch["tags"] = tags

        updated = repo.update_project(project_id, **patch)
        if not updated:
            raise NotFoundError("项目不存在")
        return self._decorate(updated, username)

    def delete_project(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        caps = _team_permissions(project["team_id"], username) or set()
        is_creator = project["created_by_id"] == _uid(username)
        if "project:delete" not in caps and not is_creator:
            raise PermissionDeniedError("只有团队所有者或项目创建者可以删除")
        repo.delete_project(project_id)
        logger.info(f"社区项目已删除: {project_id} by={username}")
        return {"success": True, "id": project_id}

    def publish_project(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        if not self._can_publish(project, username):
            raise PermissionDeniedError("需要发布权限")
        updated = repo.publish_project(project_id)
        logger.info(f"社区项目已发布: {project_id} by={username}")
        return self._decorate(updated, username)  # type: ignore[arg-type]

    def unpublish_project(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        if not self._can_publish(project, username):
            raise PermissionDeniedError("需要发布权限")
        updated = repo.unpublish_project(project_id)
        return self._decorate(updated, username)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    #  贡献者
    # ------------------------------------------------------------------

    def list_contributors(self, project_id: str, username: str) -> list[dict[str, Any]]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        return repo.list_members(project_id)

    def add_contributor(
        self, project_id: str, actor: str, *, target: str, role: str = "contributor"
    ) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        if not self._can_manage_members(project, actor):
            raise PermissionDeniedError("需要项目维护者或团队管理权限")
        role = (role or "contributor").strip().lower()
        if role not in _PROJECT_ROLES:
            raise ValidationError("项目角色只能是 maintainer 或 contributor")
        target = (target or "").strip()
        if not target:
            raise ValidationError("用户名不能为空")
        if not self._is_team_member(project["team_id"], target):
            raise ValidationError("只能添加团队成员为贡献者")
        return repo.upsert_member(
            project_id,
            user_id=_uid(target),
            username=target,
            role=role,
        )

    def remove_contributor(
        self, project_id: str, actor: str, target: str
    ) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        if not self._can_manage_members(project, actor):
            raise PermissionDeniedError("需要项目维护者或团队管理权限")
        target_id = _uid(target)
        if target_id == project["created_by_id"]:
            raise PermissionDeniedError("不能移除项目创建者")
        if not repo.remove_member(project_id, target_id):
            raise NotFoundError("该用户不是项目贡献者")
        return {"success": True, "project_id": project_id, "username": target}

    # ------------------------------------------------------------------
    #  Star / Watch
    # ------------------------------------------------------------------

    def star(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        if project["visibility"] != "published":
            raise PermissionDeniedError("草稿项目不能被 star")
        repo.add_star(project_id, _uid(username))
        return self._decorate(self._project_or_404(project_id), username)

    def unstar(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        repo.remove_star(project_id, _uid(username))
        return self._decorate(self._project_or_404(project_id), username)

    def watch(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        repo.add_watch(project_id, _uid(username))
        return self._decorate(project, username)

    def unwatch(self, project_id: str, username: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        repo.remove_watch(project_id, _uid(username))
        return self._decorate(project, username)

    # ------------------------------------------------------------------
    #  评论
    # ------------------------------------------------------------------

    def list_comments(self, project_id: str, username: str) -> list[dict[str, Any]]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        return repo.list_comments(project_id)

    def add_comment(self, project_id: str, username: str, body: str) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        self._can_read(project, username)
        text = (body or "").strip()
        if not text:
            raise ValidationError("评论不能为空")
        if len(text) > 1000:
            raise ValidationError("评论不能超过 1000 个字符")
        return repo.create_comment(
            project_id, user_id=_uid(username), username=username, body=text
        )

    def delete_comment(
        self, project_id: str, comment_id: str, username: str
    ) -> dict[str, Any]:
        project = self._project_or_404(project_id)
        comment = repo.get_comment(comment_id)
        if not comment or comment["project_id"] != project_id:
            raise NotFoundError("评论不存在")
        is_author = comment["user_id"] == _uid(username)
        if not is_author and not self._can_manage_members(project, username):
            raise PermissionDeniedError("只能删除自己的评论")
        repo.delete_comment(comment_id)
        return {"success": True, "id": comment_id}

    # ------------------------------------------------------------------
    #  用户主页 / 关注
    # ------------------------------------------------------------------

    def profile(self, target: str, viewer: str) -> dict[str, Any]:
        rec = identity_service.find_by_username(target)
        if not rec:
            raise NotFoundError("用户不存在")
        target_id = rec["id"]
        viewer_id = _uid(viewer)
        projects = repo.list_projects_by_creator(target_id)
        return {
            "id": target_id,
            "username": rec["username"],
            "display_name": rec.get("display_name") or rec["username"],
            "projects": projects,
            "followers": repo.count_followers(target_id),
            "following": repo.count_following(target_id),
            "is_following": (
                False
                if viewer_id == target_id
                else repo.is_following(viewer_id, target_id)
            ),
        }

    def follow(self, target: str, username: str) -> dict[str, Any]:
        rec = identity_service.find_by_username(target)
        if not rec:
            raise NotFoundError("用户不存在")
        if rec["id"] == _uid(username):
            raise ValidationError("不能关注自己")
        repo.follow(_uid(username), rec["id"])
        return {
            "success": True,
            "username": rec["username"],
            "followers": repo.count_followers(rec["id"]),
        }

    def unfollow(self, target: str, username: str) -> dict[str, Any]:
        rec = identity_service.find_by_username(target)
        if not rec:
            raise NotFoundError("用户不存在")
        repo.unfollow(_uid(username), rec["id"])
        return {
            "success": True,
            "username": rec["username"],
            "followers": repo.count_followers(rec["id"]),
        }
