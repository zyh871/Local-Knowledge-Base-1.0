"""身份管理路由：部署管理员建号/邀请 + 公开的邀请接受。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import AdminUser, rate_limit
from app.core.exceptions import NotFoundError
from app.schemas.identity import (
    InvitationAccept,
    InvitationAcceptResult,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import identity_service

router = APIRouter()


@router.get("/admin/users", response_model=list[UserOut], summary="账号列表")
async def list_users(
    admin: AdminUser,
    _: None = Depends(rate_limit("admin")),
) -> list[UserOut]:
    return [UserOut(**item) for item in identity_service.list_users()]


@router.post("/admin/users", response_model=UserOut, summary="创建账号")
async def create_user(
    payload: UserCreate,
    admin: AdminUser,
    _: None = Depends(rate_limit("admin")),
) -> UserOut:
    return UserOut(
        **identity_service.create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            global_role=payload.global_role,
        )
    )


@router.patch("/admin/users/{user_id}", response_model=UserOut, summary="修改账号")
async def update_user(
    user_id: str,
    payload: UserUpdate,
    admin: AdminUser,
    _: None = Depends(rate_limit("admin")),
) -> UserOut:
    return UserOut(
        **identity_service.update_user(
            user_id,
            display_name=payload.display_name,
            global_role=payload.global_role,
            status=payload.status,
            password=payload.password,
        )
    )


@router.get(
    "/admin/invitations",
    response_model=list[InvitationOut],
    summary="邀请列表",
)
async def list_invitations(
    admin: AdminUser,
    _: None = Depends(rate_limit("admin")),
) -> list[InvitationOut]:
    return [InvitationOut(**item) for item in identity_service.list_invitations()]


@router.post(
    "/admin/invitations",
    response_model=InvitationCreated,
    summary="创建邀请（账号/团队）",
)
async def create_invitation(
    payload: InvitationCreate,
    admin: AdminUser,
    _: None = Depends(rate_limit("admin")),
) -> InvitationCreated:
    data = identity_service.create_invitation(
        kind=payload.kind,
        team_id=payload.team_id,
        role=payload.role,
        created_by=admin.username,
        ttl_hours=payload.ttl_hours,
    )
    return InvitationCreated(**data)


@router.post(
    "/invitations/accept",
    response_model=InvitationAcceptResult,
    summary="接受邀请（公开端点）",
)
async def accept_invitation(
    payload: InvitationAccept,
    _: None = Depends(rate_limit("token", limit=10)),
) -> InvitationAcceptResult:
    result = identity_service.accept_invitation(
        token=payload.token,
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
    )
    inv = result["invitation"]

    # 团队邀请：接受后直接加入空间（P1 会切到 team_service 的成员写入）
    if inv["kind"] == "team" and inv.get("team_id"):
        from app.repositories import team_repo

        if not team_repo.get_team(inv["team_id"]):
            raise NotFoundError("邀请对应的团队不存在")
        team_repo.upsert_member(
            inv["team_id"],
            result["user"]["username"],
            inv.get("role") or "viewer",
            user_id=result["user"]["id"],
        )

    return InvitationAcceptResult(
        user=UserOut(**result["user"]),
        invitation=InvitationOut(**inv),
    )
