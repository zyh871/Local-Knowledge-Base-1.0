"""`file_write` 工具的边界：写得到、写不错、写不出去要说清楚。

这个工具是「让 AI 产出文件」的**唯一**通路。它一旦有洞，
洞就是"往用户磁盘任意位置写文件"；它一旦太严，用户看到的就是
「运行中创建不了文件」—— 也就是这次要修的那个报告。
所以这里两侧都钉：

  * 正向：产物目录能写、内容能落盘、能被登记
  * 反向：越界路径、不允许的后缀、超限大小、未绑定文件夹 —— 全部明确拒绝
"""

from __future__ import annotations

import os

import pytest

from app.tools.file_write import FileWriteTool


@pytest.fixture
def artifact_dir(tmp_path):
    target = tmp_path / "artifacts"
    target.mkdir()
    return target


@pytest.fixture
def bound_dir(tmp_path):
    target = tmp_path / "bound"
    target.mkdir()
    return target


def _ctx(artifact_dir, **overrides):
    ctx = {
        "artifact_dir": str(artifact_dir),
        "allowed_extensions": [".py", ".md", ".txt", ".json"],
        "max_bytes": 1024,
        "bound_allowed": True,
    }
    ctx.update(overrides)
    return ctx


class TestWritesToArtifactDir:
    def test_writes_file(self, artifact_dir):
        tool = FileWriteTool()
        result = tool.execute(
            path="fib.py", content="print('hi')", _context=_ctx(artifact_dir)
        )
        assert result["success"] is True
        written = result["written"]
        assert written["filename"] == "fib.py"
        assert written["target"] == "artifacts"
        path = os.path.join(str(artifact_dir), "fib.py")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == "print('hi')"

    def test_creates_missing_directory(self, tmp_path):
        """产物目录在第一次写入前可能还不存在。"""
        tool = FileWriteTool()
        target = tmp_path / "not-yet" / "artifacts"
        result = tool.execute(path="a.txt", content="x", _context=_ctx(target))
        assert result["success"] is True
        assert (target / "a.txt").is_file()

    def test_does_not_overwrite_and_says_so(self, artifact_dir):
        """★ 覆盖用户文件是不可逆的；改名必须**说出来**，否则用户以为没生成。"""
        tool = FileWriteTool()
        first = tool.execute(path="a.txt", content="one", _context=_ctx(artifact_dir))
        second = tool.execute(path="a.txt", content="two", _context=_ctx(artifact_dir))

        assert first["written"]["renamed"] is False
        assert second["written"]["renamed"] is True
        assert second["written"]["filename"] == "a_1.txt"
        assert "改名为" in second["result"]
        with open(os.path.join(str(artifact_dir), "a.txt"), encoding="utf-8") as handle:
            assert handle.read() == "one", "原文件被覆盖了"

    def test_summary_reports_size_and_where(self, artifact_dir):
        tool = FileWriteTool()
        result = tool.execute(path="a.md", content="hello", _context=_ctx(artifact_dir))
        assert "字节" in result["result"]
        assert "产物目录" in result["result"]


