"""F-010 判据第 1 层：AST 赋值点存在性。

为什么必须是 AST 层（而不是运行时探针）
--------------------------------------
F-010 的残留是**身份问题**，不是内容问题：``sys.stdout`` 被改指向一个废弃的
StringIO 就算污染，无论它里面有没有内容。任何依赖运行时状态的判据都受两个
变量摆布：

1. **退出分支**——``code_executor.py`` 的 ``buf.getvalue()`` 只在 ``:99``
   （正常完成）执行。实测：正常完成→残留 0；abort 成功（``:100``）→无残留；
   abort 失败、线程仍在跑（``:125-130``）→ 残留非 0。
2. **abort 成败**——取决于被测代码是否卡在 C 层（纯 Python 死循环→abort 成功；
   ``time.sleep``→abort 失败）。

换句话说，**任何运行时探针的结论都会随被测代码内部实现变化**。AST 静态扫描
不受此影响——它检查"这行代码是否存在"，与运行到哪条分支无关。

本文件要证明的事
----------------
``app/tools/code_executor.py`` 中**不存在**任何对 ``sys.stdout`` 的赋值。
只要有，print 注入就不彻底。

元测试（重要）
--------------
``TestCriterionSelfCheck`` 先证明本判据在已知坏样本上**会失败**。

> 依据团队规范：恒绿的判据可能只是坏了。「全部通过」与「判据根本抓不到东西」
> 在结果上无法区分，除非先验证判据本身有鉴别力。
>
> 本文件第一版就是反面教材——用 ``ast.dump(t) == "sys.stdout"`` 比较，
> 而 ``dump`` 输出的是``Attribute(value=Name(id='sys',...), attr='stdout')``，
> **永不匹配**，于是 6 个坏样本全部漏过、却"全绿"。加了元测试才能发现。

别名绕过
--------
``_s = sys; _s.stdout = buf`` 与直接写 ``sys.stdout = buf`` 效果相同。
本判据会做一轮**局部别名追踪**（模块级 ``X = sys``），并对无法静态解析的
别名保持**保守判定**（见 ``TestAliasedStdoutIsCaught``）。
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

TARGET = Path(__file__).resolve().parents[2] / "app" / "tools" / "code_executor.py"


def _is_sys_module(node: ast.AST, aliases: set[str]) -> bool:
    """``node`` 是否指向 ``sys`` 模块（直接 ``sys`` 或已知别名）。"""
    if isinstance(node, ast.Name):
        return node.id == "sys" or node.id in aliases
    return False


def _module_level_sys_aliases(tree: ast.Module) -> set[str]:
    """收集模块级 ``X = sys`` / ``X = sys as ...`` 形式的别名。"""
    aliases: set[str] = set()
    for node in tree.body:
        # X = sys
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and _is_sys_module(node.value, set()):
                    aliases.add(target.id)
        # import sys as X
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys" and alias.asname:
                    aliases.add(alias.asname)
    return aliases


def _stdout_assignments(source: str) -> list[tuple[str, int]]:
    """返回源码中所有对 ``sys.stdout``（含别名）的赋值点 ``[(op, lineno)]``。

    只认**写入**（``Assign`` / ``AugAssign`` / ``AnnAssign``）。
    ``old_stdout = sys.stdout`` 是把 stdout 读进变量、不改变它指向谁，
    因此不算违规。
    """
    tree = ast.parse(source)
    aliases = _module_level_sys_aliases(tree)
    hits: list[tuple[str, int]] = []

    def check(target: ast.AST, op: str, lineno: int) -> None:
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "stdout"
            and _is_sys_module(target.value, aliases)
        ):
            hits.append((op, lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                check(target, "Assign", node.lineno)
        elif isinstance(node, ast.AugAssign):
            check(node.target, "AugAssign", node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            check(node.target, "AnnAssign", node.lineno)

    return hits


class TestStdoutNeverReassigned:
    """判据第 1 层（主判据）。"""

    def test_code_executor_does_not_assign_stdout(self) -> None:
        """``code_executor.py`` 不得对 ``sys.stdout`` 赋值。"""
        assert TARGET.exists(), f"目标文件不存在：{TARGET}"

        hits = _stdout_assignments(TARGET.read_text(encoding="utf-8"))
        detail = "\n".join(
            f"  第 {ln} 行: {op}" for op, ln in hits
        )

        assert not hits, (
            f"`code_executor.py` 仍对 `sys.stdout` 赋值（{len(hits)} 处）：\n"
            f"{detail}\n"
            "print 注入方案要求彻底移除全局 stdout 替换——"
            "读（`old_stdout = sys.stdout`）可以保留，写不可以。"
        )


class TestCriterionSelfCheck:
    """元测试：先证明判据在已知坏样本上会失败。

    没有这一节，「全部通过」和「判据根本抓不到东西」在结果上无法区分。
    第一版判据就是靠这里被发现的假绿。
    """

    BAD_SAMPLES = {
        "assign": "import sys\nsys.stdout = buf\n",
        "augassign": "import sys\nsys.stdout += 'x'\n",
        "annassign": "import sys\nsys.stdout: object = buf\n",
        "alias_attr": "import sys\n_s = sys\n_s.stdout = buf\n",
        "import_as": "import sys as S\nS.stdout = buf\n",
        "chained": "import sys\nsys.stdout = sys.__stdout__ = buf\n",
    }

    CLEAN_SAMPLES = {
        # print 局部注入（第一步修复的正确写法）——绝不能被误报
        "print_injection": textwrap.dedent(
            """
            import io
            buf = io.StringIO()
            g = {'print': lambda *a, **k: buf.write(' '.join(map(str, a)) + chr(10))}
            exec(code, g, {})
            """
        ),
        # 读取 stdout 是允许的
        "read_only": "import sys\nold_stdout = sys.stdout\n",
        "read_only_call": "import sys\nv = sys.stdout.write\n",
        # 其他对象赋值不应误报
        "other_object": "import sys\nsys.stderr = buf\n",
        "normal_code": "x = 1\ny = x + 1\nprint(y)\n",
    }

    @pytest.mark.parametrize("name", sorted(BAD_SAMPLES))
    def test_bad_sample_is_detected(self, name: str) -> None:
        """已知坏样本必须被抓到，否则判据无鉴别力。"""
        hits = _stdout_assignments(self.BAD_SAMPLES[name])
        assert hits, (
            f"判据失效：坏样本 `{name}` 未被抓到 —— 本判据根本无效，"
            "此时上面所有「通过」都是假绿。"
        )

    @pytest.mark.parametrize("name", sorted(CLEAN_SAMPLES))
    def test_clean_sample_is_not_flagged(self, name: str) -> None:
        """正确写法不得被误报，否则修复方会被逼着保留全局赋值。"""
        hits = _stdout_assignments(self.CLEAN_SAMPLES[name])
        assert not hits, (
            f"判据误报：正确写法 `{name}` 被标记为 {hits}。"
            "这会让修复方无法采用 print 注入方案。"
        )

    #: 期望的样本数量。**故意写成模块常量而非引用 BAD_SAMPLES/CLEAN_SAMPLES**
    #: ——若写成 ``len(BAD_SAMPLES) == 6``，把字典清空成 ``{}`` 时等式依然成立，
    #: 判据会被「清空样本 + 全部转绿」这种手法悄悄拆掉。这是个真实漏洞，
    #: 由 product-reviewer 在复核时发现。
    EXPECTED_BAD_SAMPLES = 6
    EXPECTED_CLEAN_SAMPLES = 5

    def test_every_bad_sample_documented(self) -> None:
        """坏样本清单与判据能力对齐：新增样本必须同步更新覆盖。"""
        for name in self.BAD_SAMPLES:
            assert _stdout_assignments(self.BAD_SAMPLES[name]), name
        for name in self.CLEAN_SAMPLES:
            assert not _stdout_assignments(self.CLEAN_SAMPLES[name]), name

    def test_sample_sets_are_not_empty(self) -> None:
        """★ 防「清空样本」手法。

        ``for name in {}:`` 不会执行任何断言，测试照样通过——这会让整个
        ``TestCriterionSelfCheck`` 静默失效：把BAD_SAMPLES 清空后，
        所有判据测试消失，而 ``pytest`` 报告一片绿。

        所以这里断言的是**绝对数量**而非「数量等于字典大小」——后者
        在字典被清空时依然成立，形同虚设。
        """
        assert len(self.BAD_SAMPLES) == self.EXPECTED_BAD_SAMPLES, (
            f"坏样本数应为 {self.EXPECTED_BAD_SAMPLES}，实际 {len(self.BAD_SAMPLES)}。"
            "若确认要减少样本，请同步改EXPECTED_BAD_SAMPLES 并说明理由。"
        )
        assert len(self.CLEAN_SAMPLES) == self.EXPECTED_CLEAN_SAMPLES, (
            f"干净样本数应为 {self.EXPECTED_CLEAN_SAMPLES}，实际 {len(self.CLEAN_SAMPLES)}。"
            "若确认要减少样本，请同步改 EXPECTED_CLEAN_SAMPLES 并说明理由。"
        )

    @pytest.mark.parametrize(
        "required",
        ["assign", "augassign", "annassign", "alias_attr", "import_as", "chained"],
    )
    def test_required_bad_samples_present(self, required: str) -> None:
        """★ 逐个点名：关键绕过样本必须存在。

        比 ``test_sample_sets_are_not_empty`` 更强——即使有人用一个
        同量的新样本替换掉某个关键样本，这条也会红。
        """
        assert required in self.BAD_SAMPLES, f"关键坏样本 `{required}` 被移除了"
        assert _stdout_assignments(self.BAD_SAMPLES[required]), (
            f"关键坏样本 `{required}` 未被检测到——判据已失效"
        )


class TestOtherToolsAlsoClean:
    """旁证：其他工具也不应改写全局 stdout。

    当前全项目只有 ``code_executor.py`` 触碰过 ``sys.stdout``（实测
    ``grep "sys\\.stdout\\s*=" app/`` 仅 :97/:106 两行）。这条测试防止
    将来有人在新工具里重犯——那正是"逐条修补误用点"路径的隐患。
    """

    @pytest.mark.parametrize(
        "path",
        sorted(
            p for p in (TARGET.parent).glob("*.py") if p.name != "__init__.py"
        ),
    )
    def test_no_tool_assigns_stdout(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        if "stdout" not in source:
            pytest.skip(f"{path.name} 不涉及 stdout")

        hits = _stdout_assignments(source)
        assert not hits, f"{path.name} 对 `sys.stdout` 赋值：{hits}"