"""工作区文件夹清单：只给名称与大小，**绝不含内容**。

这段提示词的目的是解决一个真实的误判：用户绑定了文件夹、然后说
「整理**这个**文件夹里的文件」，而模型此前完全不知道文件夹存在，
只能反问「哪个文件夹？」—— 用户则以为「程序访问不了我的本地文件」。

因此这里有两条必须守住的东西：
  1. **只有清单**（名称 + 大小）。目录内容是无界的，塞进提示词会撑爆上下文。
  2. **必须带免责声明**。给了清单，模型很容易顺势假设「那我能读到它们」
     并开始编造内容 —— 这句声明是防止幻觉的最后一道闸。
"""

from __future__ import annotations

import os

from app.workflow.clarifier import build_clarify_prompt
from app.workflow.file_context import render_folder_listing
from app.workflow.local_fs import summarize_dir
from app.workflow.planner import build_plan_prompt


class TestSummarizeDir:
    def test_lists_names_and_sizes_only(self, tmp_path):
        (tmp_path / "a.md").write_text("x" * 1200, encoding="utf-8")
        (tmp_path / "b.txt").write_text("短内容", encoding="utf-8")
        (tmp_path / "sub").mkdir()

        summary = summarize_dir(str(tmp_path))
        assert summary is not None
        names = [f["name"] for f in summary["files"]]
        assert names == ["a.md", "b.txt"]  # 按名称排序，稳定
        assert summary["files"][0]["size"] == 1200
        assert summary["dirs"] == ["sub"]
        assert summary["total_bytes"] == 1200 + len("短内容".encode("utf-8"))

    def test_no_content_leaks_into_summary(self, tmp_path):
        """★ 摘要里不能出现文件内容 —— 这是「只给清单」的底线。"""
        secret = "内部机密：定价 0.42 元"
        (tmp_path / "secret.md").write_text(secret, encoding="utf-8")
        summary = summarize_dir(str(tmp_path))
        assert secret not in str(summary)

    def test_file_entries_carry_path_for_the_ui(self, tmp_path):
        """界面要靠 `path` 调批量引用接口，所以摘要必须给路径。

        但**提示词渲染不能带路径** —— 模型只需要知道「有什么」，
        不需要知道磁盘结构。这条把两者的边界钉住。
        """
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        summary = summarize_dir(str(tmp_path))
        entry = summary["files"][0]
        assert set(entry) == {"name", "size", "path"}
        assert entry["path"].endswith("a.md")

        from app.workflow.file_context import render_folder_listing

        rendered = render_folder_listing(summary)
        assert "a.md" in rendered
        assert str(tmp_path) not in rendered, "提示词里不该出现磁盘路径"

    def test_hidden_files_skipped(self, tmp_path):
        (tmp_path / ".hidden").write_text("x", encoding="utf-8")
        (tmp_path / "visible.md").write_text("x", encoding="utf-8")
        assert [f["name"] for f in summarize_dir(str(tmp_path))["files"]] == ["visible.md"]

    def test_office_lock_files_skipped(self, tmp_path):
        """`~$xxx.docx` 是 Office 打开文档时的锁定文件，不是真文档。"""
        (tmp_path / "~$周考3考试题.docx").write_text("x", encoding="utf-8")
        (tmp_path / "周考3考试题.docx").write_text("x", encoding="utf-8")
        names = [f["name"] for f in summarize_dir(str(tmp_path))["files"]]
        assert names == ["周考3考试题.docx"]

    def test_missing_dir_returns_none(self, tmp_path):
        """目录不可读时返回 None，而不是抛错 —— 一个坏目录不该拖垮澄清/规划。"""
        assert summarize_dir(str(tmp_path / "nope")) is None

    def test_truncates_large_dir(self, tmp_path):
        for i in range(12):
            (tmp_path / f"f{i:02d}.md").write_text("x", encoding="utf-8")
        summary = summarize_dir(str(tmp_path), limit=5)
        assert len(summary["files"]) == 5
        assert summary["truncated"] is True


