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
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ---------------------------------------------------------------------------
#  供应商注册表（放在 tools 层：service 可以 import tools，反向不允许）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchProvider:
    id: str
    name: str
    requires_key: bool
    docs: str
    hint: str
    env_key: str = ""
    free_tier: str = ""


#: 可选供应商。顺序即界面展示顺序。
PROVIDERS: tuple[SearchProvider, ...] = (
    SearchProvider(
        id="duckduckgo",
        name="DuckDuckGo（免密钥）",
        requires_key=False,
        docs="https://duckduckgo.com",
        hint="开箱即用、无需注册；结果来自 Instant Answer，覆盖面比付费 API 窄。部分网络环境无法直连，测不通就换 Tavily/Serper。",
        free_tier="完全免费",
    ),
    SearchProvider(
        id="tavily",
        name="Tavily",
        requires_key=True,
        docs="https://app.tavily.com",
        hint="面向 AI 检索优化，返回摘要 + 来源，最适合问答场景。",
        env_key="TAVILY_API_KEY",
        free_tier="每月 1000 次免费额度",
    ),
    SearchProvider(
        id="bocha",
        name="博查 Bocha（中文优化）",
        requires_key=True,
        docs="https://open.bochaai.com",
        hint="国内可直连，中文网页覆盖好、摘要较长，适合中文问答与资讯检索。",
        env_key="BOCHA_API_KEY",
        free_tier="新用户有免费额度",
    ),
    SearchProvider(
        id="serper",
        name="Serper（Google 搜索）",
        requires_key=True,
        docs="https://serper.dev",
        hint="走 Google 搜索结果，覆盖面广、时效性好。",
        env_key="SERPER_API_KEY",
        free_tier="每月 2500 次免费额度",
    ),
)

_BY_ID = {p.id: p for p in PROVIDERS}
DEFAULT_PROVIDER = "duckduckgo"


def provider_by_id(provider_id: str) -> SearchProvider:
    return _BY_ID.get(provider_id, _BY_ID[DEFAULT_PROVIDER])


#: 运行时配置（由 service 注入）。
#:
# ★ tools 层不能直接读数据库 —— 分层约束里 tools 禁依赖 repositories。
#   所以「用户在界面上填的密钥」由 service 层读出来，通过这里推给工具。
#   环境变量仍然作为兜底，保证不配置界面也能用。
_RUNTIME: Dict[str, str] = {"provider": "", "api_key": ""}


def configure_runtime(provider: str = "", api_key: str = "") -> None:
    """由 service 层调用：把当前生效的供应商与密钥推进来。"""
    _RUNTIME["provider"] = provider or ""
    _RUNTIME["api_key"] = api_key or ""


def runtime_provider_id() -> str:
    pid = _RUNTIME.get("provider") or ""
    if pid in _BY_ID:
        return pid
    # 界面上没配过时：谁有密钥就用谁（设置里的 .env 也算）
    for prov in PROVIDERS:
        if _settings_key(prov.id) or (prov.env_key and os.getenv(prov.env_key)):
            return prov.id
    return DEFAULT_PROVIDER


def _settings_key(provider_id: str) -> str:
    """从应用设置里取密钥。

    ★ 为什么要多这一步：`os.getenv()` **读不到 .env 文件里的键** ——
      pydantic-settings 是自己解析文件的，键并没有进进程环境。
      所以用户明明在 backend/.env 里写了 TAVILY_API_KEY，工具却报
      「未配置」。这里直接问设置对象，才拿得到。
    """
    try:
        from app.core.config import get_settings

        model = get_settings().model
        if provider_id == "tavily":
            return getattr(model, "tavily_api_key", "") or ""
        if provider_id == "serper":
            return getattr(model, "serper_api_key", "") or ""
    except Exception:  # noqa: BLE001 - 取不到就当没有
        return ""
    return ""


def runtime_api_key(provider_id: str = "") -> str:
    pid = provider_id or runtime_provider_id()
    prov = provider_by_id(pid)
    key = _RUNTIME.get("api_key") or ""
    if key:
        return key
    key = _settings_key(pid)
    if key:
        return key
    if prov.env_key:
        return os.getenv(prov.env_key, "")
    return ""


