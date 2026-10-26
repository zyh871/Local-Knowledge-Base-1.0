"""
模型工厂：按 `MODEL_BACKEND` 返回对应的 LLM / Embedding / Vision 实例。

设计要点：
  * 所有工厂都是**懒初始化 + 进程内单例**，避免启动期就建立网络连接
  * 智谱（zhipu）走 OpenAI 兼容协议，直接复用 `ChatOpenAI` / `OpenAIEmbeddings`，
    只替换 `api_key` 与 `base_url`
  * 嵌入结果自动套上 Redis 缓存（`wrap_with_cache`）
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from langchain_core.language_models.llms import BaseLLM

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.embeddings import (
    OpenAICompatEmbeddings,
    SimpleTFIDFEmbeddings,
    wrap_with_cache,
)

logger = get_logger(__name__)

__all__ = [
    "MockLLM",
    "get_embeddings",
    "get_llm",
    "get_query_llm",
    "get_thinking_llm",
    "get_vision_llm",
    "is_thinking_capable",
    "list_llm_models",
    "reset_model_singletons",
    "resolve_model",
]


# ---------------------------------------------------------------------------
#  Mock LLM（本地/测试）
# ---------------------------------------------------------------------------


class MockLLM(BaseLLM):
    """不调用任何远端服务：直接回显检索到的上下文。"""

    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(self, prompts, stop=None, run_manager=None):  # type: ignore[override]
        from langchain_core.outputs import Generation, LLMResult

        results = []
        for prompt in prompts:
            content = prompt.text if hasattr(prompt, "text") else str(prompt)
            if "上下文" in content:
                start = content.find("上下文：")
                end = content.find("\n\n问题：")
                if start != -1 and end != -1:
                    context_text = content[start + 5 : end].strip()
                    if context_text:
                        results.append(context_text[:500])
                        continue
                results.append(f"根据知识库内容回答：\n\n{content[:300]}")
            else:
                results.append(f"直接回答：{content[:50]}...")
        return LLMResult(generations=[[Generation(text=r)] for r in results])


# ---------------------------------------------------------------------------
#  单例与锁
# ---------------------------------------------------------------------------

_embeddings_instance: Any = None
_embeddings_lock = threading.Lock()

_query_llm_instance: Any = None
_query_llm_lock = threading.Lock()

_vision_llm_instance: Any = None
_vision_llm_lock = threading.Lock()


def reset_model_singletons() -> None:
    """测试用：清空模型单例。"""
    global _embeddings_instance, _query_llm_instance, _vision_llm_instance
    with _embeddings_lock, _query_llm_lock, _vision_llm_lock, _llm_lock, _thinking_lock:
        _embeddings_instance = None
        _query_llm_instance = None
        _vision_llm_instance = None
        _llm_instances.clear()
        _thinking_instances.clear()


def _openai_compatible(model: str, api_key: str, base_url: str, **kwargs: Any):
    """构造指向任意 OpenAI 兼容端点的 Chat 模型。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        temperature=0,
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url,
        **kwargs,
    )


# ---------------------------------------------------------------------------
#  Embeddings
# ---------------------------------------------------------------------------


def get_embeddings() -> Any:
    """按后端返回嵌入模型（带 Redis 缓存包装）。"""
    global _embeddings_instance
    if _embeddings_instance is not None:
        return _embeddings_instance

    with _embeddings_lock:
        if _embeddings_instance is not None:
            return _embeddings_instance
        _embeddings_instance = _build_embeddings()
        return _embeddings_instance


def _build_embeddings() -> Any:
    model = get_settings().model

    if model.embedding_model == "local-tfidf":
        logger.info("使用本地 TF-IDF 嵌入模型（768 维）")
        return SimpleTFIDFEmbeddings()

    backend = model.backend
    if backend == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return wrap_with_cache(
            OllamaEmbeddings(model=model.embedding_model, base_url=model.ollama_base_url)
        )
    if backend == "openai":
        from langchain_openai import OpenAIEmbeddings

        return wrap_with_cache(
            OpenAIEmbeddings(
                model=model.embedding_model,
                api_key=model.openai_api_key,  # type: ignore[arg-type]
            )
        )
    if backend == "zhipu":
        logger.info(f"使用智谱嵌入服务: {model.embedding_model}")
        return wrap_with_cache(
            OpenAICompatEmbeddings(
                model.embedding_model, model.zhipu_api_key, model.zhipu_base_url
            )
        )
    if backend == "baidu_qianfan":
        from langchain_qianfan import QianfanEmbeddingsEndpoint  # type: ignore

        return wrap_with_cache(
            QianfanEmbeddingsEndpoint(
                model=model.embedding_model, ak=model.qianfan_ak, sk=model.qianfan_sk
            )
        )
    if backend == "modelscope":
        logger.info(f"使用 ModelScope 嵌入服务: {model.embedding_model}")
        return wrap_with_cache(
            OpenAICompatEmbeddings(
                model.embedding_model, model.modelscope_api_key, model.modelscope_base_url
            )
        )
    if backend == "local":
        from langchain_community.embeddings import HuggingFaceEmbeddings

        return wrap_with_cache(HuggingFaceEmbeddings(model_name=model.embedding_model))
    if backend == "mock":
        logger.warning("使用 Mock 嵌入模型（128 维），仅用于测试")
        from langchain_community.embeddings import FakeEmbeddings

        return FakeEmbeddings(size=128)

    raise ValueError(f"不支持的模型后端: {backend}")


