"""社区路由：项目展示、贡献者、star/watch/评论/关注。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import CommunityServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.community import (
    CommentCreate,
    CommentOut,
    ContributorAdd,
    ContributorOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    UserProfileOut,
)

router = APIRouter()


@router.get(
    "/community/projects",
    response_model=list[ProjectOut],
    summary="社区项目列表（公开只含已发布；mine=1 含我的草稿）",
)
async def list_projects(
    current_user: CurrentUser,
    service: CommunityServiceDep,
    q: str = Query("", max_length=100),
    language: str = Query("", max_length=40),
    tag: str = Query("", max_length=40),
    sort: str = Query("recent", pattern="^(recent|stars|name)$"),
    mine: bool = Query(False, description="只看我创建/贡献的项目（含草稿）"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: None = Depends(rate_limit("community")),
) -> list[ProjectOut]:
    return [
        ProjectOut(**item)
        for item in service.list_projects(
            current_user.username,
            q=q,
            language=language,
            tag=tag,
            sort=sort,
            limit=limit,
            offset=offset,
            mine=mine,
        )
    ]


@router.post(
    "/community/projects", response_model=ProjectOut, summary="创建项目（默认草稿）"
)
async def create_project(
    payload: ProjectCreate,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(
        **service.create_project(
            payload.team_id,
            current_user.username,
            name=payload.name,
            summary=payload.summary,
            description=payload.description,
            language=payload.language,
            license=payload.license,
            repo_url=payload.repo_url,
            homepage=payload.homepage,
            tags=payload.tags,
            slug=payload.slug,
        )
    )


@router.get(
    "/community/projects/{project_id}",
    response_model=ProjectOut,
    summary="项目详情（草稿仅团队成员可见）",
)
async def get_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(**service.get_project(project_id, current_user.username))


@router.patch(
    "/community/projects/{project_id}",
    response_model=ProjectOut,
    summary="编辑项目",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(
        **service.update_project(
            project_id,
            current_user.username,
            **payload.model_dump(exclude_unset=True),
        )
    )


@router.delete("/community/projects/{project_id}", summary="删除项目")
async def delete_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> dict:
    return service.delete_project(project_id, current_user.username)


@router.post(
    "/community/projects/{project_id}/publish",
    response_model=ProjectOut,
    summary="发布到社区",
)
async def publish_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(
        **service.publish_project(project_id, current_user.username)
    )


@router.post(
    "/community/projects/{project_id}/unpublish",
    response_model=ProjectOut,
    summary="撤回为草稿",
)
async def unpublish_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(
        **service.unpublish_project(project_id, current_user.username)
    )


@router.get(
    "/community/projects/{project_id}/contributors",
    response_model=list[ContributorOut],
    summary="贡献者列表",
)
async def list_contributors(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> list[ContributorOut]:
    return [
        ContributorOut(**item)
        for item in service.list_contributors(project_id, current_user.username)
    ]


@router.post(
    "/community/projects/{project_id}/contributors",
    response_model=ContributorOut,
    summary="添加贡献者",
)
async def add_contributor(
    project_id: str,
    payload: ContributorAdd,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ContributorOut:
    return ContributorOut(
        **service.add_contributor(
            project_id,
            current_user.username,
            target=payload.username,
            role=payload.role,
        )
    )


@router.delete(
    "/community/projects/{project_id}/contributors/{username}",
    summary="移除贡献者",
)
async def remove_contributor(
    project_id: str,
    username: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> dict:
    return service.remove_contributor(
        project_id, current_user.username, username
    )


@router.post(
    "/community/projects/{project_id}/star",
    response_model=ProjectOut,
    summary="Star 项目（幂等）",
)
async def star_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(**service.star(project_id, current_user.username))


@router.delete(
    "/community/projects/{project_id}/star",
    response_model=ProjectOut,
    summary="取消 Star",
)
async def unstar_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(**service.unstar(project_id, current_user.username))


@router.post(
    "/community/projects/{project_id}/watch",
    response_model=ProjectOut,
    summary="关注项目（幂等）",
)
async def watch_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(**service.watch(project_id, current_user.username))


@router.delete(
    "/community/projects/{project_id}/watch",
    response_model=ProjectOut,
    summary="取消关注项目",
)
async def unwatch_project(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> ProjectOut:
    return ProjectOut(**service.unwatch(project_id, current_user.username))


@router.get(
    "/community/projects/{project_id}/comments",
    response_model=list[CommentOut],
    summary="评论列表",
)
async def list_comments(
    project_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> list[CommentOut]:
    return [
        CommentOut(**item)
        for item in service.list_comments(project_id, current_user.username)
    ]


@router.post(
    "/community/projects/{project_id}/comments",
    response_model=CommentOut,
    summary="发表评论",
)
async def add_comment(
    project_id: str,
    payload: CommentCreate,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> CommentOut:
    return CommentOut(
        **service.add_comment(
            project_id, current_user.username, payload.body
        )
    )


@router.delete(
    "/community/projects/{project_id}/comments/{comment_id}",
    summary="删除评论（作者或维护者）",
)
async def delete_comment(
    project_id: str,
    comment_id: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> dict:
    return service.delete_comment(
        project_id, comment_id, current_user.username
    )


@router.get(
    "/community/users/{username}",
    response_model=UserProfileOut,
    summary="用户主页（项目/关注）",
)
async def user_profile(
    username: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> UserProfileOut:
    return UserProfileOut(**service.profile(username, current_user.username))


@router.post("/community/users/{username}/follow", summary="关注用户")
async def follow_user(
    username: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> dict:
    return service.follow(username, current_user.username)


@router.delete("/community/users/{username}/follow", summary="取消关注用户")
async def unfollow_user(
    username: str,
    current_user: CurrentUser,
    service: CommunityServiceDep,
    _: None = Depends(rate_limit("community")),
) -> dict:
    return service.unfollow(username, current_user.username)
