"""
FastAPI 依赖注入 provider —— 所有可替换依赖的**唯一**提供点。

为什么不用模块级全局变量：
    旧实现把 RAGChain、限流器、向量库单例散落在模块级全局，导致测试无法替换、
    多 worker 语义错乱。改为 `app.state` + `Depends` 后，测试可直接覆盖依赖。

测试替换示例：
    app.dependency_overrides[get_settings] = lambda: Settings(env="test")
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.core.config import Settings, get_settings
from app.core.exceptions import RateLimitError, UnauthorizedError
from app.core.logging import get_trace_id
from app.core.redis import check_rate_limit
from app.core.security import UserInDB, decode_access_token, get_user, require_active

# ---------------------------------------------------------------------------
#  基础 provider
# ---------------------------------------------------------------------------

SettingsDep = Annotated[Settings, Depends(get_settings)]

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/token", auto_error=False)


def get_redis(request: Request) -> Any:
    """返回 lifespan 中建立的 Redis 客户端。"""
    client = getattr(request.app.state, "redis", None)
    if client is None:
        from app.core.exceptions import DependencyUnavailableError

        raise DependencyUnavailableError("Redis 尚未初始化")
    return client


def get_redis_optional(request: Request) -> Optional[Any]:
    return getattr(request.app.state, "redis", None)


def get_rag_chain_dep(request: Request) -> Any:
    """RAGChain 单例（lifespan 中创建）。"""
    chain = getattr(request.app.state, "rag_chain", None)
    if chain is None:
        from app.rag import get_rag_chain

        chain = get_rag_chain()
    return chain


def get_qa_graph(request: Request) -> Any:
    """编译好的 LangGraph 图（lifespan 中构建）。"""
    graph = getattr(request.app.state, "qa_graph", None)
    if graph is None:
        from app.core.exceptions import DependencyUnavailableError

        raise DependencyUnavailableError("问答图尚未初始化")
    return graph


RedisDep = Annotated[Any, Depends(get_redis)]
RagChainDep = Annotated[Any, Depends(get_rag_chain_dep)]
QAGraphDep = Annotated[Any, Depends(get_qa_graph)]


# ---------------------------------------------------------------------------
#  认证
# ---------------------------------------------------------------------------


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> UserInDB:
    payload = decode_access_token(token)
    username = payload.get("sub")
    if not username:
        raise UnauthorizedError("无法验证凭据")
    user = get_user(str(username))
    if user is None:
        raise UnauthorizedError("无法验证凭据")
    return user


def get_current_active_user(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
) -> UserInDB:
    return require_active(current_user)


CurrentUser = Annotated[UserInDB, Depends(get_current_active_user)]


def get_optional_user(
    token: Annotated[Optional[str], Depends(oauth2_scheme_optional)] = None,
) -> Optional[UserInDB]:
    """公开端点可用：有 token 就解析，没有也不报错。"""
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        username = payload.get("sub")
        return get_user(str(username)) if username else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
#  限流依赖工厂
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def resolve_identity(request: Request) -> str:
    """限流身份：优先用户名，回退 IP。

    为什么不用纯 IP：同一 NAT 出口的用户会互相挤占配额。
    为什么保留 IP 回退：`/share/{token}` 等公开端点无登录态。
    """
    username = getattr(request.state, "username", None)
    if username:
        return f"u:{username}"

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = decode_access_token(auth[7:].strip())
            sub = payload.get("sub")
            if sub:
                return f"u:{sub}"
        except Exception:  # noqa: BLE001  令牌无效时退回 IP 维度
            pass

    return f"ip:{_client_ip(request)}"


def rate_limit(
    scope: str,
    limit: Optional[int] = None,
    window: Optional[int] = None,
):
    """生成一个限流依赖。

        @router.post("/ask")
        async def ask(..., _: None = Depends(rate_limit("ask"))):
    """

    async def _dependency(
        request: Request,
        settings: SettingsDep,
        redis: Optional[Any] = Depends(get_redis_optional),
    ) -> None:
        rl = settings.rate_limit
        if not rl.enabled or redis is None:
            return
        eff_limit, eff_window = rl.limit_for(scope)
        if limit is not None:
            eff_limit = limit
        if window is not None:
            eff_window = window

        result = await check_rate_limit(
            redis,
            scope=scope,
            identity=resolve_identity(request),
            limit=eff_limit,
            window=eff_window,
        )
        if not result.allowed:
            from app.core.logging import get_logger

            get_logger(__name__).warning(
                f"[{get_trace_id(request)}] 触发限流 scope={scope} "
                f"identity={resolve_identity(request)} {result.current}/{result.limit}"
            )
            raise RateLimitError(
                f"请求过于频繁，请 {result.retry_after} 秒后重试",
                retry_after=result.retry_after,
            )

    return _dependency