_CACHE: Dict[str, Tuple[float, Dict]] = {}
_CACHE_TTL = 300  # 5 分钟缓存
_CACHE_LOCK = asyncio.Lock()

# 全局异步客户端
_http_client: Optional[httpx.AsyncClient] = None
#: 直连客户端（绕过系统代理）—— 代理配错/不通时的兜底
_direct_client: Optional[httpx.AsyncClient] = None
#: 这两个客户端是**在哪个事件循环里创建的**。
#
# ★ httpx.AsyncClient 的连接池与 anyio 锁都绑定在创建它的循环上。
#   循环一关，客户端表面还活着（is_closed=False），但下一次请求就会抛
#   `RuntimeError: Event loop is closed`。工作流是同步调工具、每次 asyncio.run
#   一个新循环，正好踩中这个坑：第一次能搜，第二次必挂。
_http_client_loop: Optional[asyncio.AbstractEventLoop] = None
_direct_client_loop: Optional[asyncio.AbstractEventLoop] = None


def _current_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


async def _get_client() -> httpx.AsyncClient:
    """Get or create the async HTTP client **for the current event loop**."""
    global _http_client, _http_client_loop
    loop = _current_loop()
    if _http_client is None or _http_client.is_closed or _http_client_loop is not loop:
        if _http_client is not None and _http_client_loop is not loop:
            logger.info("事件循环已切换，重建 HTTP 客户端（旧连接池随旧循环作废）")
        _http_client = httpx.AsyncClient(timeout=30.0)
        _http_client_loop = loop
    return _http_client


async def _get_direct_client() -> httpx.AsyncClient:
    """直连客户端：trust_env=False，不吃 HTTP_PROXY/HTTPS_PROXY。"""
    global _direct_client, _direct_client_loop
    loop = _current_loop()
    if _direct_client is None or _direct_client.is_closed or _direct_client_loop is not loop:
        _direct_client = httpx.AsyncClient(timeout=30.0, trust_env=False)
        _direct_client_loop = loop
    return _direct_client


async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """发请求；代理报错/网关异常时自动改走直连再试一次。

    本机常见的坑：HTTP_PROXY 指向一个不通的代理，搜索请求全被它吞掉，
    用户看到的是「搜索失败」，其实是代理的问题。
    """
    client = await _get_client()
    try:
        try:
            resp = await client.request(method, url, **kwargs)
        except RuntimeError as exc:
            # 兜底：万一还有别的入口把旧循环的客户端递了过来
            if "Event loop is closed" not in str(exc):
                raise
            global _http_client, _http_client_loop
            _http_client, _http_client_loop = None, None
            client = await _get_client()
            resp = await client.request(method, url, **kwargs)
        if resp.status_code in (407, 502, 503, 504):
            direct = await _get_direct_client()
            try:
                return await direct.request(method, url, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"直连也失败: {exc}")
                return resp
        return resp
    except Exception:
        direct = await _get_direct_client()
        return await direct.request(method, url, **kwargs)


async def close_client():
    """Close the global HTTP client (call on shutdown)."""
    global _http_client, _direct_client, _http_client_loop, _direct_client_loop
    if _http_client and not _http_client.is_closed:
        try:
            await _http_client.aclose()
        except RuntimeError:
            # 循环已经关了，连接本来也没了，直接丢弃
            pass
    _http_client, _http_client_loop = None, None
    if _direct_client and not _direct_client.is_closed:
        try:
            await _direct_client.aclose()
        except RuntimeError:
            pass
    _direct_client, _direct_client_loop = None, None
    stop_sync_loop()


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

    # ── 选供应商：界面里配的 > 环境变量 ─────────────────────────
    #
    # 以前这里只认 TAVILY_API_KEY，用户得自己改 .env 再重启。
    # 现在密钥可以在「用户菜单 → 联网搜索」里填，存库、即时生效。
    provider_id = runtime_provider_id()
    prov = provider_by_id(provider_id)
    api_key = runtime_api_key(provider_id)

    if prov.requires_key and not api_key:
        logger.error(f"{prov.name} 未配置 API Key，无法执行联网搜索")
        return {
            "query": query,
            "instant_answer": None,
            "results": [],
            "success": False,
            "error": f"{prov.name} 还没有配置 API Key —— 打开「用户菜单 → 联网搜索」填一下即可（免密钥方案也有）",
            "error_code": "search_key_missing",
            "action": "configure_search",
            "provider": provider_id,
            "engines_used": [],
        }

    key = _cache_key(f"{provider_id}|{query}", max_results)
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    if provider_id == "bocha":
        payload = await _do_search_bocha(query, api_key, max_results, timeout)
    elif provider_id == "serper":
        payload = await _do_search_serper(query, api_key, max_results, timeout)
    elif provider_id == "duckduckgo":
        payload = await _do_search_duckduckgo(query, max_results, timeout)
    else:
        payload = await _do_search_tavily(query, api_key, max_results, search_depth, timeout)

    await _cache_set(key, payload)
    return payload


