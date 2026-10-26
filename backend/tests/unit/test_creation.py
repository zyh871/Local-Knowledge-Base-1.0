"""AI 创作：参数解析、提示词拼装、模型校验与持久化。

**不触网、不真出图** —— 出图/出视频的成本与延迟都不适合放进单测。
这里只钉住「推理与校验」部分：哪些参数合法、拼出来的提示词是什么顺序、
历史记录（含视频任务状态）读写是否成对。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationError
from app.rag.bailian import to_dashscope_size
from app.rag.thinking import _delta_reasoning
from app.repositories import creation_repo as repo
from app.schemas.creation import CreationRequest, VideoRequest
from app.services.creation_service import (
    IMAGE_RATIOS,
    STYLE_PRESETS,
    VIDEO_RATIOS,
    CreationService,
    build_prompt,
)

#: CogView / 通义万相都能接受的画幅尺寸（宽 x 高）
UPSTREAM_IMAGE_SIZES = {
    "1024x1024",
    "768x1344",
    "864x1152",
    "1344x768",
    "1152x864",
    "1440x720",
    "720x1440",
}

#: 百炼 `wan2.2-t2v-plus` 的尺寸白名单（实测报错信息里给的就是这一串）。
#: 传别的值任务会 FAILED，而不是被上游悄悄改成最接近的 —— 所以必须逐个校验。
DASHSCOPE_VIDEO_SIZES = {
    "1080x1920",
    "1920x1080",
    "1440x1440",
    "1632x1248",
    "1248x1632",
    "480x832",
    "832x480",
    "624x624",
}


class TestPrompt:
    def test_user_prompt_comes_first(self):
        final = build_prompt("一只橘猫", style="watercolor", reference_desc="暖光窗台")
        assert final.startswith("一只橘猫")
        assert final.index("一只橘猫") < final.index("水彩")
        assert final.index("水彩") < final.index("暖光窗台")

    def test_no_style_no_reference_keeps_prompt_verbatim(self):
        assert build_prompt("  一只橘猫  ") == "一只橘猫"

    def test_unknown_style_does_not_crash(self):
        """风格 id 只来自前端，但接口是公开的：非法值应当退化成「不加修饰」。"""
        assert build_prompt("猫", style="not-a-style") == "猫"

    def test_reference_is_truncated(self):
        assert len(build_prompt("猫", reference_desc="细" * 1000)) < 600

    def test_style_table_has_empty_option(self):
        """必须能选「无风格」——否则用户想原样描述时无处可选。"""
        assert "" in STYLE_PRESETS


class TestRatios:
    def test_image_ratios_map_to_supported_sizes(self):
        for ratio, (size, _label, _hint) in IMAGE_RATIOS.items():
            assert size in UPSTREAM_IMAGE_SIZES, f"{ratio} → 上游不支持的尺寸 {size}"

    def test_video_ratios_map_to_dashscope_whitelist(self):
        for ratio, (size, _label, _hint) in VIDEO_RATIOS.items():
            assert size in DASHSCOPE_VIDEO_SIZES, f"{ratio} → 百炼不接受的尺寸 {size}"

    def test_size_conversion_uses_star_separator(self):
        assert to_dashscope_size("1024x1024") == "1024*1024"
        assert to_dashscope_size("1920X1080") == "1920*1080"


class TestModelResolution:
    @pytest.fixture()
    def service(self):
        return CreationService()

    def test_unknown_image_model_rejected(self, service):
        with pytest.raises(ValidationError) as excinfo:
            service._resolve_media_model("not-a-model", kind="image")
        assert "不支持" in str(excinfo.value)

    def test_video_model_is_not_accepted_as_image(self, service):
        with pytest.raises(ValidationError):
            service._resolve_media_model("wan2.2-t2v-plus", kind="image")

    def test_image_model_is_not_accepted_as_video(self, service):
        with pytest.raises(ValidationError):
            service._resolve_media_model("Qwen/Qwen-Image", kind="video")

    def test_i2i_model_requires_source_image(self, service):
        """图生图模型没有源图时必须直接拒绝，而不是让上游拿空图去猜。"""
        from app.rag.generation import needs_source_image

        assert needs_source_image("Qwen/Qwen-Image-Edit") is True
        assert needs_source_image("Qwen/Qwen-Image") is False


class TestModelScopePayload:
    """ModelScope 的请求体有三个容易写错的地方，逐个钉住。"""

    def test_source_image_is_sent_as_base64_list(self):
        from app.rag.modelscope import build_payload

        payload = build_payload(
            model="Qwen/Qwen-Image-Edit",
            prompt="把背景换成星空",
            size="1024x1024",
            source_image=b"fake-png-bytes",
        )
        # ① 必须是列表（文档示例就是列表，裸字符串编辑模型读不到）
        assert isinstance(payload["image_url"], list)
        assert payload["image_url"][0].startswith("data:image/png;base64,")
        # ② 有源图时不传 size：尺寸跟源图走，硬塞会把画面拉伸
        assert "size" not in payload

    def test_text_to_image_keeps_size(self):
        from app.rag.modelscope import build_payload

        payload = build_payload(model="Qwen/Qwen-Image", prompt="一只猫", size="1024x1024")
        assert payload["size"] == "1024x1024"
        assert "image_url" not in payload

    def test_succeed_status_is_terminal(self):
        """⚠️ 终止状态是 SUCCEED（不是 SUCCEEDED）。只认后者会一直轮询到任务被回收。"""
        from app.rag.modelscope import _parse_task

        task = _parse_task(
            {"task_status": "SUCCEED", "output_images": ["https://x/y.png"]}
        )
        assert task.done is True
        assert task.images == ["https://x/y.png"]

    def test_40212_maps_to_readable_reason(self):
        from app.rag.modelscope import _reason

        reason = _reason({"errors": {"message": "submit failed with code: 40212"}}, 400)
        assert "40212" in reason
        # 必须说清"不是你的参数问题"，否则用户会一直去调参数
        assert "未对当前账号开放" in reason


class TestRequestSchema:
    def test_empty_prompt_rejected(self):
        with pytest.raises(PydanticValidationError):
            CreationRequest(prompt="")

    def test_overlong_prompt_rejected(self):
        with pytest.raises(PydanticValidationError):
            CreationRequest(prompt="猫" * 1001)

    def test_defaults(self):
        request = CreationRequest(prompt="一只猫")
        assert request.ratio == "1:1"
        assert request.style == ""

    def test_video_defaults_to_landscape(self):
        """视频默认 16:9：竖屏需要用户明确选择，不能替他猜。"""
        assert VideoRequest(prompt="一只猫").ratio == "16:9"


class TestReasoningDelta:
    def test_reads_attribute(self):
        delta = SimpleNamespace(reasoning_content="先想一下")
        assert _delta_reasoning(delta) == "先想一下"

    def test_reads_model_extra(self):
        delta = SimpleNamespace(reasoning_content=None, model_extra={"reasoning_content": "想"})
        assert _delta_reasoning(delta) == "想"

    def test_missing_returns_empty(self):
        assert _delta_reasoning(SimpleNamespace()) == ""
        assert _delta_reasoning(None) == ""


class TestRepository:
    @pytest.fixture(autouse=True)
    def _tmp_db(self, tmp_path, monkeypatch):
        """把库与媒体目录都指到 tmp —— 绝不让单测写进真实的 data/。"""
        monkeypatch.setattr(repo, "_db_path", lambda: str(tmp_path / "creations.db"))
        monkeypatch.setattr(repo, "image_dir", lambda: tmp_path)
        repo.reset_connection()
        yield
        repo.reset_connection()

    def _create(self, creation_id="cr_test1", **overrides):
        payload = dict(
            creation_id=creation_id,
            username="alice",
            prompt="一只猫",
            final_prompt="一只猫，水彩",
            model="wanx2.1-t2i-turbo",
            size="1024x1024",
            ratio="1:1",
            style="watercolor",
            image_path="/tmp/x.png",
            size_bytes=123,
            elapsed_ms=456,
        )
        payload.update(overrides)
        return repo.create_creation(**payload)

    def test_roundtrip(self):
        created = self._create()
        assert created["id"] == "cr_test1"
        assert created["kind"] == "image"
        assert created["status"] == "done"
        assert [row["id"] for row in repo.list_creations("alice")] == ["cr_test1"]
        # 别人的历史里不应该出现这条
        assert repo.list_creations("bob") == []

    def test_video_task_can_be_updated(self):
        """视频是「先建 running 行，再回写文件」—— 这条链路必须能走通。"""
        self._create(
            creation_id="cr_vid",
            kind="video",
            status="running",
            task_id="t-123",
            image_path="",
            mime="video/mp4",
            provider="bailian",
        )
        updated = repo.update_creation(
            "cr_vid", image_path="/tmp/v.mp4", bytes=999, status="done", mime="video/mp4"
        )
        assert updated["status"] == "done"
        assert updated["bytes"] == 999
        assert updated["task_id"] == "t-123"

    def test_update_refuses_protected_fields(self):
        """`username` 一旦写入就不该被顺手改掉（等于把作品移到别人名下）。"""
        self._create()
        repo.update_creation("cr_test1", username="bob", status="failed")
        assert repo.get_creation("cr_test1")["username"] == "alice"
        assert repo.get_creation("cr_test1")["status"] == "failed"

    def test_delete_is_scoped_to_owner(self):
        self._create(creation_id="cr_test2")
        assert repo.delete_creation("cr_test2", "bob") is None
        assert repo.get_creation("cr_test2") is not None
        assert repo.delete_creation("cr_test2", "alice")["id"] == "cr_test2"
        assert repo.get_creation("cr_test2") is None
