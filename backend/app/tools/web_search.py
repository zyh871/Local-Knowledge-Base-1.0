"""
联网搜索工具 — Tavily API 版本

使用 Tavily Search API 获取高质量、结构化的搜索结果。
特性：
- 异步 HTTP 客户端 (httpx)
- 自动重试 + 指数退避
- 内存级 LRU 缓存
- 智能触发判断
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

_CACHE: Dict[str, Tuple[float, Dict]] = {}
_CACHE_TTL = 300  # 5 分钟缓存
_CACHE_LOCK = asyncio.Lock()

# 全局异步客户端
_http_client: Optional[httpx.AsyncClient] = None


async def _get_client() -> httpx.AsyncClient:
    """Get or create the global async HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def close_client():
    """Close the global HTTP client (call on shutdown)."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def _cache_key(query: str, max_results: int) -> str:
    return hashlib.md5(f"{query.lower().strip()}|{max_results}".encode("utf-8")).hexdigest()


async def _cache_get(key: str) -> Optional[Dict]:
    item = _CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return val


async def _cache_set(key: str, val: Dict) -> None:
    _CACHE[key] = (time.time(), val)
    if len(_CACHE) > 256:
        # 清理最旧的64条
        sorted_keys = sorted(_CACHE.keys(), key=lambda k: _CACHE[k][0])
        for k in sorted_keys[:64]:
            _CACHE.pop(k, None)


async def search_async(
    query: str,
    max_results: int = 5,
    timeout: int = 15,
    search_depth: str = "basic",
) -> Dict[str, any]:
    """
    使用 Tavily API 进行联网搜索（异步版本）。

    Returns:
        {
            "query": str,
            "instant_answer": str | None,
            "results": [{title, snippet, href, source}],
            "success": bool,
            "error": str | None,
            "engines_used": [str],
        }
    """
    if not query or not query.strip():
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False, "error": "empty query", "engines_used": []
        }

    key = _cache_key(query, max_results)
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    api_key = TAVILY_API_KEY or os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        logger.error("TAVILY_API_KEY 未配置，无法执行联网搜索")
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False, "error": "TAVILY_API_KEY not configured", "engines_used": []
        }

    payload = {
        "api_key": api_key,
        "query": query.strip(),
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }

    client = await _get_client()
    last_exc: Optional[Exception] = None

    for attempt in range(3):
        try:
            resp = await client.post(TAVILY_API_URL, json=payload, timeout=timeout)
            if resp.status_code == 429:
                await asyncio.sleep(0.8 * (2 ** attempt))
                continue
            if resp.status_code != 200:
                logger.warning(f"Tavily API returned {resp.status_code}: {resp.text[:200]}")
                last_exc = Exception(f"HTTP {resp.status_code}")
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            data = resp.json()
            break
        except httpx.TimeoutException as e:
            last_exc = e
            await asyncio.sleep(0.8 * (2 ** attempt))
        except Exception as e:
            last_exc = e
            await asyncio.sleep(0.5 * (2 ** attempt))
    else:
        logger.error(f"Tavily search failed after 3 attempts: {last_exc}")
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False, "error": str(last_exc) if last_exc else "unknown error", "engines_used": []
        }

    # 解析 Tavily 响应
    results: List[Dict[str, str]] = []
    instant_answer: Optional[str] = None

    answer_text = data.get("answer")
    if answer_text and str(answer_text).strip():
        instant_answer = str(answer_text).strip()[:3000]

    for r in data.get("results", []):
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        if title and url:
            results.append({
                "title": title,
                "snippet": content,
                "href": url,
                "source": "tavily",
            })

    payload = {
        "query": query,
        "instant_answer": instant_answer,
        "results": results,
        "success": len(results) > 0 or instant_answer is not None,
        "error": None,
        "engines_used": ["tavily"],
    }
    await _cache_set(key, payload)
    return payload


def search(
    query: str,
    max_results: int = 5,
    timeout: int = 15,
    search_depth: str = "basic",
) -> Dict[str, any]:
    """
    同步包装器 - 调用异步搜索函数。
    用于向后兼容，但推荐使用 search_async。
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 如果在异步环境中调用，创建新事件循环
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, search_async(query, max_results, timeout, search_depth))
                return future.result()
        else:
            return loop.run_until_complete(search_async(query, max_results, timeout, search_depth))
    except Exception as e:
        logger.error(f"Search wrapper failed: {e}")
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False, "error": str(e), "engines_used": []
        }


def build_search_context(
    search_result: Dict[str, any],
    max_chars: int = 3500,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    将搜索结果拼装为 LLM 上下文，返回 (context_text, sources_list)。
    sources_list 保留完整的元信息（title, href, snippet），供前端展示。
    """
    lines: List[str] = []
    sources: List[Dict[str, str]] = []

    if search_result.get("instant_answer"):
        lines.append(f"【即时回答】\n{search_result['instant_answer']}\n")

    for i, r in enumerate(search_result.get("results", []), 1):
        line = f"【来源 {i}】"
        if r.get("title"):
            line += f" {r['title']}"
        if r.get("snippet"):
            line += f"\n{r['snippet']}"
        if r.get("href"):
            line += f"\n链接: {r['href']}"
        lines.append(line)
        sources.append(r)

    joined = "\n\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n（搜索结果已截断）"

    return joined, sources


# --------------------- 智能触发 ---------------------

STRONG_TRIGGERS = [
    "搜索", "搜一下", "查一下", "查找", "百度", "谷歌", "google", "bing",
    "联网", "网上", "网络", "tavily",
]

TIME_TRIGGERS = [
    "最新", "最近", "现在", "当前", "今天", "今日", "本周", "本月", "今年",
    "新闻", "热点", "动态", "行情", "走势", "实时",
    "2024", "2025", "2026", "2027", "2028", "2029", "2030",
    "now", "today", "current", "latest", "news", "breaking",
]

FACT_TRIGGERS = [
    "天气", "气温", "温度", "pm2.5", "空气质量",
    "股价", "股票", "价格", "金价", "黄金价格", "银价", "油价",
    "汇率", "比分", "赛果",
    "weather", "stock", "price", "score",
]

QUESTION_STARTERS = [
    "什么是", "是什么", "谁是", "是谁", "怎么样", "如何",
    "why", "how", "what", "who", "when", "where",
]


def should_search(query: str) -> bool:
    """智能判断用户问题是否需要联网搜索。默认返回 True（默认开启搜索）。"""
    if not query:
        return False
    q = query.lower().strip()

    if any(t in q for t in STRONG_TRIGGERS):
        return True
    if any(t in q for t in TIME_TRIGGERS):
        return True
    if any(t in q for t in FACT_TRIGGERS):
        return True
    if len(q) <= 15 and any(q.startswith(s) for s in QUESTION_STARTERS):
        return True

    return True
