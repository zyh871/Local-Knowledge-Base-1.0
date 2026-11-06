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
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

# ⚠️ 必须给**绝对路径**。无参 `load_dotenv()` 会按 cwd 向上找第一个 .env，
# 于是「从 backend/ 启动」与「从仓库根启动」会得到两份不同的配置
# （实测 JWT 密钥 41 vs 58，互不认），换目录重启就会让所有 token 失效。
# 这里只为让 `os.getenv()` 直读的地方（如 SERVE_FRONTEND_DIR）也看到配置，
# override=False 保证真实环境变量优先。
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

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

    # ---- 安全基线：默认 JWT 密钥等于「任何人都能伪造令牌」----
    # 生产环境直接拒绝启动；开发环境给出显眼告警，避免本地调试被卡死。
    if settings.security.is_default_secret:
        message = (
            "JWT_SECRET_KEY 仍是默认值 change-me-in-production，"
            "任何人都能伪造登录令牌。请在 .env 设置至少 32 位的随机值。"
        )
        if settings.env == "prod":
            raise RuntimeError(message)
        logger.warning("⚠️ %s（当前 env=%s，仅限本地开发）", message, settings.env)

    # ---- 身份：初始化 users 表并注入查询 provider ----
    # 失败不阻断启动：回退到 env 管理员，保证旧部署仍可登录。
    try:
        from app.core import security
        from app.services import identity_service

        identity_service.bootstrap_identity()
        security.set_user_provider(identity_service.get_user_record)

        from app.services.team_service import migrate_legacy_teams

        migrated = migrate_legacy_teams()
        logger.info(
            f"身份数据已就绪（identity.db）；团队 user_id 回填 {migrated} 条"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"身份初始化失败，回退到 env 管理员: {exc}")

    # ---- 联网搜索：把界面里存的供应商与密钥推给工具层 ----
    # tools 层不能读数据库（分层约束），由 service 读出来注入。
    # 不配也能用：DuckDuckGo 免密钥，或环境变量里的 TAVILY/SERPER。
    try:
        from app.services import search_service

        search_service.apply_runtime_config()
        _sc = search_service.current_config()
        logger.info(
            f"联网搜索已就绪: {_sc['provider_name']}"
            f"（{'已配置' if _sc['configured'] else '未配置'}）"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"联网搜索配置加载失败: {exc}")

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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """补一组基础安全响应头。

    原先进程完全不发这些头，浏览器侧默认行为是「允许被任意站点内嵌」
    （点击劫持）与「允许 MIME 嗅探」。这里只补**低风险、不改变行为**的几项：

      * `X-Content-Type-Options: nosniff` —— 禁止按内容猜类型
      * `X-Frame-Options: DENY` —— 禁止被 iframe 内嵌
      * `Referrer-Policy: no-referrer` —— 跨站不泄漏路径（可能含会话 id）
      * `X-XSS-Protection: 0` —— 显式关掉已被废弃的旧过滤器
      * `Content-Security-Policy` —— 只锁 frame-ancestors/base-uri/form-action，
        其余保持宽松。**不能**用 `default-src 'none'`：本进程同时托管前端 SPA，
        收死会让打包客户端白屏。

    ⚠️ 不要把它放在 CORS 之前 —— CORS 预检的响应也要带这些头。
    """

    # 故意保持宽松：只约束「谁能内嵌我」和「基准 URL」，不限制资源来源。
    _CSP = (
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("X-XSS-Protection", "0")
        headers.setdefault("Content-Security-Policy", self._CSP)
        return response


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log, force=True)

    # 生产环境不暴露交互式文档：/docs 与 /openapi.json 匿名可读，
    # 等于把全部端点、参数结构与内部错误模型免费发给任何人。
    # 开发环境保留，契约测试也依赖它。
    expose_docs = settings.env != "prod"

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
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
    app.add_middleware(SecurityHeadersMiddleware)

    # 路由：api_prefix 默认为空，保持与 v3 完全一致的接口路径
    app.include_router(api_router, prefix=settings.api_prefix)

    # ── 客户端模式：后端自己托管已构建的前端 ──────────────────────
    # 桌面客户端只有一个进程，不能像生产部署那样靠 nginx 做 /api 反向代理。
    # 这里做两件事：再挂一份 /api 前缀的同一组路由 + 把静态目录挂到根路径。
    #
    # 用环境变量而不是 Settings 字段，是为了让默认行为**完全不变**：
    # 不设 SERVE_FRONTEND_DIR 时下面是死代码，接口路径集合与契约基线逐字节一致。
    frontend_dir = os.getenv("SERVE_FRONTEND_DIR", "").strip()
    if frontend_dir and Path(frontend_dir).is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        # 前端 http.js 里的 API_BASE 是 '/api'，同源部署必须补这份前缀
        app.include_router(api_router, prefix="/api")

        root = Path(frontend_dir).resolve()
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")
        index_file = root / "index.html"

        # ★ 浏览器导航必须交给前端路由，哪怕同名的 API 存在。
        #   后端的兼容路由里有 GET /models、/documents…，浏览器直接打开
        #   http://127.0.0.1:8000/models 会命中 API 并返回 401 JSON，
        #   用户看到的就是一屏 {"detail":"Not authenticated"}。
        #   判据用 Accept: text/html —— 只有导航会带它，fetch/XHR 不会。
        @app.middleware("http")
        async def _spa_fallback(request, call_next):  # type: ignore[no-untyped-def]
            path = request.url.path
            if (
                request.method == "GET"
                and "text/html" in request.headers.get("accept", "")
                and not path.startswith(("/api/", "/assets/"))
                and path not in ("/docs", "/redoc", "/openapi.json")
            ):
                candidate = (root / path.lstrip("/")).resolve()
                is_real_file = candidate.is_file() and str(candidate).startswith(str(root))
                if not is_real_file:
                    return FileResponse(index_file)
            return await call_next(request)

        # 兜底静态挂载：让 "/" 这种不带 Accept: text/html 的请求也能拿到首页。
        # 必须放在最后——前面注册的 API 路由优先级更高。
        app.mount("/", StaticFiles(directory=root, html=True), name="spa")

    return app


app = create_app()
