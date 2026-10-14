"""
pytest 共享夹具。

设计要点：
  * 强制 `MODEL_BACKEND=mock`，避免测试触发真实模型调用
  * 清空配置缓存，确保环境变量在 `get_settings()` 首次调用前生效
  * 提供「带 lifespan 的 client」（真实 Redis/降级路径）与
    「不带 lifespan 的 client」（纯路由测试）两种夹具
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR.parent / ".env")
load_dotenv(BACKEND_DIR / ".env")

# 测试期强制使用 mock 后端，杜绝外部网络调用
os.environ["MODEL_BACKEND"] = "mock"
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")  # 不可达 → 走降级
os.environ.setdefault("REDIS_FALLBACK_TO_MEMORY", "true")

# 限流计数落在 fakeredis 的**全 session 共享** server 上，会跨用例累积。
# 默认全局限额（60/窗口）在测试总量增大后会被打满，表现为「随机一批用例 429」——
# 排查成本很高且与被测逻辑无关。这里放宽即可：
# 限流本身的行为由 tests/unit/test_core_redis.py 与 tests/test_redis_live.py
# 用**显式 limit** 覆盖，放宽默认值不影响那部分覆盖率。
os.environ["RATE_LIMIT_MAX_REQUESTS"] = "100000"

DATA_DIR = BACKEND_DIR / "tests" / "data"


@pytest.fixture(scope="session", autouse=True)
def _reset_settings_cache():
    from app.core.config import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """每个用例前后清掉模块级的全局缓存。

    ⚠️ 这些缓存在进程里是**全局的**，用例之间会互相污染，而且只在全量运行时
    才暴露 —— 单独跑某个文件时一切正常，排查成本极高。已经真实发生过一次：

      `reachability._cache` 缓存了「未校验」的探测结果（某个用例没关掉真实
      网络探测 → HEAD 失败 → 记为 None 并缓存 30 分钟），
      下一个文件里断言「可达」的用例于是读到 None 而失败。

    自愈不如隔离：与其要求每个用例自己记得清缓存，不如统一在边界上清掉。
    """
    from app.rag import providers
    from app.workflow import reachability

    reachability.clear_cache()
    # 自定义模型端点是另一处全局注册表（providers 不能查库，只能被注入），
    # 同样需要在用例之间复位，否则「上一个用例配的模型」会漏进下一个用例
    providers.set_custom_endpoints([])
    yield
    reachability.clear_cache()
    providers.set_custom_endpoints([])


@pytest.fixture(scope="session")
def settings():
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture(scope="session")
def client():
    """带 lifespan 的客户端（会话级共享）。

    **必须走 lifespan** —— 状态图与 Redis 客户端都在启动流程中构建，
    不跑 lifespan 的话 `app.state.qa_graph` 不存在，
    所有问答类端点都会报"状态图未初始化"。
    同时这也让测试环境与生产更接近（含 Redis 降级路径）。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def live_client(client):
    """语义别名：强调"已完整启动"。保留以区分配置类测试。"""
    return client


@pytest.fixture(scope="session")
def auth_token(client) -> str:
    response = client.post(
        "/token",
        data={
            "username": os.environ["ADMIN_USERNAME"],
            "password": os.environ["ADMIN_PASSWORD"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"登录失败: {response.text}"
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(auth_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
