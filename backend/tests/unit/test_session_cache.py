"""
会话消息 / 用户画像 Redis 缓存测试（P4）。

核心验证点：
  1. 缓存启用时会话历史走 Redis，且能正确回源 SQLite（真相源）
  2. 缓存被清掉后（模拟换了一个 worker 或 Redis 重启）仍能读到完整历史
     —— 这是引入 Redis 的核心动机：**多 worker 一致性**
  3. Redis 不可用时全程降级直读 SQLite，功能不受影响
  4. 写入顺序为「先 SQLite 后 Redis」，不会出现"缓存有、库没有"的幻觉数据
"""

from __future__ import annotations

import uuid

import pytest

from app.core import redis as core_redis


@pytest.fixture
def bound_redis():
    """绑定一个进程内同步 Redis 客户端，用完解绑。"""
    import fakeredis

    client = fakeredis.FakeRedis(decode_responses=True)
    core_redis.bind_sync_client(client)
    yield client
    core_redis.unbind_sync_client()


@pytest.fixture
def unbound_redis():
    core_redis.unbind_sync_client()
    yield
    core_redis.unbind_sync_client()


class TestSessionMessageCache:
    def test_cache_disabled_degrades_to_sqlite(self, unbound_redis):
        """Redis 未绑定时，读写应完全走 SQLite，功能不受影响。"""
        from app.memory import conversation_memory as cm

        cid = f"cache_off_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="u1", title="t")
        cm.save_context(cid, "问题A", "回答A")

        history = cm.load_history(cid)
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "问题A"}
        assert history[1] == {"role": "assistant", "content": "回答A"}
        cm.delete_conversation(cid)

    def test_write_populates_cache(self, bound_redis):
        from app.memory import conversation_memory as cm
        from app.repositories import session_cache

        cid = f"cache_on_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="u1", title="t")
        cm.save_context(cid, "问题B", "回答B")

        cached = session_cache.get_cached_messages(cid)
        assert cached is not None
        assert [m["content"] for m in cached] == ["问题B", "回答B"]
        cm.delete_conversation(cid)

    def test_cache_lost_falls_back_to_sqlite(self, bound_redis):
        """★ 多 worker 场景：缓存丢失后必须能从真相源恢复完整历史。"""
        from app.memory import conversation_memory as cm
        from app.repositories import session_cache

        cid = f"cache_lost_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="u1", title="t")
        cm.save_context(cid, "q1", "a1")
        cm.save_context(cid, "q2", "a2")

        # 模拟另一个 worker（缓存为空）
        bound_redis.flushall()
        assert session_cache.get_cached_messages(cid) is None

        history = cm.load_history(cid)
        assert [m["content"] for m in history] == ["q1", "a1", "q2", "a2"]
        # 回源后应回填缓存
        assert session_cache.get_cached_messages(cid) is not None
        cm.delete_conversation(cid)

    def test_clear_invalidates_cache(self, bound_redis):
        from app.memory import conversation_memory as cm
        from app.repositories import session_cache

        cid = f"cache_clear_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="u1", title="t")
        cm.save_context(cid, "q", "a")
        cm.clear_memory(cid)

        assert session_cache.get_cached_messages(cid) is None
        assert cm.load_history(cid) == []
        cm.delete_conversation(cid)

    def test_cache_has_ttl(self, bound_redis):
        from app.core.redis import get_keys
        from app.memory import conversation_memory as cm

        cid = f"cache_ttl_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="u1", title="t")
        cm.save_context(cid, "q", "a")

        ttl = bound_redis.ttl(get_keys().session_msgs(cid))
        assert ttl > 0
        cm.delete_conversation(cid)

    def test_list_is_trimmed_to_cap(self, bound_redis):
        """防止长会话把 List 撑爆（上限 SESSION_MESSAGE_CAP）。"""
        from app.repositories import session_cache

        cid = f"cache_cap_{uuid.uuid4().hex[:8]}"
        cap = session_cache.SESSION_MESSAGE_CAP
        payload = [{"role": "user", "content": f"m{i}"} for i in range(cap + 50)]
        session_cache.append_messages(cid, payload)

        cached = session_cache.get_cached_messages(cid)
        assert cached is not None
        assert len(cached) == cap
        # 保留的是最新的那批
        assert cached[-1]["content"] == f"m{cap + 49}"

        from app.core.redis import get_keys

        bound_redis.delete(get_keys().session_msgs(cid))

    def test_corrupted_entry_is_treated_as_miss(self, bound_redis):
        """缓存内容损坏时应回源数据库，而不是抛异常或返回脏数据。"""
        from app.core.redis import get_keys
        from app.repositories import session_cache

        cid = f"cache_broken_{uuid.uuid4().hex[:8]}"
        bound_redis.rpush(get_keys().session_msgs(cid), "{not json")
        assert session_cache.get_cached_messages(cid) is None
        bound_redis.delete(get_keys().session_msgs(cid))


class TestProfileCache:
    def test_profile_read_through(self, bound_redis):
        from app.memory import user_profile
        from app.repositories import session_cache

        user = f"pc_{uuid.uuid4().hex[:8]}"
        user_profile.update_profile(user, {"display_name": "张三"})

        # update 会主动失效，首次读应回源并回填
        session_cache.invalidate_profile(user)
        assert session_cache.get_cached_profile(user) is None

        profile = user_profile.get_profile(user)
        assert profile["display_name"] == "张三"
        assert session_cache.get_cached_profile(user) is not None
        user_profile.delete_profile(user)

    def test_profile_update_does_not_leave_stale_cache(self, bound_redis):
        """画像更新后缓存不能残留旧值。

        实现上是「先失效、再由 `return get_profile()` 回填新值」，
        因此更新后缓存**非空是正常的** —— 关键不变量是它必须与新值一致。
        """
        from app.memory import user_profile
        from app.repositories import session_cache

        user = f"pi_{uuid.uuid4().hex[:8]}"
        user_profile.update_profile(user, {"display_name": "李四"})
        user_profile.get_profile(user)  # 填充缓存
        assert session_cache.get_cached_profile(user)["display_name"] == "李四"

        user_profile.update_profile(user, {"profession": "工程师"})

        cached = session_cache.get_cached_profile(user)
        if cached is not None:
            assert cached["profession"] == "工程师", "缓存里出现了过期画像"
        assert user_profile.get_profile(user)["profession"] == "工程师"
        user_profile.delete_profile(user)

    def test_profile_delete_clears_cache(self, bound_redis):
        from app.memory import user_profile
        from app.repositories import session_cache

        user = f"pd_{uuid.uuid4().hex[:8]}"
        user_profile.update_profile(user, {"display_name": "王五"})
        user_profile.get_profile(user)
        assert session_cache.get_cached_profile(user) is not None

        user_profile.delete_profile(user)
        assert session_cache.get_cached_profile(user) is None

    def test_empty_profile_not_cached(self, bound_redis):
        """合成空画像未落库，不应进缓存（否则会出现库里没有、缓存有的不一致）。"""
        from app.memory import user_profile
        from app.repositories import session_cache

        user = f"pe_{uuid.uuid4().hex[:8]}"
        profile = user_profile.get_profile(user)
        assert profile["username"] == user
        assert session_cache.get_cached_profile(user) is None
