"""用户画像与长期记忆用例。"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class ProfileService:
    def __init__(self, *, settings: Any, redis: Any = None) -> None:
        self.settings = settings
        self.redis = redis

    def get(self, username: str) -> dict[str, Any]:
        from app.memory import get_profile

        return get_profile(username)

    def update(self, username: str, patch: dict[str, Any]) -> dict[str, Any]:
        from app.memory import update_profile

        return {"profile": update_profile(username, patch)}

    def remember_turn(self, username: str, text: str) -> dict[str, Any]:
        """从一轮对话中抽取稳定事实并合并进画像。"""
        from app.memory import remember_turn

        return {"profile": remember_turn(username, text)}
