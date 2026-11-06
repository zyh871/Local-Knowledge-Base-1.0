"""回归护栏：测试进程绝不能写到真实的 `backend/data/` 与 `backend/chroma_db/`。

这两条曾经真实出过事：
  * `tests/` 里写死的 conversation_id 直接落进真实 `conversations.db`，
    累计 1190 条孤儿消息（占消息表 76.5%），每跑一次新增 26 条；
  * 只隔离 `data/` 时，`chroma_persist_dir` 仍指向真实向量库，
    `ensure_embedding_schema()` 会因此判定"嵌入方式变了"并**重建生产索引**。

所以这里直接断言"路径不是真实路径"，而不是断言"路径里有某个前缀"——
前者不依赖 conftest 的具体实现。
"""

from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_data_path_is_not_production() -> None:
    from app.core.config import get_settings

    resolved = Path(get_settings().paths.data_path).resolve()
    production = (BACKEND_DIR / "data").resolve()
    assert resolved != production, f"DATA_PATH 指向了生产目录: {resolved}"
    assert resolved.exists()


def test_meta_db_lives_under_isolated_data_path() -> None:
    from app.core.config import get_settings
    from app.memory import conversation_memory as cm

    data_path = Path(get_settings().paths.data_path).resolve()
    assert Path(cm.META_DB).resolve().parent == data_path, (
        f"META_DB 未跟随 DATA_PATH: {cm.META_DB}"
    )
    assert Path(cm.META_DB).resolve() != (BACKEND_DIR / "data" / "conversations.db").resolve()


def test_chroma_dir_is_not_production() -> None:
    from app.core.config import get_settings

    resolved = Path(get_settings().paths.chroma_persist_dir).resolve()
    production = (BACKEND_DIR / "chroma_db").resolve()
    assert resolved != production, f"CHROMA_PERSIST_DIR 指向了生产向量库: {resolved}"


def test_credentials_are_not_real() -> None:
    """测试进程不允许持有真实凭据（否则"禁止联网"只是口头约定）。"""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.security.jwt_secret_key == "change-me-in-production"
    for key in ("tavily_api_key", "zhipu_api_key", "modelscope_api_key"):
        value = getattr(settings.model, key, "")
        assert value in ("", "disabled-in-test"), f"{key} 未隔离: {value[:4]}..."
