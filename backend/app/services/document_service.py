"""文档摄取与管理用例。"""

from __future__ import annotations

import os
from typing import Any, Optional

from app.core.exceptions import (
    DocumentParseError,
    FileTooLargeError,
    NotFoundError,
    VectorStoreError,
)
from app.core.logging import get_logger
from app.core.redis import distributed_lock
from app.utils.files import (
    MAX_UPLOAD_SIZE,
    sanitize_filename,
    save_upload,
    validate_extension,
)

logger = get_logger(__name__)

_FALLBACK_QUESTIONS = [
    "介绍一下项目的主要功能",
    "如何上传和管理文档？",
    "支持哪些文件格式？",
    "问答系统是如何工作的？",
    "如何使用联网搜索功能？",
]


class DocumentService:
    def __init__(self, *, chain: Any, settings: Any, redis: Any = None) -> None:
        self.chain = chain
        self.settings = settings
        self.redis = redis

    # ------------------------------------------------------------------
    #  上传
    # ------------------------------------------------------------------

    @property
    def allowed_extensions(self) -> set[str]:
        from app.document import SUPPORTED_EXTENSIONS

        return {ext.lower() for ext in SUPPORTED_EXTENSIONS}

    def _ingest(self, filename: str, contents: bytes) -> tuple[str, int]:
        """落盘 → 解析 → 增量入向量库。返回 (真实文件名, 分块数)。"""
        from app.rag import add_documents_to_store, load_single_document, split_documents

        if len(contents) > MAX_UPLOAD_SIZE:
            raise FileTooLargeError(
                f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)"
            )

        safe_name = sanitize_filename(filename)
        validate_extension(safe_name, self.allowed_extensions)

        data_dir = self.settings.paths.data_path
        file_path = save_upload(data_dir, safe_name, contents)

        docs = load_single_document(file_path)
        if not docs:
            raise DocumentParseError("无法解析该文件内容，请检查文件格式")

        add_documents_to_store(docs)
        chunks = split_documents(docs)

        # 知识库内容变化 → 主动失效检索缓存（TTL 只是兜底）
        self.invalidate_retrieval_cache()

        return os.path.basename(file_path), len(chunks)

    def upload(self, *, filename: str, contents: bytes, trace_id: str) -> dict[str, Any]:
        real_name, chunks = self._ingest(filename, contents)
        logger.info(
            f"[{trace_id}] 文件上传成功: {filename}, 分块 {chunks}"
        )
        return {
            "success": True,
            "file": real_name,
            "chunks_added": chunks,
            "trace_id": trace_id,
        }

    def upload_many(
        self, *, files: list[tuple[str, bytes]], trace_id: str
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for filename, contents in files:
            try:
                real_name, chunks = self._ingest(filename, contents)
                results.append(
                    {"file": real_name, "success": True, "chunks_added": chunks}
                )
            except Exception as exc:  # noqa: BLE001
                results.append({"file": filename, "success": False, "error": str(exc)})
        succeeded = sum(1 for r in results if r["success"])
        logger.info(f"[{trace_id}] 批量上传完成: {len(files)} 文件, {succeeded} 成功")
        return {"results": results, "trace_id": trace_id}

    # ------------------------------------------------------------------
    #  删除 / 重建
    # ------------------------------------------------------------------

    def delete(self, *, filename: str, trace_id: str) -> dict[str, Any]:
        from app.rag import remove_document_from_store

        safe_name = sanitize_filename(filename)
        remove_document_from_store(safe_name)

        file_path = os.path.join(self.settings.paths.data_path, safe_name)
        if os.path.isfile(file_path):
            os.remove(file_path)

        self.invalidate_retrieval_cache()
        logger.info(f"[{trace_id}] 删除文档成功: {safe_name}")
        return {"success": True, "file": safe_name}

    async def rebuild(self) -> dict[str, Any]:
        """重建向量库（清空 + 重新索引 data/ 下的全部文档）。

        用分布式锁保证多 worker 下同一时刻只有一处在重建。
        """
        import asyncio

        from app.rag import get_vectorstore, rebuild_vectorstore

        def _do_rebuild() -> int:
            rebuild_vectorstore(reindex=True)
            store = get_vectorstore()
            return store.get_document_count() if hasattr(store, "get_document_count") else 0

        ttl = self.settings.redis.ttl_lock_rebuild
        try:
            if self.redis is None:
                chunks = _do_rebuild()
            else:
                async with distributed_lock(self.redis, "vectorstore:rebuild", ttl=ttl):
                    # 重建是阻塞的 CPU/IO 密集操作，放线程池避免占住事件循环
                    chunks = await asyncio.get_running_loop().run_in_executor(
                        None, _do_rebuild
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"向量库重建失败: {exc}", exc_info=True)
            raise VectorStoreError(f"向量库重建失败: {exc}") from exc

        logger.info(f"向量库重建完成，共 {chunks} 个分块")
        return {
            "success": True,
            "message": "向量库重建完成",
            "chunks": chunks,
        }

    def invalidate_retrieval_cache(self) -> None:
        """清空检索缓存。文档增删后**必须**调用，否则会返回陈旧结果。"""
        if self.redis is None:
            return
        from app.core.redis import cache_delete_pattern, get_keys

        self._schedule_cache_clear(cache_delete_pattern, self.redis, get_keys())

    @staticmethod
    def _schedule_cache_clear(clear_fn, redis: Any, keys: Any) -> None:
        """在事件循环可用时异步清理；否则退化为同步忽略。

        文档增删通常发生在异步请求上下文中，此处用 fire-and-forget 任务，
        避免把一次缓存清理变成阻塞上传响应的关键路径。
        """
        import asyncio

        async def _run() -> None:
            try:
                await clear_fn(redis, keys.scan_pattern("cache"))
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"检索缓存清理失败（将由 TTL 兜底）: {exc}")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run())
        except RuntimeError:
            logger.debug("无运行中的事件循环，跳过检索缓存清理（TTL 兜底）")

    # ------------------------------------------------------------------
    #  查询
    # ------------------------------------------------------------------

    def list_documents(self, *, skip: int, limit: int) -> dict[str, Any]:
        all_docs = self.chain.list_documents(limit=skip + limit)
        return {
            "documents": all_docs[skip : skip + limit],
            "total": len(all_docs),
            "skip": skip,
            "limit": limit,
        }

    def stats(self) -> dict[str, Any]:
        return self.chain.get_stats()

    def example_questions(self, *, limit: int) -> list[str]:
        try:
            docs = self.chain.list_documents()
            if not docs:
                return list(_FALLBACK_QUESTIONS)
            questions: list[str] = []
            for doc in docs[:10]:
                name = doc.get("filename", "") or doc.get("file", "")
                if name:
                    questions.append(f"介绍一下 {name} 的内容")
                    questions.append(f"{name} 中有哪些重要信息？")
            return list(set(questions))[:limit]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"获取示例问题失败: {exc}", exc_info=True)
            return list(_FALLBACK_QUESTIONS)

    def resolve_path(self, filename: str) -> Optional[str]:
        path = os.path.join(self.settings.paths.data_path, filename)
        return path if os.path.isfile(path) else None