# ---------------------------------------------------------------------------
#  LLM
# ---------------------------------------------------------------------------

# 按模型名缓存实例：前端可切换模型，逐个缓存避免每次请求重建客户端。
# 数量受 `llm_choices` 白名单约束（通常 < 10），无需 LRU。
_llm_instances: dict[str, Any] = {}
_llm_lock = threading.Lock()


# ---------------------------------------------------------------------------
#  用户自定义端点（由 service 层注入）
# ---------------------------------------------------------------------------
#
# 分层规则禁止 `app/rag` 依赖 `app/repositories`，所以自定义端点**不能**在这里查库。
# 改成上层把数据推进来，这里只提供插槽 —— 与 `bind_sync_client()` 同一套路。
# 好处是 providers 依然可以脱离数据库单测。

#: 自定义模型 id 的统一前缀，用来和内置模型名区分开
CUSTOM_PREFIX = "custom:"

_custom_endpoints: dict[str, dict[str, Any]] = {}
_custom_lock = threading.Lock()


def set_custom_endpoints(items: list[dict[str, Any]]) -> None:
    """注入自定义端点清单（只接受已启用的）。

    ⚠️ **必须让受影响的实例缓存失效**：编辑端点后若继续复用旧实例，
    用户会遇到「密钥改了但请求还在用旧的」—— 而且完全不报错，极难排查。
    这里按 id 比对内容，只有真正变化（含新增/删除）的条目才清缓存，
    没动过的模型仍然命中缓存。
    """
    with _custom_lock:
        incoming = {
            f"{CUSTOM_PREFIX}{item['id']}": dict(item)
            for item in items
            if item.get("id") and item.get("enabled", True)
        }
        stale = set(_custom_endpoints) - set(incoming)
        for key, value in incoming.items():
            if _custom_endpoints.get(key) != value:
                stale.add(key)
        _custom_endpoints.clear()
        _custom_endpoints.update(incoming)

    if stale:
        with _llm_lock:
            for key in stale:
                _llm_instances.pop(key, None)
        logger.info(f"自定义模型端点已更新，失效实例缓存 {len(stale)} 个")


def custom_model_ids() -> list[str]:
    with _custom_lock:
        return list(_custom_endpoints)


def get_custom_endpoint(model: str) -> Optional[dict[str, Any]]:
    with _custom_lock:
        item = _custom_endpoints.get(model)
    return dict(item) if item else None


def is_custom_model(model: str) -> bool:
    return bool(model) and model.startswith(CUSTOM_PREFIX)


def is_model_available(model: str) -> bool:
    """模型是否可用：内置白名单 **或** 已启用的自定义端点。

    这是对外唯一的可用性判据 —— `/ask` 的请求校验与工作流都走它，
    避免出现「这边认、那边不认」的分叉。
    """
    if not model:
        return False
    if is_custom_model(model):
        return model in custom_model_ids()
    return get_settings().model.is_llm_allowed(model)


def describe_available_models() -> str:
    return "、".join(item["id"] for item in list_llm_models())


def resolve_model(model: Optional[str] = None) -> str:
    """把请求里的 model 解析为实际使用的模型名，并做可用性校验。

    校验是必须的：否则客户端可把任意模型名透传到上游 API。
    传入非法模型时**不静默回退**（静默回退会让用户以为切成功了），而是抛错。
    """
    settings = get_settings()
    if not model:
        return settings.model.llm_model

    if is_custom_model(model):
        if model not in custom_model_ids():
            raise ValueError(f"自定义模型「{model}」不存在或已停用")
        return model

    if not settings.model.is_llm_allowed(model):
        allowed = settings.model.available_llm_models
        raise ValueError(f"不支持的模型: {model}；可选：{allowed}")
    return model


