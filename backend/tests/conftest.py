"""
pytest 共享夹具。

设计要点：
  * 强制 `MODEL_BACKEND=mock`，避免测试触发真实模型调用
  * **数据目录隔离**：`DATA_PATH` 指向进程级临时目录，测试不再写真实 `backend/data/`
  * **凭据隔离**：外部服务凭据一律置非空占位符，测试进程不持有任何真实凭据
  * 清空配置缓存，确保环境变量在 `get_settings()` 首次调用前生效
  * 提供「带 lifespan 的 client」（真实 Redis/降级路径）与
    「不带 lifespan 的 client」（纯路由测试）两种夹具

⚠️ 下面的赋值**必须保持模块级**。`tests/` 有 32 个文件在模块顶层 `import app`，
收集期就会触发 `app.core.config` / `app.memory.conversation_memory` 的 import 期求值，
session 级 autouse fixture 来不及。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
#  1) 数据目录隔离（必须先于任何 app.* import）
# ---------------------------------------------------------------------------
# 真实机制：`app/core/config.py` 的 `data_path` 默认值是 `BASE_DIR/"data"`（硬编码），
# 且 `app/memory/conversation_memory.py` 在**模块顶层**就把 `DB_DIR` 快照下来。
# 所以只能在这里、在收集期之前用环境变量覆盖；fixture 里做太晚。
#
# 曾经的事故：`tests/` 里的固定 conversation_id 直接写进真实 `backend/data/conversations.db`，
# 累计 1190 条孤儿消息（占消息表 76.5%），且每跑一次新增 26 条。
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="zj_test_data_")
os.environ["DATA_PATH"] = _TEST_DATA_DIR

# 向量库目录必须一起隔离。
#
# 只隔离 data/ 是不够的：`chroma_persist_dir` 默认也是 `BASE_DIR/"chroma_db"`，
# 而 `ensure_embedding_schema()` 看到「标记文件不存在（临时 data/ 里没有）+ 库里有数据」
# 就会判定"嵌入方式变了"，直接 **重建真实向量库**。实测触发过一次：
# 测试用 mock 嵌入（128 维）把真实 902 个 2048 维分块判成待迁移，
# 只是因为环境阻止了目录删除才没造成损失。
os.environ["CHROMA_PERSIST_DIR"] = str(Path(_TEST_DATA_DIR) / "chroma_db")
os.environ["LOG_DIR"] = str(Path(_TEST_DATA_DIR) / "logs")

# ---------------------------------------------------------------------------
#  2) 凭据隔离（一律非空占位符）
# ---------------------------------------------------------------------------
# ⚠️ 不能用空串：`_COMMON_CFG` 有 `env_ignore_empty=True`，空串会被判定为"未设置"
#    并**回落到 .env 里的真值** —— 看起来禁用了，其实没有。
_DISABLED = "disabled-in-test"
for _key in (
    "MODELSCOPE_API_KEY",
    "ZHIPU_API_KEY",
    "TAVILY_API_KEY",
    "SERPER_API_KEY",
    "BAILIAN_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "QIANFAN_AK",
    "QIANFAN_SK",
):
    os.environ[_key] = _DISABLED

# JWT 例外：`SecuritySettings.jwt_secret_key` 的默认值就是它，
# 写占位符会抹掉「默认弱密钥 → prod 拒绝启动」这条安全基线的测试路径。
os.environ["JWT_SECRET_KEY"] = "change-me-in-production"

# ADMIN_PASSWORD 例外：`auth_token` 夹具要用它真登录，置空/禁用会让所有
# 依赖该夹具的用例集体失败，且报错表面像"登录挂了"。这里换成一个固定的
# 测试口令（**必须是直接赋值，不能用 setdefault** —— .env 的值已存在时
# setdefault 不生效，会把真实口令留在进程里）。
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-only"

# 测试期强制使用 mock 后端，杜绝外部网络调用
os.environ["MODEL_BACKEND"] = "mock"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"  # 不可达 → 走降级
os.environ["REDIS_FALLBACK_TO_MEMORY"] = "true"

# 限流计数落在 fakeredis 的**全 session 共享** server 上，会跨用例累积。
# 默认全局限额（60/窗口）在测试总量增大后会被打满，表现为「随机一批用例 429」——
# 排查成本很高且与被测逻辑无关。这里放宽即可：
# 限流本身的行为由 tests/unit/test_core_redis.py 与 tests/test_redis_live.py
# 用**显式 limit** 覆盖，放宽默认值不影响那部分覆盖率。
os.environ["RATE_LIMIT_MAX_REQUESTS"] = "100000"

DATA_DIR = BACKEND_DIR / "tests" / "data"


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """回收临时数据目录。

    `mkdtemp` 不会自动清理；不删就会把"污染真实库"换成"污染系统 temp"。
    用 `ignore_errors=True`：Windows 上 sqlite 长连接若未释放，直接删会抛异常。
    """
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)



@pytest.fixture(scope="session", autouse=True)
def _reset_settings_cache():
    from app.core.config import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture(autouse=True)
def _clear_settings_cache_after_test():
    """用例结束后清掉配置缓存。

    有些用例会 monkeypatch 数据库路径（`WORKFLOW_DB_PATH` / `DATA_PATH` /
    `WORKFLOW_WORKSPACE_ROOT`），而 `get_workflow_settings()` 等分组是
    `@lru_cache(maxsize=1)`。monkeypatch 撤销后，**缓存里仍是旧路径**，
    后续用例就会去连一个已经被 pytest 删除的临时目录 ——
    表现为「单独跑这个文件全绿、全量跑一堆红」，且报错毫无指向性。

    代价是每个用例后重新解析一次配置（可忽略）。
    """
    yield
    from app.core.config import clear_settings_cache

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
