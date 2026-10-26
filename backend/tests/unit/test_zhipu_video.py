"""智谱 CogVideoX-3 视频接入：参数收敛与错误翻译。

不触网 —— 真调一次要花 1-3 分钟且按次计费，不适合进单测。
这里钉住三件最容易写错的事：
  1. 尺寸必须落在智谱的枚举里（传 1440x1440 会被 400 拒掉）
  2. 提示词超过 512 字要截断（智谱硬限制，超了同样 400）
  3. 错误码要翻成人话（`1113` 直译成「余额不足」比原样抛给用户有用）
"""

from __future__ import annotations

import pytest

from app.rag.zhipu_video import (
    ALLOWED_SIZES,
    MAX_PROMPT_CHARS,
    _fit_size,
    _reason,
    build_payload,
)


class TestFitSize:
    @pytest.mark.parametrize("size", ALLOWED_SIZES)
    def test_allowed_sizes_pass_through(self, size):
        assert _fit_size(size) == size

    def test_square_from_bailian_size_is_mapped(self):
        """1440x1440 是百炼的 1:1 尺寸，智谱没有这个枚举。"""
        assert _fit_size("1440x1440") == "1024x1024"

    def test_landscape_43_falls_back_to_16_9(self):
        assert _fit_size("1632x1248") == "1920x1080"

    def test_portrait_falls_back_to_9_16(self):
        assert _fit_size("1248x1632") == "1080x1920"

    def test_star_separator_is_accepted(self):
        """上游偶有 `1920*1080` 写法（百炼风格），这里也要能读懂。"""
        assert _fit_size("1920*1080") == "1920x1080"

    def test_garbage_returns_none(self):
        """解析不了就不传 size，让上游用默认值 —— 比传个错误值更安全。"""
        assert _fit_size("huge") is None
        assert _fit_size("") is None
        assert _fit_size(None) is None


class TestBuildPayload:
    def test_basic_payload(self):
        payload = build_payload("一只猫", model="cogvideox-3", size="1920x1080")
        assert payload["prompt"] == "一只猫"
        assert payload["size"] == "1920x1080"
        assert payload["quality"] == "quality"
        assert payload["with_audio"] is True

    def test_audio_can_be_disabled(self):
        payload = build_payload("猫", model="cogvideox-3", with_audio=False)
        assert "with_audio" not in payload

    def test_prompt_is_truncated(self):
        payload = build_payload("猫" * 900, model="cogvideox-3")
        assert len(payload["prompt"]) == MAX_PROMPT_CHARS

    def test_image_becomes_data_url(self):
        payload = build_payload("动起来", model="cogvideox-3", source_image=b"fake")
        assert payload["image_url"].startswith("data:image/png;base64,")

    def test_prompt_is_stripped(self):
        assert build_payload("  猫  ", model="cogvideox-3")["prompt"] == "猫"


class TestReason:
    def test_known_codes_are_explained(self):
        assert "余额不足" in _reason({"error": {"code": "1113", "message": "x"}}, 400)
        assert "鉴权失败" in _reason({"error": {"code": "1001", "message": "x"}}, 401)

    def test_unknown_code_keeps_message(self):
        reason = _reason({"error": {"code": "9999", "message": "something odd"}}, 500)
        assert "something odd" in reason

    def test_empty_body_falls_back_to_status(self):
        assert "HTTP 502" in _reason(None, 502)
