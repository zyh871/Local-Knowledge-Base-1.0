"""团队空间相关契约。

命名空间 `/teams`，与工作流的 `/workflow/workspaces`（本地文件夹绑定）
完全区分：前者管「谁能访问哪份知识」，后者管「任务跑在哪个目录」。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

#: 可委派的角色（owner 由创建/转让产生，不在邀请选项里）
AssignableRole = Literal["admin", "editor", "viewer"]


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80, description="团队名称")
    description: str = Field("", max_length=500, description="团队描述")


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=500)


class MemberAdd(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: AssignableRole = "viewer"


class MemberOut(BaseModel):
    username: str
    role: str
    joined_at: float


class TeamOut(BaseModel):
    id: str
    name: str
    description: str = ""
    owner: str
    my_role: str
    member_count: int = 0
    created_at: float
    updated_at: float


class TeamDetail(TeamOut):
    members: list[MemberOut] = Field(default_factory=list)


class PermissionsOut(BaseModel):
    team_id: str
    role: str
    permissions: list[str] = Field(default_factory=list)