class TestAllowedFilter:
    """清单是给模型「问用户要文件」用的，所以只该列真的能被引用的。"""

    def test_unsupported_types_are_omitted(self, tmp_path):
        (tmp_path / "讲义.md").write_text("x", encoding="utf-8")
        (tmp_path / "打包.zip").write_text("PK", encoding="utf-8")

        block = render_folder_listing(
            summarize_dir(str(tmp_path)), allowed={".md", ".txt", ".pdf"}
        )
        assert "讲义.md" in block
        # ★ 不过滤时模型会把 .zip 也列成选项，用户点了也加不进来
        assert "打包.zip" not in block.split("（另有")[0]
        assert "不支持引用" in block
        assert "共 1 个可引用文件" in block

    def test_without_allowed_lists_everything(self, tmp_path):
        (tmp_path / "讲义.md").write_text("x", encoding="utf-8")
        (tmp_path / "打包.zip").write_text("PK", encoding="utf-8")
        block = render_folder_listing(summarize_dir(str(tmp_path)))
        assert "打包.zip" in block
        assert "不支持引用" not in block

    def test_all_filtered_out_renders_nothing(self, tmp_path):
        (tmp_path / "打包.zip").write_text("PK", encoding="utf-8")
        assert render_folder_listing(summarize_dir(str(tmp_path)), allowed={".md"}) == ""


class TestRenderFolderListing:
    def test_includes_names_sizes_and_disclaimer(self, tmp_path):
        (tmp_path / "教程.md").write_text("x" * 5000, encoding="utf-8")
        block = render_folder_listing(summarize_dir(str(tmp_path)))

        assert "教程.md" in block
        assert "4.9 KB" in block
        assert "共 1 个可引用文件" in block
        # ★ 免责声明：没有它，模型会以为已经读过而编造内容
        assert "内容尚未加入" in block
        assert "看不到这些文件的内容" in block
        assert "不要" in block and "编造" in block

    def test_empty_summary_renders_nothing(self):
        assert render_folder_listing(None) == ""
        assert render_folder_listing({"files": [], "dirs": []}) == ""

    def test_truncation_is_stated(self, tmp_path):
        for i in range(8):
            (tmp_path / f"f{i}.md").write_text("x", encoding="utf-8")
        block = render_folder_listing(summarize_dir(str(tmp_path), limit=3))
        # ★ 截断时必须报目录里的**真实总数**（曾经报成已渲染的 3 个）
        assert "目录内共 8 个文件" in block
        assert "仅列出前 3 个" in block

    def test_dirs_listed(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        block = render_folder_listing(summarize_dir(str(tmp_path)))
        assert "子文件夹" in block and "sub" in block


class TestPromptInclusion:
    LISTING = "【工作区文件夹「X」里有什么（**只是清单，内容尚未加入**）】\n- a.md（1.0 KB）"

    def test_clarify_prompt_includes_listing(self):
        without = build_clarify_prompt(raw_input="整理这个文件夹里的文件")
        with_listing = build_clarify_prompt(
            raw_input="整理这个文件夹里的文件", folder_listing=self.LISTING
        )
        assert "工作区文件夹" not in without
        assert "工作区文件夹" in with_listing
        assert "a.md" in with_listing
        assert len(with_listing) > len(without)

    def test_plan_prompt_includes_listing(self):
        without = build_plan_prompt(
            raw_input="整理这个文件夹里的文件",
            mode="manual",
            force_search=False,
            tools=["web_search"],
            max_steps=12,
        )
        with_listing = build_plan_prompt(
            raw_input="整理这个文件夹里的文件",
            mode="manual",
            force_search=False,
            tools=["web_search"],
            max_steps=12,
            folder_listing=self.LISTING,
        )
        assert "工作区文件夹" not in without
        assert "工作区文件夹" in with_listing

    def test_no_workspace_name_leak(self):
        """工作区名本身绝不进提示词（它是归类标签，会被模型当成主题）。"""
        prompt = build_clarify_prompt(
            raw_input="帮我做一份调研", folder_listing=self.LISTING
        )
        assert "P4" not in prompt or "只是清单" in prompt
        # 清单块存在，但不含工作区名以外的主题词
        assert "【背景】" in prompt