# ---------------------------------------------------------------------------
#  各供应商实现
# ---------------------------------------------------------------------------


#: 博查 Web Search：Response 兼容 Bing Search API
BOCHA_API_URL = "https://api.bochaai.com/v1/web-search"


async def _do_search_bocha(
    query: str,
    api_key: str,
    max_results: int,
    timeout: int,
) -> Dict:
    """博查：国内直连，中文覆盖好，返回带长摘要的网页结果。

    响应形状（Bing 兼容）：
        {"data": {"webPages": {"value": [
            {"name", "url", "snippet", "summary", "siteName", "datePublished"}
        ]}}}
    """
    payload = {
        "query": query.strip(),
        # noLimit 让搜索引擎自己改写时间范围；写死 oneYear 常导致搜不到东西
        "freshness": "noLimit",
        "summary": True,
        "count": max(1, min(int(max_results), 50)),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_exc: Optional[Exception] = None
    data: Dict = {}
    for attempt in range(3):
        try:
            resp = await _request(
                "POST", BOCHA_API_URL, json=payload, headers=headers, timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                break
            # 401 密钥无效 / 403 无权限 / 429 限流 —— 前两个重试没意义
            if resp.status_code in (401, 402, 403):
                detail = _bocha_error(resp)
                logger.warning(f"博查拒绝请求（{resp.status_code}）: {detail}")
                return {
                    "query": query, "instant_answer": None, "results": [],
                    "success": False, "error": detail, "provider": "bocha",
                    "engines_used": [],
                }
            last_exc = Exception(f"HTTP {resp.status_code}")
            await asyncio.sleep(0.5 * (2 ** attempt))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await asyncio.sleep(0.5 * (2 ** attempt))
    else:
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False,
            "error": f"博查搜索失败：{last_exc}",
            "provider": "bocha", "engines_used": [],
        }

    # 业务错误：HTTP 200 但 code 非 200
    code = data.get("code")
    if code not in (None, 200, 0):
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False,
            "error": f"博查返回错误 {code}：{data.get('msg') or data.get('message') or ''}",
            "provider": "bocha", "engines_used": [],
        }

    pages = (
        ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    )
    results: List[Dict[str, str]] = []
    for item in pages:
        title = (item.get("name") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue
        # summary 是博查给 AI 用的长摘要，比 snippet 信息量大
        snippet = (item.get("summary") or item.get("snippet") or "").strip()
        results.append(
            {
                "title": title,
                "snippet": snippet[:1200],
                "href": url,
                "source": "bocha",
                "site": (item.get("siteName") or "").strip(),
                "published": (item.get("datePublished") or "").strip(),
            }
        )

    return {
        "query": query,
        "instant_answer": None,
        "results": results,
        "success": len(results) > 0,
        "error": None if results else "博查没有返回结果",
        "provider": "bocha",
        "engines_used": ["bocha"],
    }


def _bocha_error(resp) -> str:
    """把博查的拒绝原因翻成人话。"""
    hint = {401: "API Key 无效或已失效", 402: "账户余额不足", 403: "该 Key 无搜索权限"}
    try:
        body = resp.json()
        msg = body.get("msg") or body.get("message") or ""
    except Exception:  # noqa: BLE001
        msg = (resp.text or "")[:120]
    base = hint.get(resp.status_code, f"HTTP {resp.status_code}")
    return f"博查：{base}{('（' + msg + '）') if msg else ''}"


async def _do_search_tavily(
    query: str,
    api_key: str,
    max_results: int,
    search_depth: str,
    timeout: int,
) -> Dict:
    """Tavily：面向 AI 检索优化，返回摘要 + 来源，最适合问答场景。"""
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
            resp = await _request("POST", TAVILY_API_URL, json=payload, timeout=timeout)
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
            "success": False, "error": str(last_exc) if last_exc else "unknown error",
            "engines_used": []
        }

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

    return {
        "query": query,
        "instant_answer": instant_answer,
        "results": results,
        "success": len(results) > 0 or instant_answer is not None,
        "error": None,
        "provider": "tavily",
        "engines_used": ["tavily"],
    }


