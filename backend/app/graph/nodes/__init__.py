"""
图节点。

依赖注入方式：节点签名统一为 `(state, deps)`，由 `build.py` 用闭包绑定 deps。
这样节点本身是纯函数（可单测），依赖（chain / settings / redis）由装配层提供。

文件与节点对应关系：
    prepare.py     ingest, classify_intent
    retrieval.py   retrieve, web_search, file_qa_retrieve
    respond.py     long_text, clarify, build_prompt, generate
    persist.py     persist
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Deps:
    """节点执行所需的运行时依赖。"""

    chain: Any
    settings: Any
    redis: Any = None

    @property
    def llm(self) -> Any:
        """LLM 实例。

        懒初始化：图在启动时构建（此时可能尚未预热），
        因此首次真正用到 LLM 的节点负责触发初始化。
        """
        if self.chain is not None and not getattr(self.chain, "is_ready", False):
            try:
                self.chain.ensure_initialized()
            except Exception:  # noqa: BLE001  交由下游的 None 判断兜底
                pass
        return getattr(self.chain, "_llm", None)

    @property
    def window_size(self) -> int:
        return self.settings.retrieval.conversation_window_size

    @property
    def locked_window_size(self) -> int:
        return self.settings.retrieval.locked_window_size


def bind(fn: Callable[..., dict], deps: Deps) -> Callable[[dict], dict]:
    """把 deps 绑定进节点，得到 LangGraph 期望的 `(state) -> update` 签名。"""

    def _node(state: dict) -> dict:
        return fn(state, deps)

    _node.__name__ = getattr(fn, "__name__", "node")
    return _node
