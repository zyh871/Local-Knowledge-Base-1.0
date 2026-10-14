"""对话管理用例（列表/详情/更新/删除/导出/清空）。"""

from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger

logger = get_logger(__name__)


class ConversationService:
    def __init__(self, *, settings: Any, redis: Any = None) -> None:
        self.settings = settings
        self.redis = redis

    # ------------------------------------------------------------------
    #  内部：延迟 import，避免 memory 包在 import 期就初始化 SQLite
    # ------------------------------------------------------------------

    @staticmethod
    def _mem():
        from app import memory

        return memory

    def _assert_owner(self, conversation_id: str, username: str) -> None:
        if not self._mem().verify_owner(conversation_id, username):
            raise PermissionDeniedError("无权访问该对话")

    # ------------------------------------------------------------------
    #  查询
    # ------------------------------------------------------------------

    def list(
        self,
        *,
        username: str,
        search: Optional[str],
        include_archived: bool,
        skip: int,
        limit: int,
    ) -> dict[str, Any]:
        page = self._mem().list_conversations(
            username=username,
            include_archived=include_archived,
            skip=skip,
            limit=limit,
        )
        conversations = page["items"]
        if search:
            needle = search.lower()
            conversations = [
                c
                for c in conversations
                if needle in (c.get("title") or "").lower()
                or needle in (c.get("preview") or "").lower()
            ]
            page["total"] = len(conversations)
        return {
            "conversations": conversations,
            "total": page["total"],
            "skip": skip,
            "limit": limit,
        }

    def get(self, *, conversation_id: str, username: str) -> dict[str, Any]:
        self._assert_owner(conversation_id, username)
        conv = self._mem().get_conversation(conversation_id)
        if not conv:
            raise NotFoundError("对话不存在")
        return {"conversation": conv}

    # ------------------------------------------------------------------
    #  写入
    # ------------------------------------------------------------------

    def update(
        self, *, conversation_id: str, username: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        self._assert_owner(conversation_id, username)
        updated = self._mem().upsert_conversation(
            conversation_id,
            username=username,
            title=patch.get("title"),
            pinned=patch.get("pinned"),
            locked=patch.get("locked"),
            archived=patch.get("archived"),
            bump_turn=False,
        )
        return {"conversation": updated}

    def delete(self, *, conversation_id: str, username: str) -> dict[str, Any]:
        self._assert_owner(conversation_id, username)
        if not self._mem().delete_conversation(conversation_id):
            raise NotFoundError("对话不存在")
        self._drop_cache(conversation_id)
        logger.info(f"删除对话成功: {conversation_id}")
        return {"success": True}

    def batch_delete(
        self, *, conversation_ids: list[str], username: str
    ) -> dict[str, Any]:
        deleted = 0
        for conv_id in conversation_ids:
            if not self._mem().verify_owner(conv_id, username):
                continue
            if self._mem().delete_conversation(conv_id):
                deleted += 1
                self._drop_cache(conv_id)
        logger.info(f"批量删除完成: {deleted}/{len(conversation_ids)}")
        return {"success": True, "deleted_count": deleted}

    def clear(self, *, conversation_id: str, username: str) -> dict[str, Any]:
        self._assert_owner(conversation_id, username)
        self._mem().clear_memory(conversation_id)
        self._drop_cache(conversation_id)
        logger.info(f"清除对话记忆成功: {conversation_id}")
        return {"success": True}

    def export(self, *, conversation_id: str, username: str) -> dict[str, Any]:
        self._assert_owner(conversation_id, username)
        if not self._mem().get_conversation(conversation_id):
            raise NotFoundError("对话不存在")
        return self._mem().export_conversation(conversation_id)

    # ------------------------------------------------------------------
    #  缓存联动（P4）
    # ------------------------------------------------------------------

    def _drop_cache(self, conversation_id: str) -> None:
        """会话被删除/清空时同步清掉 Redis 热缓存，避免读到幽灵数据。"""
        if self.redis is None:
            return
        import asyncio

        from app.core.redis import get_keys

        keys = get_keys()

        async def _run() -> None:
            try:
                await self.redis.delete(
                    keys.session_msgs(conversation_id),
                    keys.session_meta(conversation_id),
                    keys.session_owner(conversation_id),
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"清理会话缓存失败（TTL 兜底）: {exc}")

        try:
            asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass
