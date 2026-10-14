"""
分享链接用例。

自 v4 起改用 Redis 承载，替代原先的「磁盘 JSON + 手动 `_purge_expired_shares()`」：
  * TTL 由 Redis 原生管理，不再需要定时清理代码
  * 二级索引 `share:by_user:{user}` 支持按用户列举与批量撤销

语义保持（前端与历史链接不受影响）：
  * token 不存在            → 404
  * token 存在但已过期      → 410

⚠️ 为实现 404 / 410 的区分，Redis 键的 TTL 会比链接名义有效期**多留 1 小时宽限**，
   这样过期后仍能读到记录并返回 410，而不是因为键消失而错报 404。

兼容：仍能读取历史遗留的 `data/shares/{token}.json`，读取成功后会回填到 Redis。
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.exceptions import GoneError, NotFoundError
from app.core.logging import get_logger
from app.core.redis import cache_get_json, cache_set_json, get_keys

logger = get_logger(__name__)

_GRACE_SECONDS = 3600
_LEGACY_DEFAULT_TTL_HOURS = 24
_MAX_TTL_HOURS = 24 * 14


class ShareService:
    def __init__(self, *, settings: Any, redis: Any = None) -> None:
        self.settings = settings
        self.redis = redis

    # ------------------------------------------------------------------
    #  内部工具
    # ------------------------------------------------------------------

    @property
    def _legacy_dir(self) -> str:
        return os.path.join(self.settings.paths.data_path, "shares")

    @staticmethod
    def _now_ts() -> float:
        return datetime.now(timezone.utc).timestamp()

    def _clamp_ttl(self, ttl_hours: Optional[int]) -> int:
        default = round(self.settings.redis.ttl_share_default / 3600)
        try:
            value = int(ttl_hours if ttl_hours is not None else default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, _MAX_TTL_HOURS))

    def _legacy_path(self, token: str) -> str:
        return os.path.join(self._legacy_dir, f"{token}.json")

    def _read_legacy(self, token: str) -> Optional[dict[str, Any]]:
        path = self._legacy_path(token)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取历史分享文件失败 {token[:8]}: {exc}")
            return None

    # ------------------------------------------------------------------
    #  创建
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        conversation_id: str,
        username: str,
        title: str,
        payload: dict[str, Any],
        messages: Optional[list[dict[str, Any]]],
        ttl_hours: Optional[int],
    ) -> dict[str, Any]:
        ttl_h = self._clamp_ttl(ttl_hours)
        token = secrets.token_urlsafe(24)
        expires_at = self._now_ts() + ttl_h * 3600

        if messages:
            payload = {**payload, "history": messages}

        record = {
            "token": token,
            "conversation_id": conversation_id,
            "owner": username,
            "title": title or "对话",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
            "data": payload,
        }

        if self.redis is None:
            # 无 Redis 时的兜底：退化为旧的磁盘存储
            os.makedirs(self._legacy_dir, exist_ok=True)
            with open(self._legacy_path(token), "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
            logger.warning("Redis 不可用，分享链接已写入磁盘（旧路径）")
        else:
            keys = get_keys()
            await cache_set_json(
                self.redis,
                keys.share(token),
                record,
                ttl=ttl_h * 3600 + _GRACE_SECONDS,
            )
            try:
                await self.redis.sadd(keys.share_by_user(username), token)
                await self.redis.expire(
                    keys.share_by_user(username), ttl_h * 3600 + _GRACE_SECONDS
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"写入分享二级索引失败（不影响主流程）: {exc}")

        logger.info(
            f"创建分享链接: conv={conversation_id} token={token[:8]}... ttl={ttl_h}h"
        )
        return {
            "success": True,
            "token": token,
            "url_path": f"/shared/{token}",
            "expires_at": expires_at,
            "ttl_hours": ttl_h,
        }

    # ------------------------------------------------------------------
    #  读取
    # ------------------------------------------------------------------

    async def get(self, token: str) -> dict[str, Any]:
        record: Optional[dict[str, Any]] = None

        if self.redis is not None:
            record = await cache_get_json(self.redis, get_keys().share(token))

        # 回退到历史磁盘文件（并回填 Redis，实现平滑迁移）
        if record is None:
            record = self._read_legacy(token)
            if record is not None and self.redis is not None:
                remaining = self._remaining_ttl(record)
                await cache_set_json(
                    self.redis,
                    get_keys().share(token),
                    record,
                    ttl=max(remaining, 60) + _GRACE_SECONDS,
                )

        if record is None:
            raise NotFoundError("分享链接不存在或已过期")

        if float(record.get("expires_at", 0)) < self._now_ts():
            await self._delete(token)
            raise GoneError("分享链接已过期")

        return {
            "title": record.get("title", "对话"),
            "conversation_id": record.get("conversation_id"),
            "created_at": record.get("created_at"),
            "expires_at": (
                record.get("expires_at", 0) * 1000 if record.get("expires_at") else None
            ),
            "data": record.get("data", {}),
        }

    def _remaining_ttl(self, record: dict[str, Any]) -> int:
        return max(int(float(record.get("expires_at", 0)) - self._now_ts()), 0)

    async def _delete(self, token: str) -> None:
        if self.redis is not None:
            try:
                await self.redis.delete(get_keys().share(token))
            except Exception:  # noqa: BLE001
                pass
        legacy = self._legacy_path(token)
        if os.path.isfile(legacy):
            try:
                os.remove(legacy)
            except OSError:
                pass
