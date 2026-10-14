"""
分层依赖检查（静态 AST 分析，不执行 import）。

分层规则若没有自动化检查，几个月后必然被破坏 ——
这是 docs/REFACTOR_*.md 第 5.1 节「依赖矩阵」的可执行版本。

实现说明：用 `ast` 解析源码而非真正 import，
因此即使被检查模块存在运行时依赖（数据库、网络），检查本身也能秒级完成。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent.parent / "app"

# 各层**禁止**依赖的上层前缀
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "core": ("app.services", "app.graph", "app.api", "app.repositories"),
    "repositories": ("app.services", "app.graph", "app.api"),
    "graph": ("app.api", "app.services"),
    "services": ("app.api",),
    "rag": ("app.services", "app.graph", "app.api", "app.repositories"),
    "tools": ("app.services", "app.graph", "app.api", "app.repositories"),
    "utils": ("app.services", "app.graph", "app.api", "app.repositories", "app.core"),
    # AI 工作流域内逻辑（planner / file_context）：纯文本与校验逻辑，
    # 只允许依赖 core / utils / document / tools / rag。
    # 刻意禁止它依赖 graph —— 否则 plan 节点会与领域逻辑形成循环诱惑，
    # 而这一层的全部价值就在于「能脱离图与数据库完整单测」。
    "workflow": (
        "app.services",
        "app.graph",
        "app.api",
        "app.repositories",
    ),
}

# 迁移期兼容模块豁免：目前仅剩组装根 main.py
# （config.py / rag_chain.py 等兼容外壳已在 Phase 6 删除，不再需要豁免）
EXEMPT_FILES = {
    "main.py",  # 组装根，允许依赖任意层
}

# 叶子模块例外：`core.exceptions` 是纯错误分类学（只依赖 typing），
# 被 core 与 utils 共同使用不构成反向依赖，也不会有循环风险。
# 允许它被下层引用，而不是把异常定义复制一份到 utils。
ALLOWED_CROSS_LAYER_IMPORTS = {
    "app.core.exceptions",
}


def _iter_module_files(layer: str):
    layer_dir = APP_DIR / layer
    if not layer_dir.is_dir():
        return []
    return [
        path
        for path in layer_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _imported_app_modules(path: Path) -> set[str]:
    """收集一个文件里 import 到的 app.* 模块名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("app."):
                found.add(node.module)
                # from app.core import deps  → 也记录 app.core.deps
                for alias in node.names:
                    found.add(f"{node.module}.{alias.name}")
    return found


def _cases() -> list[tuple[str, Path, tuple[str, ...]]]:
    cases: list[tuple[str, Path, tuple[str, ...]]] = []
    for layer, forbidden in FORBIDDEN.items():
        for path in _iter_module_files(layer):
            if path.name in EXEMPT_FILES:
                continue
            cases.append((layer, path, forbidden))
    return cases


@pytest.mark.parametrize(
    "layer,path,forbidden",
    _cases(),
    ids=lambda value: str(value).replace("\\", "/").split("/app/")[-1]
    if isinstance(value, Path)
    else str(value),
)
def test_layer_does_not_depend_upward(layer, path, forbidden):
    imported = _imported_app_modules(path)

    def _allowed(name: str) -> bool:
        # 精确匹配或前缀匹配（`from app.core.exceptions import X` 会记录成
        # `app.core.exceptions.X`，需要前缀判定）
        return any(
            name == allowed or name.startswith(allowed + ".")
            for allowed in ALLOWED_CROSS_LAYER_IMPORTS
        )

    violations = sorted(
        name
        for name in imported
        if not _allowed(name)
        and any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    )
    assert not violations, (
        f"分层违规：app/{layer}/{path.name} 依赖了上层模块 {violations}。"
        f" 允许依赖方向见 docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md 第 5.1 节。"
    )


# 当前已落地的分层目录
_ESTABLISHED_LAYERS = (
    "core",
    "api",
    "services",
    "schemas",
    "utils",
    "graph",
    "rag",
    "repositories",
    # AI 工作流域内逻辑（P2 新增）：planner / file_context
    "workflow",
)
# 规划中、尚未创建
_PLANNED_LAYERS: tuple[str, ...] = ()


def test_established_layer_directories_exist():
    for layer in _ESTABLISHED_LAYERS:
        assert (APP_DIR / layer).is_dir(), f"缺少分层目录 app/{layer}/"


def test_no_layer_directory_is_unexpected():
    """app/ 下的一级目录必须属于已知分层，避免随意新增导致边界模糊。"""
    known = set(_ESTABLISHED_LAYERS) | set(_PLANNED_LAYERS) | {
        "__pycache__",
        # v3 遗留，按 Phase 6 计划归入 rag/ 或删除
        "agents",
        "document",
        "embeddings",
        "memory",
        "retrievers",
        "tools",
    }
    actual = {
        p.name for p in APP_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    assert actual <= known, f"出现未登记的分层目录: {sorted(actual - known)}"


def test_core_has_no_fastapi_router_dependency():
    """core 可以用 fastapi 原语（Depends/Request），但不得定义路由。"""
    for path in _iter_module_files("core"):
        source = path.read_text(encoding="utf-8")
        assert "APIRouter" not in source, f"app/core/{path.name} 不应定义路由"
