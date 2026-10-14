"""
FastAPI 应用入口 —— 只负责**组装**，不含任何业务逻辑。

组装内容：
    * 配置（app/core/config.py）
    * 日志（app/core/logging.py）
    * 生命周期：Redis 连接池、RAG 链预热、LangGraph 图构建、资源回收
    * 中间件：CORS、trace_id
    * 全局异常处理器（app/core/exceptions.py）
    * 路由（app/api/v1/router.py）
    * 挂载到 app.state 的单例：redis / redis_manager / rag_chain / qa_graph

业务逻辑位置：
    路由 → app/api/v1/*      契约与转发
    用例 → app/services/*    编排
    编排 → app/graph/*       LangGraph 状态图（启用时）
    能力 → app/rag/*         检索与模型
    基建 → app/core/*        配置/日志/异常/Redis/DI
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import (
    get_logger,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
    setup_logging,
)
from app.core.redis import RedisManager, bind_sync_client, unbind_sync_client
from app.rag import RAGChain, get_rag_chain

logger = get_logger(__name__)

# 单例引用（同时挂到 app.state，供依赖注入读取）
_redis_manager: Optional[RedisManager] = None
_rag_chain: Optional[RAGChain] = None
_qa_graph: Any = None
_workflow_graph: Any = None


async def _build_workflow_graph(
    settings: Settings, redis: Any, chain: RAGChain
) -> Any:
    """构建 AI 工作流状态图（与 qa_graph 并列的另一张图）。

    两者拓扑与语义都不同，不复用 State；复用 `Deps` / `bind` / `emit`。
    同样 fail fast：带着不可用的编排对外服务，比启动失败更糟。
    """
    from app.graph.workflow import build_workflow_graph

    graph = build_workflow_graph(settings=settings, chain=chain, redis=redis)
    logger.info("工作流状态图已构建: 相位=plan（P2）")
    return graph


async def _build_graph(settings: Settings, redis: Any, chain: RAGChain) -> Any:
    """构建 LangGraph 状态图。

    图已是**唯一**的业务编排实现（原过程式 `agents/router.py` 已删除），
    因此构建失败即启动失败 —— fail fast 好过带着不可用的编排对外服务。
    """
    from app.graph.build import build_qa_graph
    from app.graph.checkpointer import setup_checkpointer

    graph = build_qa_graph(settings=settings, chain=chain, redis=redis)

    # Redis saver 需要显式建索引；必须在事件循环内 await
    await setup_checkpointer(getattr(graph, "checkpointer", None))

    logger.info(
        f"LangGraph 已构建: checkpointer={settings.graph.checkpointer} "
        f"recursion_limit={settings.graph.recursion_limit}"
    )
    return graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：连接 Redis → 预热 RAG 链 → 构建图；关闭：依次回收。"""
    global _redis_manager, _rag_chain, _qa_graph, _workflow_graph

    settings = get_settings()

    # ---- Redis：缓存 / 会话状态 / 限流 / 分布式锁 ----
    logger.info("正在连接 Redis ...")
    _redis_manager = RedisManager(settings.redis)
    try:
        await _redis_manager.connect()
        app.state.redis = _redis_manager.client
        app.state.redis_manager = _redis_manager
        health = await _redis_manager.health()
        logger.info(
            f"Redis 就绪: mode={health.get('mode')} ok={health.get('ok')} "
            f"version={health.get('version')}"
        )
        if _redis_manager.is_fallback:
            logger.warning(
                "⚠️ 当前为进程内降级模式，多 worker 下 限流/锁/缓存 不共享。"
                " 生产环境请启动 Redis 并设置 REDIS_FALLBACK_TO_MEMORY=false"
            )
    except Exception as exc:  # noqa: BLE001
        # Redis 是增强项而非硬依赖：连不上不应阻断服务启动
        app.state.redis = None
        app.state.redis_manager = None
        logger.error(f"Redis 初始化失败，相关功能降级: {exc}")

    # ---- 绑定同步 Redis 客户端 ----
    # 检索/嵌入缓存、会话消息缓存位于 LangGraph 的同步节点内执行，
    # 拿不到事件循环，因此需要一个进程内可达的同步客户端。
    bind_sync_client(getattr(app.state.redis_manager, "sync_client", None))

    # ---- OCR：图片类文档解析的可用性 ----
    # 图片上传最容易出现「用户以为能用、实际上不能用」：
    # 引擎或中文语言包缺失时，旧实现只会把图片解析成 0 页。
    # 启动时明确报一次，比等到上传失败再排查便宜得多。
    try:
        from app.document.ocr import probe as probe_ocr

        ocr_status = probe_ocr()
        if ocr_status["available"]:
            logger.info(
                f"OCR 就绪: tesseract={ocr_status['version']} "
                f"langs={','.join(ocr_status['languages'])} "
                f"tessdata={ocr_status['tessdata_prefix']}"
            )
        else:
            logger.warning(
                f"⚠️ OCR 不可用（图片类文档无法解析）: {ocr_status['reason']}"
            )
    except Exception as exc:  # noqa: BLE001 - 探测失败不应阻断启动
        logger.warning(f"OCR 探测失败: {exc}")

    # ---- 构建状态图（失败即启动失败）----
    _rag_chain = get_rag_chain()
    app.state.rag_chain = _rag_chain
    _qa_graph = await _build_graph(settings, app.state.redis, _rag_chain)
    app.state.qa_graph = _qa_graph

    # ---- AI 工作流图（同样失败即启动失败）----
    _workflow_graph = await _build_workflow_graph(
        settings, app.state.redis, _rag_chain
    )
    app.state.workflow_graph = _workflow_graph

    # ---- 自定义模型端点：同步进 providers ----
    # providers 属 rag 层，分层规则禁止它查库，所以必须由这里推一次。
    # 加载失败**不阻断启动**：模型端点是附加能力，不该因为一个 SQLite
    # 问题让整个服务起不来 —— 但要让日志明确说清，否则用户只会看到
    # 「我配的模型不见了」而毫无线索。
    try:
        from app.services.model_service import ModelService

        _custom_count = ModelService(settings=settings).sync_providers()
        logger.info(f"自定义模型端点已加载: {_custom_count} 个")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"加载自定义模型端点失败（服务继续启动，该功能不可用）: {exc}")

    # ---- 工作流 TTL 清理（必须自写：项目里没有现成的周期扫描机制）----
    # 放在 services 层：它需要访问 repositories，而 workflow 层被分层规则
    # 禁止向上依赖 —— 那边的 `cleanup.py` 只保留纯决策逻辑。
    from app.services.workflow_cleanup import start_cleanup_task

    app.state.workflow_cleanup = start_cleanup_task()

    # ---- 后台预热向量库与模型 ----
    # 图此时已可用（节点内是懒初始化），预热只是让首个请求更快。
    def _warm() -> None:
        _rag_chain.ensure_initialized()
        # 向量库为空时自动索引 data/ 下的文档
        # （补齐 README 宣称但旧实现未落实的"首次启动自动加载"行为）
        from app.rag import ensure_indexed

        ensure_indexed()

    async def _warmup() -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, _warm)
            logger.info("RAG 链预热完成")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"RAG 链预热失败（首次请求将延迟初始化）: {exc}")

    warmup_task = asyncio.ensure_future(_warmup())

    yield

    # ---- 关闭 ----
    logger.info("正在关闭应用，清理资源...")
    if not warmup_task.done():
        warmup_task.cancel()
    unbind_sync_client()
    if _qa_graph is not None:
        closer = getattr(_qa_graph, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass
        logger.info("LangGraph 已释放")
    if _workflow_graph is not None:
        closer = getattr(_workflow_graph, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass

    from app.services.workflow_cleanup import stop_cleanup_task

    await stop_cleanup_task()
    if _redis_manager is not None:
        await _redis_manager.close()
        logger.info("Redis 连接已关闭")

    from app.rag import _TOKEN_CACHE, _executor
    from app.tools.web_search import close_client

    if _executor:
        _executor.shutdown(wait=False)
        logger.info("线程池已关闭")
    _TOKEN_CACHE.clear()
    await close_client()
    logger.info("HTTP 客户端已关闭")


class TraceIDMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 trace_id，同时写入 request.state 与 contextvar。

    写入 contextvar 后，日志过滤器无需显式传参即可拿到当前请求的 trace_id。
    """

    async def dispatch(self, request: Request, call_next):
        trace_id = new_trace_id()
        request.state.trace_id = trace_id
        token = set_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            reset_trace_id(token)
        response.headers["X-Trace-ID"] = trace_id
        return response


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log, force=True)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        description=(
            "基于 FastAPI + LangGraph + Redis 的本地知识库问答系统。"
            "接口路径与 v3 保持兼容。"
        ),
    )
    app.state.settings = settings

    # 统一异常处理：AppError / 校验 / 限流 / 框架 HTTPException / 兜底 500
    register_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TraceIDMiddleware)

    # 路由：api_prefix 默认为空，保持与 v3 完全一致的接口路径
    app.include_router(api_router, prefix=settings.api_prefix)

    return app


app = create_app()
