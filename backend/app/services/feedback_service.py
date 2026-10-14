"""用户反馈用例（文件存储，保持既有行为）。"""

from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class FeedbackService:
    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    @property
    def _dir(self) -> str:
        return os.path.join(self.settings.paths.data_path, "feedback")

    def _load_all(self) -> list[dict[str, Any]]:
        if not os.path.isdir(self._dir):
            return []
        items: list[dict[str, Any]] = []
        for filename in os.listdir(self._dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self._dir, filename), "r", encoding="utf-8") as handle:
                    items.append(json.load(handle))
            except Exception:  # noqa: BLE001  损坏文件跳过
                continue
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return items

    def submit(
        self,
        *,
        message_id: str,
        conversation_id: str,
        username: str,
        rating: int,
        comment: Optional[str],
    ) -> dict[str, Any]:
        os.makedirs(self._dir, exist_ok=True)
        record = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "username": username,
            "rating": rating,
            "comment": comment,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        path = os.path.join(self._dir, f"{message_id}_{int(_time.time())}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        logger.info(f"反馈已保存: message_id={message_id}, rating={rating}")
        return {"success": True}

    def stats(self) -> dict[str, Any]:
        """聚合统计，用于 badcase 回流分析。"""
        items = self._load_all()
        if not items:
            return {"total": 0, "ratings": {}, "recent": [], "badcase_count": 0}

        ratings: dict[str, int] = {}
        for item in items:
            key = str(item.get("rating", "unknown"))
            ratings[key] = ratings.get(key, 0) + 1

        return {
            "total": len(items),
            "ratings": ratings,
            "badcase_count": sum(1 for i in items if i.get("rating", 0) <= 2),
            "recent": items[:20],
        }

    def list(
        self, *, rating: Optional[int], skip: int, limit: int
    ) -> dict[str, Any]:
        items = self._load_all()
        if rating is not None:
            items = [i for i in items if i.get("rating") == rating]
        return {
            "items": items[skip : skip + limit],
            "total": len(items),
            "skip": skip,
            "limit": limit,
        }