class TestRejections:
    def test_missing_context_is_explicit(self):
        result = FileWriteTool().execute(path="a.txt", content="x")
        assert result["success"] is False
        assert "上下文" in result["error"]

    def test_empty_content(self, artifact_dir):
        result = FileWriteTool().execute(
            path="a.txt", content="   ", _context=_ctx(artifact_dir)
        )
        assert result["success"] is False
        assert "content_from_step" in result["error"], "要告诉用户另一条路"

    def test_empty_path(self, artifact_dir):
        result = FileWriteTool().execute(path="", content="x", _context=_ctx(artifact_dir))
        assert result["success"] is False

    def test_extension_not_allowed(self, artifact_dir):
        result = FileWriteTool().execute(
            path="evil.exe", content="MZ", _context=_ctx(artifact_dir)
        )
        assert result["success"] is False
        assert "exe" in result["error"]

    def test_size_limit(self, artifact_dir):
        result = FileWriteTool().execute(
            path="big.txt", content="x" * 2000, _context=_ctx(artifact_dir, max_bytes=1024)
        )
        assert result["success"] is False
        assert "超过" in result["error"]

    @pytest.mark.parametrize(
        "path",
        ["../../escape.py", "sub/dir/a.py", "..\\escape.py", "/etc/passwd.py"],
    )
    def test_path_traversal_refused(self, path, artifact_dir, tmp_path):
        """★ 只允许文件名。带目录/绝对路径一律不能越界。"""
        result = FileWriteTool().execute(
            path=path, content="x", _context=_ctx(artifact_dir)
        )
        if result["success"]:
            stored = os.path.abspath(result["written"]["stored_path"])
            assert stored.startswith(str(artifact_dir)), f"写到范围外了: {stored}"
        assert not (tmp_path / "escape.py").exists()
        assert not (tmp_path.parent / "escape.py").exists()

    def test_bound_folder_without_binding_is_refused(self, artifact_dir):
        """★ 曾经写成"绑定了才校验"，等于给未绑定的工作区开任意路径后门。

        正确行为：**没有绑定就直接拒绝**，而不是"不校验放行"。
        """
        result = FileWriteTool().execute(
            path="a.txt",
            content="x",
            target="bound_folder",
            _context=_ctx(artifact_dir, bound_dir=""),
        )
        assert result["success"] is False
        assert "绑定" in result["error"]

    def test_bound_folder_disabled_by_config(self, artifact_dir, bound_dir):
        result = FileWriteTool().execute(
            path="a.txt",
            content="x",
            target="bound_folder",
            _context=_ctx(
                artifact_dir, bound_dir=str(bound_dir), bound_allowed=False
            ),
        )
        assert result["success"] is False
        assert "关闭" in result["error"]

    def test_bound_folder_missing_on_disk(self, artifact_dir, tmp_path):
        result = FileWriteTool().execute(
            path="a.txt",
            content="x",
            target="bound_folder",
            _context=_ctx(artifact_dir, bound_dir=str(tmp_path / "nope")),
        )
        assert result["success"] is False


class TestWritesToBoundFolder:
    def test_writes_into_bound_dir(self, artifact_dir, bound_dir):
        tool = FileWriteTool()
        result = tool.execute(
            path="out.md",
            content="# hi",
            target="bound_folder",
            _context=_ctx(artifact_dir, bound_dir=str(bound_dir)),
        )
        assert result["success"] is True
        assert (bound_dir / "out.md").is_file()
        assert result["written"]["target"] == "bound_folder"

    def test_stays_inside_bound_dir(self, artifact_dir, bound_dir, tmp_path):
        tool = FileWriteTool()
        tool.execute(
            path="../outside.txt",
            content="x",
            target="bound_folder",
            _context=_ctx(artifact_dir, bound_dir=str(bound_dir)),
        )
        assert not (tmp_path / "outside.txt").exists()

    def test_sibling_prefix_dir_is_not_treated_as_inside(self, tmp_path):
        """`/a/b2` 以 `/a/b` 为前缀但不在里面 —— 用 startswith 判断就会写出去。"""
        root = tmp_path / "bound"
        root.mkdir()
        sibling = tmp_path / "bound_evil"
        sibling.mkdir()
        tool = FileWriteTool()
        result = tool.execute(
            path="a.txt",
            content="x",
            target="bound_folder",
            _context=_ctx(tmp_path / "artifacts", bound_dir=str(root)),
        )
        assert result["success"] is True
        assert not (sibling / "a.txt").exists()


def test_default_extensions_include_code_and_docs():
    from app.tools.file_write import DEFAULT_WRITE_EXTENSIONS

    for ext in (".py", ".js", ".md", ".json", ".csv", ".html", ".yaml"):
        assert ext in DEFAULT_WRITE_EXTENSIONS


def test_tool_is_registered_and_requires_approval_ready():
    """工具必须在注册表里，否则计划里的 file_write 会在执行期报「未注册」。"""
    from app.tools.base import get_registry

    assert "file_write" in get_registry().list_names()