async def _do_search_serper(
    query: str,
    api_key: str,
    max_results: int,
    timeout: int,
) -> Dict:
    """Serper：走 Google 搜索结果，覆盖面广、时效性好。"""
    client = await _get_client()
    try:
        resp = await _request(
            "POST",
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query.strip(), "num": max_results},
            timeout=timeout,
        )
        if resp.status_code != 200:
            msg = resp.text[:200]
            return {
                "query": query, "instant_answer": None, "results": [],
                "success": False, "error": f"Serper 返回 {resp.status_code}: {msg}",
                "provider": "serper", "engines_used": []
            }
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False, "error": str(exc),
            "provider": "serper", "engines_used": []
        }

    results: List[Dict[str, str]] = []
    for r in (data.get("organic") or [])[:max_results]:
        title = r.get("title", "")
        link = r.get("link", "")
        snippet = r.get("snippet", "")
        if title and link:
            results.append({
                "title": title,
                "snippet": snippet,
                "href": link,
                "source": "serper",
            })

    instant = None
    answer_box = data.get("answerBox")
    if isinstance(answer_box, dict):
        instant = (answer_box.get("answer") or answer_box.get("snippet") or "")[:3000] or None
    elif isinstance(answer_box, list) and answer_box:
        first = answer_box[0]
        if isinstance(first, dict):
            instant = (first.get("answer") or first.get("snippet") or "")[:3000] or None

    return {
        "query": query,
        "instant_answer": instant,
        "results": results,
        "success": len(results) > 0 or instant is not None,
        "error": None,
        "provider": "serper",
        "engines_used": ["serper"],
    }


async def _do_search_duckduckgo(
    query: str,
    max_results: int,
    timeout: int,
) -> Dict:
    """DuckDuckGo Instant Answer：不需要密钥，先让功能开箱可用。

    覆盖面比付费 API 窄（偏百科式答案），所以前端会提示「想要更全结果可换 Tavily/Serper」。
    """
    client = await _get_client()
    try:
        resp = await _request(
            "GET",
            "https://api.duckduckgo.com/",
            params={"q": query.strip(), "format": "json", "no_html": 1, "skip_disambig": 1},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {
                "query": query, "instant_answer": None, "results": [],
                "success": False, "error": f"DuckDuckGo 返回 {resp.status_code}",
                "provider": "duckduckgo", "engines_used": []
            }
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False,
            "error": f"DuckDuckGo 在当前网络不可达（{type(exc).__name__}）。"
                     "请在「用户菜单 → 联网搜索」里换用 Tavily 或 Serper。",
            "provider": "duckduckgo", "engines_used": []
        }

    results: List[Dict[str, str]] = []
    instant = data.get("AbstractText") or data.get("Answer") or ""

    def _push(title: str, text: str, url: str) -> None:
        if title and url and len(results) < max_results:
            results.append({
                "title": title,
                "snippet": (text or "")[:600],
                "href": url,
                "source": "duckduckgo",
            })

    for topic in (data.get("RelatedTopics") or [])[: max_results * 2]:
        if not isinstance(topic, dict):
            continue
        if topic.get("Topics"):
            for sub in topic["Topics"][:2]:
                _push(sub.get("Text", ""), sub.get("Text", ""), sub.get("FirstURL", ""))
        else:
            _push(topic.get("Text", ""), topic.get("Text", ""), topic.get("FirstURL", ""))

    for res in (data.get("Results") or [])[:max_results]:
        _push(res.get("Title", ""), res.get("Snippet", ""), res.get("FirstURL", ""))

    return {
        "query": query,
        "instant_answer": (instant or "")[:3000] or None,
        "results": results,
        "success": len(results) > 0 or bool(instant),
        "error": None,
        "provider": "duckduckgo",
        "engines_used": ["duckduckgo"],
    }


