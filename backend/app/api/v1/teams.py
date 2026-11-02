"""团队空间路由（`/teams`）。

与既有 `/workflow/workspaces` 的区别：
  * `/workflow/workspaces` —— 任务绑定的**本地文件夹**
  * `/teams`               —— 成员与**角色权限**（谁能读/写/管理）

本模块只做契约声明与转发，业务规则在 `app/services/team_service.py`。
所有端点都要求登录（`CurrentUser`），并通过 service 校验团队成员身份。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import TeamServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.identity import InvitationCreated
from app.schemas.team import (
    MemberAdd,
    MemberOut,
    PermissionsOut,
    TeamCreate,
    TeamDetail,
    TeamInvitationCreate,
    TeamOut,
    TeamUpdate,
    TransferRequest,
)

router = APIRouter()


@router.get("/teams", response_model=list[TeamOut], summary="我加入的团队")
async def list_teams(
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> list[TeamOut]:
    return [TeamOut(**item) for item in service.list_teams(current_user.username)]


@router.post("/teams", response_model=TeamDetail, summary="创建团队")
async def create_team(
    payload: TeamCreate,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> TeamDetail:
    return TeamDetail(
        **service.create_team(
            name=payload.name,
            description=payload.description,
            owner=current_user.username,
        )
    )


@router.get("/teams/{team_id}", response_model=TeamDetail, summary="团队详情")
async def get_team(
    team_id: str,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> TeamDetail:
    return TeamDetail(**service.get_team(team_id, current_user.username))


@router.patch("/teams/{team_id}", response_model=TeamDetail, summary="修改团队")
async def update_team(
    team_id: str,
    payload: TeamUpdate,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> TeamDetail:
    return TeamDetail(
        **service.update_team(
            team_id,
            current_user.username,
            name=payload.name,
            description=payload.description,
        )
    )


@router.delete("/teams/{team_id}", summary="删除团队")
async def delete_team(
    team_id: str,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> dict:
    return service.delete_team(team_id, current_user.username)


@router.get(
    "/teams/{team_id}/members",
    response_model=list[MemberOut],
    summary="团队成员列表",
)
async def list_members(
    team_id: str,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> list[MemberOut]:
    return [
        MemberOut(**item)
        for item in service.list_members(team_id, current_user.username)
    ]


@router.post(
    "/teams/{team_id}/members",
    response_model=MemberOut,
    summary="邀请成员 / 修改角色",
)
async def add_member(
    team_id: str,
    payload: MemberAdd,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> MemberOut:
    return MemberOut(
        **service.add_member(
            team_id,
            current_user.username,
            target=payload.username,
            role=payload.role,
        )
    )


@router.delete(
    "/teams/{team_id}/members/{username}",
    summary="移除成员",
)
async def remove_member(
    team_id: str,
    username: str,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> dict:
    return service.remove_member(team_id, current_user.username, username)


@router.post(
    "/teams/{team_id}/transfer",
    response_model=TeamDetail,
    summary="转让所有权（owner）",
)
async def transfer_owner(
    team_id: str,
    payload: TransferRequest,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> TeamDetail:
    return TeamDetail(
        **service.transfer_owner(
            team_id, current_user.username, payload.username
        )
    )


@router.post(
    "/teams/{team_id}/invitations",
    response_model=InvitationCreated,
    summary="生成团队邀请链接（admin+）",
)
async def create_team_invitation(
    team_id: str,
    payload: TeamInvitationCreate,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> InvitationCreated:
    return InvitationCreated(
        **service.create_team_invitation(
            team_id,
            current_user.username,
            role=payload.role,
            ttl_hours=payload.ttl_hours,
        )
    )


@router.get(
    "/teams/{team_id}/permissions",
    response_model=PermissionsOut,
    summary="我的有效权限",
)
async def my_permissions(
    team_id: str,
    current_user: CurrentUser,
    service: TeamServiceDep,
    _: None = Depends(rate_limit("teams")),
) -> PermissionsOut:
    return PermissionsOut(
        **service.permissions(team_id, current_user.username)
    )
