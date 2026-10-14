"""来源可达性标记（设计 §10.2 第 3 条）。

「真实可访问」这个要求，光有真实 URL 还不够 —— 页面可能已经 404。
这里对每个来源发一次 `HEAD`，把结果写回 `sources[].reachable`：

  * `True`  —— 2xx/3xx，链接可用
  * `False` —— 4xx/5xx 或连接失败，前端标注「可能失效」
  * `None`  —— **未校验**（超时、网络受限、被反爬拦截）

为什么把「不确定」单独区分出来：网络不通与链接失效是两回事。
把超时当成 `False` 会让一批好链接被标成坏链接（用户会失去对标注的信任），
把超时当成 `True` 则等于没校验。**宁可说不知道**。

并发 + 缓存是必需的：一次搜索可能返回 5-6 条，串行 3 秒超时最坏要 18 秒，
用户会以为界面卡死了。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["check_sources", "clear_cache", "probe_url"]

_HEAD_TIMEOUT = 3.0
_MAX_WORKERS = 6
_MAX_SOURCES = 8
_CACHE_TTL = 1800.0  # 半小时内同一个 URL 不重复探测
_CACHE_LIMIT = 512

_cache: dict[str, tuple[float, Optional[bool]]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """测试用。"""
    with _cache_lock:
        _cache.clear()


def _cache_get(url: str) -> tuple[bool, Optional[bool]]:
    with _cache_lock:
        hit = _cache.get(url)
    if not hit:
        return False, None
    expires_at, value = hit
    if expires_at < time.time():
        with _cache_lock:
            _cache.pop(url, None)
        return False, None
    return True, value


def _cache_put(url: str, value: Optional[bool]) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            # 简单淘汰：清掉最早插入的一批
            for old in list(_cache)[: _CACHE_LIMIT // 2]:
                _cache.pop(old, None)
        _cache[url] = (time.time() + _CACHE_TTL, value)


def probe_url(url: str, *, timeout: float = _HEAD_TIMEOUT) -> Optional[bool]:
    """探测单个 URL。返回 True/False，不确定时返回 None。"""
    href = (url or "").strip()
    if not href.lower().startswith(("http://", "https://")):
        return None

    found, cached = _cache_get(href)
    if found:
        return cached

    value: Optional[bool] = None
    try:
        import httpx

        # trust_env=False：本机有代理环境变量，走代理会让内网/被墙判断失真
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; KnowledgeBaseBot/1.0)"},
        ) as client:
            response = client.head(href)
            if response.status_code in (403, 405, 501):
                # 有些站点不支持 HEAD / 拦截爬虫 —— 不能据此判定链接失效
                value = None
            else:
                value = response.status_code < 400
    except Exception as exc:  # noqa: BLE001 - 探测失败绝不影响主流程
        logger.debug(f"可达性探测失败 {href[:80]}: {exc}")
        value = None

    _cache_put(href, value)
    return value


def check_sources(
    sources: Optional[list[dict[str, Any]]],
    *,
    enabled: bool = True,
    limit: int = _MAX_SOURCES,
) -> list[dict[str, Any]]:
    """给来源补上 `reachable` 字段（并发探测 + 缓存）。

    超过 `limit` 条的来源不再探测（保持 `reachable=None`）——
    一次搜索返回几十条时，探测的收益远小于它带来的等待。
    """
    items = [dict(item) for item in (sources or []) if isinstance(item, dict)]
    if not items:
        return items
    if not enabled:
        for item in items:
            item.setdefault("reachable", None)
        return items

    targets = [
        item for item in items[:limit] if str(item.get("href") or "").strip()
    ]
    if not targets:
        for item in items:
            item.setdefault("reachable", None)
        return items

    started = time.time()
    try:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(targets))) as pool:
            results = list(pool.map(lambda item: probe_url(str(item.get("href"))), targets))
    except Exception as exc:  # noqa: BLE001 - 线程池异常也不能影响主流程
        logger.warning(f"可达性探测整体失败，全部按未校验处理: {exc}")
        results = [None] * len(targets)

    for item, value in zip(targets, results):
        item["reachable"] = value
    for item in items:
        item.setdefault("reachable", None)

    checked = sum(1 for v in results if v is not None)
    dead = sum(1 for v in results if v is False)
    logger.info(
        f"[可达性] 探测 {len(targets)} 条，已判定 {checked} 条（其中失效 {dead} 条），"
        f"耗时 {time.time() - started:.1f}s"
    )
    return items
