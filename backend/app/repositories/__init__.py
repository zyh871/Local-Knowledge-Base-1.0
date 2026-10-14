"""
数据访问层。

  * `session_cache` —— 会话消息 / 用户画像的 Redis 热缓存（同步客户端）

分层约束：本层不得依赖 services / graph / api。
"""

from app.repositories import session_cache

__all__ = ["session_cache"]
