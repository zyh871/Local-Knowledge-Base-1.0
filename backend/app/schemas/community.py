"""社区契约：项目、贡献者、评论、用户主页。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    team_id: str
    name: str = Field(..., min_length=1, max_length=80)
    summary: str = Field("", max_length=300)
    description: str = ""
    language: str = Field("", max_length=40)
    license: str = Field("", max_length=40)
    repo_url: str = Field("", max_length=500)
    homepage: str = Field("", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)
    slug: str = Field("", max_length=60)


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    summary: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    language: Optional[str] = Field(None, max_length=40)
    license: Optional[str] = Field(None, max_length=40)
    repo_url: Optional[str] = Field(None, max_length=500)
    homepage: Optional[str] = Field(None, max_length=500)
    tags: Optional[list[str]] = Field(None, max_length=8)


class ProjectOut(BaseModel):
    id: str
    team_id: str
    slug: str = ""
    name: str
    summary: str = ""
    description: str = ""
    language: str = ""
    license: str = ""
    repo_url: str = ""
    homepage: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: str = "draft"
    stars_count: int = 0
    created_by: str = ""
    created_by_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    published_at: Optional[float] = None
    is_starred: bool = False
    is_watching: bool = False
    my_project_role: str = ""


class ContributorAdd(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: Literal["maintainer", "contributor"] = "contributor"


class ContributorOut(BaseModel):
    user_id: str = ""
    username: str
    role: str = "contributor"
    added_at: float = 0.0


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)


class CommentOut(BaseModel):
    id: str
    project_id: str
    user_id: str = ""
    username: str
    body: str
    created_at: float = 0.0
    deleted_at: Optional[float] = None


class UserProfileOut(BaseModel):
    id: str
    username: str
    display_name: str = ""
    projects: list[dict[str, Any]] = Field(default_factory=list)
    followers: int = 0
    following: int = 0
    is_following: bool = False
