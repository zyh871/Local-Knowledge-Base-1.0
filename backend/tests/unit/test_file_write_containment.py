"""`file_write` 的落点边界判定。

为什么单独一个文件：原来的两个"越界"用例其实都测不到越界 ——
  * `test_path_traversal_refused` 用 `if result["success"]:` 条件断言，
    无论成功失败都算通过；
  * `test_sibling_prefix_dir_is_not_treated_as_inside` 只传 `path="a.txt"`，
    任何实现都不可能写到 sibling 目录 —— 恒绿、零鉴别力。

真正的边界逻辑在 `_inside()`，这里对它做表驱动测试。
变异验证：把 `commonpath` 改回 `candidate.startswith(root)`
→ sibling 前缀那一行必须变红。
"""

from __future__ import annotations

import os

import pytest

from app.tools.file_write import _inside


class TestInside:
    def test_child_is_inside(self, tmp_path):
        root = tmp_path / "bound"
        root.mkdir()
        assert _inside(str(root), str(root / "x.txt")) is True

    def test_root_itself_is_inside(self, tmp_path):
        root = tmp_path / "bound"
        root.mkdir()
        assert _inside(str(root), str(root)) is True

    def test_sibling_prefix_is_not_inside(self, tmp_path):
        """`bound_evil` 以 `bound` 为前缀但不在里面。

        用 `startswith` 判断就会把这里判定为"在里面" —— 越界写出去。
        """
        root = tmp_path / "bound"
        root.mkdir()
        sibling = tmp_path / "bound_evil"
        sibling.mkdir()
        assert _inside(str(root), str(sibling / "x.txt")) is False

    def test_parent_escape_is_not_inside(self, tmp_path):
        root = tmp_path / "bound"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        assert _inside(str(root), str(outside)) is False

    def test_dotdot_escape_is_not_inside(self, tmp_path):
        root = tmp_path / "bound"
        root.mkdir()
        assert _inside(str(root), str(root / ".." / "outside.txt")) is False

    @pytest.mark.skipif(os.name != "nt", reason="跨盘符是 Windows 特有语义")
    def test_cross_drive_is_not_inside(self, tmp_path):
        root = tmp_path / "bound"
        root.mkdir()
        assert _inside(str(root), "Z:/other/x.txt") is False


class TestToolNeverEscapesArtifactDir:
    """工具层护栏：无论传什么 path，落点都必须在产物目录内。"""

    @pytest.mark.parametrize(
        "path",
        ["../../escape.py", "sub/dir/a.py", "..\\escape.py", "/etc/passwd.py"],
    )
    def test_lands_inside_artifact_dir(self, path, tmp_path):
        from app.tools.file_write import FileWriteTool

        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()

        result = FileWriteTool().execute(
            path=path,
            content="x",
            _context={
                "artifact_dir": str(artifact_dir),
                "allowed_extensions": [".py", ".md", ".txt", ".json"],
                "max_bytes": 1024,
                "bound_allowed": True,
            },
        )
        # 工具只接受**纯文件名**，目录部分会被剥掉 —— 所以这里断言的是
        # "落点一定在产物目录内"，而不是"路径被拒绝"。
        assert result["success"] is True, result
        stored = os.path.abspath(result["written"]["stored_path"])
        assert os.path.commonpath([str(artifact_dir), stored]) == str(artifact_dir)

        # 目录外不能凭空长出文件
        assert not (tmp_path / "escape.py").exists()
        assert not (tmp_path.parent / "escape.py").exists()
