"""文本类格式（源码/配置/数据）按原文读入。

背景：用户做前端类任务时要把**已有的 HTML 页面**喂给模型当参考，
而 `.html` 此前不在白名单里 —— 选择器里看得见、一点就被拒。
这条测试锁住「能读、读到的就是原文」。
"""

from __future__ import annotations

import pytest

from app.document import SUPPORTED_EXTENSIONS, load_any

TEXT_FORMATS = [".html", ".htm", ".js", ".css", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log", ".sql"]


class TestTextFormatsSupported:
    def test_all_new_formats_are_supported(self):
        lowered = {ext.lower() for ext in SUPPORTED_EXTENSIONS}
        missing = [ext for ext in TEXT_FORMATS if ext not in lowered]
        assert not missing, f"未纳入白名单: {missing}"

    @pytest.mark.parametrize("ext", [".html", ".js", ".css", ".json"])
    def test_loads_raw_content(self, tmp_path, ext):
        """★ 读到的必须是**原文**。

        刻意不剥离 HTML 标签：工作流要的是「拿它当源码来改」，
        标签、内联 CSS/JS 恰恰是要修改的对象；剥成纯文本反而把要改的东西删了。
        """
        target = tmp_path / f"sample{ext}"
        body = '<div class="a" onclick="go()">你好</div>\n<style>.a{color:red}</style>'
        target.write_text(body, encoding="utf-8")

        docs = load_any(str(target))
        assert docs, f"{ext} 应当能被读入"
        content = "\n".join(d.page_content for d in docs)
        assert "你好" in content
        # 标签与内联样式都保留
        assert "<div" in content
        assert "color:red" in content

    def test_markup_still_uses_plain_text_loader(self):
        from app.document.parser import LOADER_MAPPING, load_text

        for ext in TEXT_FORMATS:
            assert LOADER_MAPPING[ext] is load_text, ext

    def test_binary_and_office_formats_unchanged(self):
        """新增类型不能把原有的解析器挤掉。"""
        from app.document.parser import LOADER_MAPPING

        assert LOADER_MAPPING[".pdf"].__name__ == "load_pdf"
        assert LOADER_MAPPING[".docx"].__name__ == "load_docx"
        assert LOADER_MAPPING[".png"].__name__ == "load_image_ocr"
