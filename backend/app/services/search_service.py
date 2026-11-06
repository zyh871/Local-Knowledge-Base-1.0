"""
联网搜索的供应商注册表与配置读写。

设计要点
  * **密钥优先级**：界面里存的（DB）> 环境变量。界面配置优先，
    因为它是用户刚填的、期望立刻生效的那一份。
  * **免密钥选项**：DuckDuckGo 的 Instant Answer API 不需要 key，
    先保证「联网搜索」这个功能开箱可用；想要更全的结果再换 Tavily / Serper。
  * **不保存明文回显**：接口只回传末 4 位，和模型端点密钥的处理一致。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger
from app.repositories import search_repo
from app.tools.web_search import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    SearchProvider,
    configure_runtime,
    provider_by_id,
)

logger = get_logger(__name__)

#: 配置项名
KEY_PROVIDER = "provider"
KEY_API_KEY = "api_key"


def current_provider_id() -> str:
    saved = search_repo.get_value(KEY_PROVIDER, "")
    known = {p.id for p in PROVIDERS}
    if saved and saved in known:
        return saved
    # 界面上没配过时，看环境变量指向哪家：有 Tavily key 就默认用它
    for prov in PROVIDERS:
        if prov.env_key and os.getenv(prov.env_key):
            return prov.id
    return DEFAULT_PROVIDER


def resolve_api_key(provider_id: Optional[str] = None) -> str:
    """取实际使用的密钥：界面配置 > 环境变量。"""
    prov = provider_by_id(provider_id or current_provider_id())
    stored = search_repo.get_value(KEY_API_KEY, "")
    if stored:
        return stored
    if prov.env_key:
        return os.getenv(prov.env_key, "")
    return ""


def current_config() -> dict[str, Any]:
    prov = provider_by_id(current_provider_id())
    key = resolve_api_key(prov.id)
    return {
        "provider": prov.id,
        "provider_name": prov.name,
        "requires_key": prov.requires_key,
        "configured": bool(key) or not prov.requires_key,
        "key_masked": (key[-4:] if key else ""),
        "source": "app" if search_repo.get_value(KEY_API_KEY, "") else ("env" if key else "none"),
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "requires_key": p.requires_key,
                "docs": p.docs,
                "hint": p.hint,
                "free_tier": p.free_tier,
                "configured": bool(resolve_api_key(p.id)) or not p.requires_key,
            }
            for p in PROVIDERS
        ],
    }


def save_config(provider_id: str, api_key: str) -> dict[str, Any]:
    prov = provider_by_id(provider_id)
    key = (api_key or "").strip()
    if prov.requires_key and not key:
        # 已经有可用密钥（界面/设置/环境变量任一）就直接换供应商，
        # 不用让用户把密钥再贴一遍 —— 多余的一步就是多余的流失。
        if not resolve_api_key(prov.id):
            raise ValueError(f"{prov.name} 需要 API Key")
    search_repo.set_value(KEY_PROVIDER, prov.id)
    if key:
        search_repo.set_value(KEY_API_KEY, key)
    elif not prov.requires_key:
        search_repo.delete_value(KEY_API_KEY)
    logger.info(f"已保存联网搜索配置: {prov.id}（密钥来源：界面）")
    apply_runtime_config()
    return current_config()


def clear_config() -> dict[str, Any]:
    search_repo.delete_value(KEY_PROVIDER)
    search_repo.delete_value(KEY_API_KEY)
    logger.info("已清除联网搜索配置")
    apply_runtime_config()
    return current_config()


def apply_runtime_config() -> None:
    """把当前生效的供应商与密钥推给 tools 层。

    分层约束：tools 不能读数据库，所以由 service 读出来注入。
    启动时调用一次即可，之后界面每次保存也会再推一次。
    """
    prov = provider_by_id(current_provider_id())
    configure_runtime(prov.id, resolve_api_key(prov.id))