#: 同步调用专用的事件循环（跑在一个守护线程里）。
#
# 为什么不是每次 asyncio.run()：
#   那样每次都新建并关闭一个循环，httpx 的连接池跟着作废，
#   第二次调用就会拿到连着死循环的客户端 → "Event loop is closed"。
#   常驻一个循环，同步调用之间能复用连接，也彻底避开跨循环问题。
_SYNC_LOOP: Optional[asyncio.AbstractEventLoop] = None
_SYNC_LOOP_LOCK = threading.Lock()


def _ensure_sync_loop() -> asyncio.AbstractEventLoop:
    global _SYNC_LOOP
    with _SYNC_LOOP_LOCK:
        if _SYNC_LOOP is None or _SYNC_LOOP.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="web-search-loop", daemon=True
            )
            thread.start()
            _SYNC_LOOP = loop
            logger.debug("已启动联网搜索的常驻事件循环线程")
        return _SYNC_LOOP


def stop_sync_loop() -> None:
    global _SYNC_LOOP
    loop, _SYNC_LOOP = _SYNC_LOOP, None
    if loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(loop.stop)
    except RuntimeError:
        pass


def search(
    query: str,
    max_results: int = 5,
    timeout: int = 15,
    search_depth: str = "basic",
) -> Dict[str, any]:
    """同步包装器。

    工作流里工具是同步执行的（`registry.execute`），走的就是这里。
    内部提交到常驻循环执行，**连续调用共享同一个循环与连接池**。
    """
    # 已经在事件循环里还调同步接口：再 run_forever/阻塞都会死锁，直接说清楚
    if _current_loop() is not None:
        logger.error("search() 不能在事件循环内调用，请改用 await search_async()")
        return {
            "query": query, "instant_answer": None, "results": [],
            "success": False,
            "error": "同步搜索接口不能在事件循环内调用（请用 search_async）",
            "engines_used": [],
        }

    try:
        loop = _ensure_sync_loop()
        future = asyncio.run_coroutine_threadsafe(
            search_async(query, max_results, timeout, search_depth), loop
        )
        # 给足余量：内部有 3 次重试，不能比单次超时还短
        return future.result(timeout=timeout * 4 + 30)
    except Exception as e:  # noqa: BLE001
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
    """智能判断用户问题是否需要联网搜索。"""
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

    return False


# ---------------------------------------------------------------------------
#  Tool adapter — 将联网搜索封装为 BaseTool 接口
# ---------------------------------------------------------------------------

from typing import Any, Dict as _Dict

from app.tools.base import BaseTool


class WebSearchToolAdapter(BaseTool):
    """联网搜索工具适配器，对接 ToolRegistry。"""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "联网搜索实时信息。当问题涉及最新新闻、天气、股价、汇率、当前事件等需要互联网数据时使用。返回搜索结果摘要和来源链接。"

    @property
    def parameters(self) -> _Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数，默认5",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str = "", max_results: int = 5, **kwargs) -> _Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "搜索关键词不能为空"}

        sr = search(query, max_results=max_results)
        if not sr.get("success"):
            # 把「去配置」的线索原样带出去，前端据此给出跳转入口
            out = {"success": False, "error": sr.get("error", "搜索失败")}
            if sr.get("action"):
                out["action"] = sr["action"]
            if sr.get("error_code"):
                out["error_code"] = sr["error_code"]
            return out

        context, sources = build_search_context(sr)
        return {
            "success": True,
            "result": context,
            "sources": sources,
            "instant_answer": sr.get("instant_answer"),
        }
