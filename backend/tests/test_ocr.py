"""Tesseract OCR 适配层测试。

覆盖三类关键行为：
  1. 中文文本后处理（chi_sim 会在汉字间插空格，不清理会污染分块与检索）
  2. **失败可区分** —— 引擎缺失 / 缺语言包 / 图片无文字，是三种不同的结果，
     旧实现一律返回 [] 让调用方无从判断
  3. 领域异常必须穿透 `load_any`，不能被通用 except 吞掉

真实 OCR 用例在没有 Tesseract 的机器上自动跳过，CI 不因环境缺组件而红。
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.config import clear_settings_cache
from app.core.exceptions import OcrUnavailableError
from app.document import load_any
from app.document.ocr import (
    available_languages,
    normalize_cjk_spacing,
    probe,
    reset_probe_cache,
)


@pytest.fixture
def fresh_ocr(monkeypatch):
    """清空配置与探测缓存，避免用例间串味。"""
    clear_settings_cache()
    reset_probe_cache()
    yield monkeypatch
    clear_settings_cache()
    reset_probe_cache()


@pytest.fixture
def sample_png(tmp_path):
    """生成一张带中英文的测试图。"""
    path = tmp_path / "sample.png"
    img = Image.new("RGB", (760, 220), "white")
    draw = ImageDraw.Draw(img)
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    try:
        font = ImageFont.truetype(font_path, 36)
    except OSError:  # 非 Windows / 缺字体时退回默认位图字体
        font = ImageFont.load_default()
    draw.text((24, 30), "本地知识库问答系统", font=font, fill="black")
    draw.text((24, 100), "OCR Test 12345", font=font, fill="black")
    img.save(path)
    return str(path)


# ---------------------------------------------------------------------------
#  文本后处理
# ---------------------------------------------------------------------------


class TestNormalizeCjkSpacing:
    def test_removes_spaces_between_han_chars(self):
        assert normalize_cjk_spacing("本 地 知 识 库") == "本地知识库"

    def test_keeps_space_between_han_and_latin(self):
        # 汉字与拉丁之间的空格是真实分词边界，不能一起清掉
        out = normalize_cjk_spacing("中文 OCR 识别")
        assert out == "中文 OCR 识别"

    def test_removes_spaces_between_han_and_fullwidth_punct(self):
        assert normalize_cjk_spacing("你好 ， 世界 ！") == "你好，世界！"

    def test_preserves_newlines(self):
        out = normalize_cjk_spacing("第一行\n第二行")
        assert out == "第一行\n第二行"

    def test_collapses_excess_blank_lines(self):
        assert normalize_cjk_spacing("A\n\n\n\nB") == "A\n\nB"

    def test_empty_input(self):
        assert normalize_cjk_spacing("") == ""


# ---------------------------------------------------------------------------
#  可用性探测：三类失败必须给出不同原因
# ---------------------------------------------------------------------------


class TestProbe:
    def test_disabled_reports_reason(self, fresh_ocr):
        fresh_ocr.setenv("OCR_ENABLED", "false")
        status = probe(refresh=True)
        assert status["available"] is False
        assert "关闭" in status["reason"]

    def test_missing_binary_reports_reason(self, fresh_ocr, tmp_path):
        fresh_ocr.setenv("OCR_TESSERACT_CMD", str(tmp_path / "nope.exe"))
        status = probe(refresh=True)
        assert status["available"] is False
        assert status["tesseract_cmd"] is None
        assert "未找到" in status["reason"]

    def test_missing_language_reports_which_one(self, fresh_ocr, tmp_path):
        """语言包目录存在但没有目标语言 —— 原因里必须点名缺哪个。"""
        empty = tmp_path / "tessdata"
        empty.mkdir()
        (empty / "eng.traineddata").write_bytes(b"x")  # 占位，使目录被认作语言包目录
        fresh_ocr.setenv("OCR_TESSDATA_PREFIX", str(empty))
        fresh_ocr.setenv("OCR_LANGUAGES", "chi_sim+eng")
        status = probe(refresh=True)
        assert status["available"] is False
        assert status["missing_languages"] == ["chi_sim"]
        assert "chi_sim" in status["reason"]

    def test_available_languages_lists_traineddata(self, tmp_path):
        d = tmp_path / "tessdata"
        d.mkdir()
        for name in ("eng", "chi_sim"):
            (d / f"{name}.traineddata").write_bytes(b"x")
        (d / "readme.txt").write_text("ignore me", encoding="utf-8")
        assert available_languages(str(d)) == ["chi_sim", "eng"]


# ---------------------------------------------------------------------------
#  失败可区分：引擎不可用时必须抛领域异常，而不是静默返回空
# ---------------------------------------------------------------------------


class TestUnavailableSurfaces:
    def test_image_to_text_raises_when_unavailable(self, fresh_ocr, tmp_path, sample_png):
        from app.document.ocr import image_to_text

        fresh_ocr.setenv("OCR_TESSERACT_CMD", str(tmp_path / "nope.exe"))
        with pytest.raises(OcrUnavailableError):
            image_to_text(sample_png)

    def test_load_any_propagates_domain_error(self, fresh_ocr, tmp_path, sample_png):
        """回归：load_any 曾把所有异常吞成 []，
        导致「环境缺 OCR」被误报成「文件没内容」，用户无从下手。
        """
        fresh_ocr.setenv("OCR_TESSERACT_CMD", str(tmp_path / "nope.exe"))
        with pytest.raises(OcrUnavailableError):
            load_any(sample_png)

    def test_ocr_unavailable_is_a_parse_error_with_own_code(self):
        from app.core.exceptions import DocumentParseError

        exc = OcrUnavailableError()
        assert isinstance(exc, DocumentParseError)
        assert exc.status_code == 422
        # 与通用解析失败区分开，前端才能给出「去装 OCR」的提示
        assert exc.code == "ocr_unavailable"
        assert DocumentParseError().code == "document_parse_failed"


# ---------------------------------------------------------------------------
#  真实 OCR（无 Tesseract 时跳过）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not probe(refresh=True)["available"], reason="本机未安装可用的 Tesseract OCR"
)
class TestRealOcr:
    def test_recognizes_chinese_and_latin(self, sample_png):
        docs = load_any(sample_png)
        assert len(docs) == 1
        text = docs[0].page_content
        # 汉字间不应残留空格
        assert "本地知识库" in text
        assert "本地 知识" not in text
        assert docs[0].metadata["file_type"] == "png"

    def test_blank_image_returns_empty_not_error(self, tmp_path):
        """空白图是「没有文字」而非「引擎坏了」，必须是空列表、不能抛异常。"""
        path = tmp_path / "blank.png"
        Image.new("RGB", (200, 100), "white").save(path)
        assert load_any(str(path)) == []