def get_llm(model: Optional[str] = None) -> Any:
    """获取 LLM 实例。

    `model` 为空时用配置的默认模型；指定时按名下缓存。
    """
    resolved = resolve_model(model)
    cached = _llm_instances.get(resolved)
    if cached is not None:
        return cached
    with _llm_lock:
        if resolved not in _llm_instances:
            _llm_instances[resolved] = _build_llm(resolved)
        return _llm_instances[resolved]


def _openai_options() -> dict[str, Any]:
    """OpenAI 兼容后端的公共可选参数。

    只用于 Chat 模型构造（`_build_llm`），不会传给 Ollama / 千帆等原生 SDK 分支。
    """
    settings = get_settings()
    options: dict[str, Any] = {}
    if settings.model.max_tokens:
        options["max_tokens"] = settings.model.max_tokens

    # 超时是**必须**的：没有它，上游一旦挂起就无限等，而界面上
    # 「正常但缓慢的生成」与「已经卡死」完全无法区分。
    timeout = float(getattr(settings.model, "llm_timeout_seconds", 0) or 0)
    if timeout > 0:
        options["timeout"] = timeout
    # 显式设定重试次数，避免 langchain 默认的 2 次把最坏等待放大三倍
    options["max_retries"] = int(getattr(settings.model, "llm_max_retries", 1))
    return options


def _build_llm(model_name: str) -> Any:
    model = get_settings().model
    backend = model.backend
    extra = _openai_options()

    # 自定义端点优先：它们与内置后端无关，永远走 OpenAI 兼容协议
    endpoint = get_custom_endpoint(model_name)
    if endpoint is not None:
        logger.info(
            f"使用自定义模型: {endpoint.get('name')} → {endpoint.get('model')} "
            f"@ {endpoint.get('base_url')}"
        )
        return _openai_compatible(
            endpoint["model"], endpoint["api_key"], endpoint["base_url"], **extra
        )

    if backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            temperature=0,
            base_url=model.ollama_base_url,
            **({"num_predict": model.max_tokens} if model.max_tokens else {}),
        )
    if backend == "openai":
        return _openai_compatible(
            model_name,
            model.openai_api_key,
            model.openai_base_url or "https://api.openai.com/v1",
            **extra,
        )
    if backend == "zhipu":
        logger.info(f"使用智谱 LLM: {model_name}")
        return _openai_compatible(
            model_name, model.zhipu_api_key, model.zhipu_base_url, **extra
        )
    if backend == "baidu_qianfan":
        from langchain_qianfan import QianfanChatEndpoint  # type: ignore

        return QianfanChatEndpoint(
            model=model_name, ak=model.qianfan_ak, sk=model.qianfan_sk
        )
    if backend == "modelscope":
        logger.info(f"使用 ModelScope LLM: {model_name}")
        return _openai_compatible(
            model_name, model.modelscope_api_key, model.modelscope_base_url, **extra
        )
    if backend in ("local", "mock"):
        logger.info("使用本地 Mock LLM")
        return MockLLM()

    raise ValueError(f"不支持的模型后端: {backend}")


def list_llm_models() -> list[dict[str, Any]]:
    """供 `GET /models` 使用的模型清单（内置 + 自定义端点）。"""
    model = get_settings().model
    default = model.llm_model
    items = [
        {
            "id": name,
            "label": model.model_label(name) or name,
            "is_default": name == default,
            "custom": False,
            "provider": "",
        }
        for name in model.available_llm_models
    ]

    with _custom_lock:
        custom = list(_custom_endpoints.values())
    # 按创建顺序稳定输出，避免前端选择器的顺序每次刷新都变
    for endpoint in sorted(custom, key=lambda e: e.get("created_at") or 0):
        items.append(
            {
                "id": f"{CUSTOM_PREFIX}{endpoint['id']}",
                # 标签里带上上游模型名：用户配了多个端点时需要一眼分辨
                "label": f"{endpoint.get('name') or '自定义'}（{endpoint.get('model')}）",
                "is_default": False,
                "custom": True,
                "provider": endpoint.get("name") or "",
            }
        )
    return items


def get_query_llm() -> Any:
    """轻量模型，用于意图分类、事实抽取等内部调用。

    未单独配置 `QUERY_MODEL` 时回落为主 LLM。
    """
    global _query_llm_instance
    model = get_settings().model

    if model.backend in ("local", "mock"):
        return get_llm()
    if model.query_model == model.llm_model:
        return get_llm()

    if _query_llm_instance is not None:
        return _query_llm_instance
    with _query_llm_lock:
        if _query_llm_instance is None:
            try:
                if model.backend == "zhipu":
                    _query_llm_instance = _openai_compatible(
                        model.query_model, model.zhipu_api_key, model.zhipu_base_url
                    )
                elif model.backend == "modelscope":
                    _query_llm_instance = _openai_compatible(
                        model.query_model, model.modelscope_api_key, model.modelscope_base_url
                    )
                else:
                    _query_llm_instance = get_llm()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"轻量模型初始化失败，回落主 LLM: {exc}")
                _query_llm_instance = get_llm()
        return _query_llm_instance


