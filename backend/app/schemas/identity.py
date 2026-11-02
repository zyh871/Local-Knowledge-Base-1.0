"""身份与邀请契约。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str = ""
    global_role: str = "user"
    status: str = "active"
    created_at: float = 0.0


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field("", max_length=64)
    global_role: Literal["admin", "user"] = "user"


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=64)
    global_role: Optional[Literal["admin", "user"]] = None
    status: Optional[Literal["active", "disabled"]] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)


class InvitationCreate(BaseModel):
    kind: Literal["account", "team"] = "account"
    team_id: Optional[str] = None
    role: Literal["admin", "editor", "viewer"] = "viewer"
    ttl_hours: int = Field(72, ge=1, le=168)


class InvitationOut(BaseModel):
    id: str
    kind: str
    team_id: Optional[str] = None
    role: str = ""
    created_by: str = ""
    expires_at: float
    used_at: Optional[float] = None
    used_by: Optional[str] = None
    created_at: float


class InvitationCreated(InvitationOut):
    token: str
    invite_path: str


class InvitationAccept(BaseModel):
    token: str = Field(..., min_length=8)
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field("", max_length=64)


class InvitationAcceptResult(BaseModel):
    user: UserOut
    invitation: InvitationOut