# ---------------------------------------------------------------------------
#  Vision
# ---------------------------------------------------------------------------


def get_vision_llm() -> Any:
    """多模态 LLM，用于图片问答。进程内单例。"""
    global _vision_llm_instance
    if _vision_llm_instance is not None:
        return _vision_llm_instance
    with _vision_llm_lock:
        if _vision_llm_instance is None:
            _vision_llm_instance = _build_vision_llm()
        return _vision_llm_instance


def _build_vision_llm() -> Any:
    model = get_settings().model
    backend = model.backend

    if backend in ("local", "mock"):
        logger.warning("该后端无视觉能力，图片问答回落为文本 LLM")
        return get_llm()
    if backend == "zhipu":
        logger.info(f"使用智谱 Vision LLM: {model.vision_model}")
        return _openai_compatible(
            model.vision_model, model.zhipu_api_key, model.zhipu_base_url, max_retries=2
        )
    if backend == "modelscope":
        logger.info(f"使用 ModelScope Vision LLM: {model.vision_model}")
        return _openai_compatible(
            model.vision_model,
            model.modelscope_api_key,
            model.modelscope_base_url,
            max_retries=2,
        )
    if backend == "openai":
        return _openai_compatible(
            model.vision_model,
            model.openai_api_key,
            model.openai_base_url or "https://api.openai.com/v1",
        )
    if backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model.vision_model, temperature=0, base_url=model.ollama_base_url)

    logger.warning(f"后端 {backend} 未配置视觉模型，回落为文本 LLM")
    return get_llm()


# ---------------------------------------------------------------------------
#  深度思考（推理流）
# ---------------------------------------------------------------------------
#
# `ChatOpenAI` 会丢弃第三方 provider 的 `reasoning_content`（其源码注释里
# 写得很直白：非 OpenAI 规范的字段不提取）。所以推理这一条路改用
# `app/rag/thinking.py` 的轻量客户端 —— 只用 openai SDK 的流式接口，
# 把 reasoning / content 两条通道分开。其余路径不变。

_thinking_instances: dict[str, Any] = {}
_thinking_lock = threading.Lock()


def is_thinking_capable(model: str) -> bool:
    """该模型是否会把推理过程流出来。

    判据只有 `ModelSettings.is_thinking_capable` 一处 —— 免得前端、节点、
    接口各有一套「哪些模型能思考」的名单，最后互相矛盾。
    """
    return get_settings().model.is_thinking_capable(model)


def get_thinking_llm(model: Optional[str] = None) -> Any:
    """获取带推理通道的 LLM。

    不支持的模型后端（local / mock / ollama）直接回落普通 LLM：
    此时 `thinking=true` 会被忽略，界面上不会出现「已思考」面板 ——
    这比编一段假的推理过程诚实得多。
    """
    settings = get_settings()
    model = model or settings.model.thinking_model

    cached = _thinking_instances.get(model)
    if cached is not None:
        return cached

    with _thinking_lock:
        if model not in _thinking_instances:
            _thinking_instances[model] = _build_thinking_llm(model)
        return _thinking_instances[model]


def _build_thinking_llm(model_name: str) -> Any:
    from app.rag.thinking import ThinkingChat

    settings = get_settings()
    model = settings.model
    timeout = float(getattr(model, "llm_timeout_seconds", 0) or 600)

    if model.backend == "zhipu":
        return ThinkingChat(
            model=model_name,
            api_key=model.zhipu_api_key,
            base_url=model.zhipu_base_url,
            timeout=timeout,
            max_tokens=model.max_tokens,
        )
    if model.backend == "openai":
        return ThinkingChat(
            model=model_name,
            api_key=model.openai_api_key,
            base_url=model.openai_base_url or "https://api.openai.com/v1",
            timeout=timeout,
            max_tokens=model.max_tokens,
        )
    if model.backend == "modelscope":
        return ThinkingChat(
            model=model_name,
            api_key=model.modelscope_api_key,
            base_url=model.modelscope_base_url,
            timeout=timeout,
            max_tokens=model.max_tokens,
        )

    logger.info(f"后端 {model.backend} 无推理通道，thinking 请求回落普通 LLM")
    return get_llm(model_name)
