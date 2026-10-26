"""AI 创作用例（文生图 / 文生视频）。

## 供应商与参数都收在这里

- 智谱 CogView（图）与阿里百炼通义万相（图 / 视频）由
  `app/rag/generation.py` 的注册表分派，本模块只负责**业务规则**：
  哪些比例合法、提示词怎么拼、产物怎么落盘、历史怎么记。
- 风格不是上游参数，只能靠提示词表达 —— 所以它被**追加**在原描述之后，
  不会覆盖用户自己写的细节。

## 视频为什么是「两段式」

百炼的视频任务要跑 1-3 分钟。直接挂在 HTTP 请求上，网关会先超时，
用户看到的是 504 而实际已经扣了费。所以：

    POST /creations/videos  → 提交任务，立刻返回一条 status=running 的记录
    GET  /creations/{id}    → 每次查询顺带问一次上游；完成了就下载落盘

这样做没有后台任务、也没有「服务重启后任务丢失」的问题：任务真相在
上游的 task_id 上，本地只是缓存。多 worker 下也天然一致。

分层：本模块属 services 层，可依赖 repositories / rag / utils。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_model_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.rag import generation
from app.repositories import creation_repo as repo

logger = get_logger(__name__)

__all__ = [
    "IMAGE_RATIOS",
    "STYLE_PRESETS",
    "VIDEO_RATIOS",
    "CreationService",
    "build_prompt",
]

#: 图片比例 → (像素尺寸, 中文名, 用途)。这些尺寸 CogView 与通义万相都接受。
IMAGE_RATIOS: dict[str, tuple[str, str, str]] = {
    "1:1": ("1024x1024", "方形", "头像 / 封面"),
    "4:3": ("1152x864", "横图", "照片 / 展示"),
    "3:4": ("864x1152", "竖图", "海报 / 人物"),
    "16:9": ("1344x768", "宽屏", "壁纸 / 场景"),
    "9:16": ("768x1344", "竖屏", "手机 / 故事"),
}

#: 视频比例 → 像素尺寸。
#: ⚠️ 值必须来自百炼白名单（`1080*1920,1920*1080,1440*1440,1632*1248,1248*1632,...`），
#: 传其他值任务会直接 FAILED —— 上游不会替你纠正比例。
VIDEO_RATIOS: dict[str, tuple[str, str, str]] = {
    "16:9": ("1920x1080", "横屏", "标准视频"),
    "9:16": ("1080x1920", "竖屏", "短视频"),
    "1:1": ("1440x1440", "方形", "社交媒体"),
    "4:3": ("1632x1248", "横图", "复古宽幅"),
    "3:4": ("1248x1632", "竖图", "人物特写"),
}

#: 视频时长（秒）。百炼当前按 5 秒计费，不做多档选择以免误导。
VIDEO_DURATION_SECONDS = 5

#: 风格 id → (展示名, 追加到提示词的修饰语)。
STYLE_PRESETS: dict[str, tuple[str, str]] = {
    "": ("无风格", ""),
    "photo": ("摄影", "摄影作品，真实光线，浅景深，高细节"),
    "illustration": ("插画", "手绘插画风格，柔和笔触，温暖配色"),
    "watercolor": ("水彩", "水彩画风格，晕染层次，纸面纹理"),
    "render3d": ("3D 渲染", "3D 渲染，柔和体积光，细腻材质，C4D 质感"),
    "guofeng": ("国风", "中国画风格，青绿设色，留白构图，意境悠远"),
    "anime": ("动漫", "日系动漫风格，清晰线条，明亮色彩"),
    "cyberpunk": ("赛博朋克", "赛博朋克风格，霓虹灯光，雨夜反光，高对比"),
    "lineart": ("极简线稿", "极简线稿，单色，大量留白，干净构图"),
}

MAX_REFERENCE_CHARS = 400


def build_prompt(prompt: str, *, style: str = "", reference_desc: str = "") -> str:
    """用户描述 + 风格修饰 + 参考画面。

    顺序是刻意的：用户原话在最前（权重最高），风格其次，参考图在最后。
    反过来参考图的细节会盖掉用户真正想要的东西。
    """
    parts = [prompt.strip()]
    style_prompt = STYLE_PRESETS.get(style, ("", ""))[1]
    if style_prompt:
        parts.append(style_prompt)
    reference = (reference_desc or "").strip()[:MAX_REFERENCE_CHARS]
    if reference:
        parts.append(f"参考画面：{reference}")
    return "，".join(part for part in parts if part)


def _sniff_ext(data: bytes) -> tuple[str, str]:
    """按魔数判断媒体格式。

    不信 `Content-Type`：上游偶尔给 `application/octet-stream`，而扩展名
    直接决定浏览器怎么渲染 —— 猜错会得到一个「下载不了也播放不了」的文件。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[4:8] == b"ftyp":
        return "mp4", "video/mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm", "video/webm"
    return "bin", "application/octet-stream"


class CreationService:
    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings or get_model_settings()

    # ── 能力与参数 ────────────────────────────────────────────────

    def options(self) -> dict[str, Any]:
        images = generation.available_image_models()
        videos = generation.available_video_models()
        enabled = bool(images)
        i2i = [m for m in images if m.get("needs_image")]
        i2v = [m for m in videos if m.get("needs_image")]
        return {
            "enabled": enabled,
            "message": "" if enabled else _no_provider_message(),
            "models": images,
            "default_model": images[0]["id"] if images else "",
            #: 图生图能力：默认模型（第一个可用）取非图生图的那个，
            #: 图生图需要用户显式选模型并上传源图
            "i2i_enabled": bool(i2i),
            "i2i_models": i2i,
            "ratios": [
                {"id": key, "label": value[1], "size": value[0], "hint": value[2]}
                for key, value in IMAGE_RATIOS.items()
            ],
            "styles": [
                {"id": key, "label": value[0]} for key, value in STYLE_PRESETS.items()
            ],
            "video_enabled": bool(videos),
            "video_models": videos,
            "default_video_model": videos[0]["id"] if videos else "",
            #: 视频不可用时**必须说明原因**。否则界面只能把整组置灰，
            #: 用户既不知道是没配置密钥，还是平台没开放（实测是 40212）。
            "video_message": "" if videos else _video_unavailable_message(),
            "i2v_enabled": bool(i2v),
            "i2v_models": i2v,
            "video_ratios": [
                {"id": key, "label": value[1], "size": value[0], "hint": value[2]}
                for key, value in VIDEO_RATIOS.items()
                if not _video_ratio_filter(videos, key)
            ],
            "video_duration_seconds": VIDEO_DURATION_SECONDS,
            "max_prompt_chars": 1000,
        }

    # ── 历史 ──────────────────────────────────────────────────────

    def list(self, username: str, *, limit: int = 30) -> dict[str, Any]:
        items = [_present(row) for row in repo.list_creations(username, limit=limit)]
        enabled = bool(generation.available_image_models())
        return {
            "creations": items,
            "enabled": enabled,
            "message": "" if enabled else _no_provider_message(),
        }

    async def get(self, username: str, creation_id: str) -> dict[str, Any]:
        """取单条记录；未完成的任务在查询时会顺带推进状态。"""
        row = self._owned(username, creation_id)
        if row.get("status") in ("queued", "running"):
            row = (
                await self._advance_video(row)
                if row.get("kind") == "video"
                else await self._advance_image(row)
            )
        return _present(row)

    def get_media(self, username: str, creation_id: str) -> dict[str, Any]:
        row = self._owned(username, creation_id)
        if row.get("status") != "done":
            raise NotFoundError("作品还在生成中，暂时没有可读取的文件")
        path = Path(row["image_path"] or "")
        if not path.is_file():
            raise NotFoundError("文件已丢失（可能被手动删除）")
        return {
            "path": str(path),
            "mime": row["mime"] or "application/octet-stream",
            "filename": f"{row['id']}.{path.suffix.lstrip('.') or 'bin'}",
        }

    def delete(self, username: str, creation_id: str) -> dict[str, Any]:
        row = repo.delete_creation(creation_id, username)
        if row is None:
            raise NotFoundError("创作记录不存在")
        if row["image_path"]:
            try:
                Path(row["image_path"]).unlink(missing_ok=True)
            except OSError as exc:  # noqa: BLE001
                logger.warning(f"删除作品文件失败（记录已删除）: {exc}")
        return {"ok": True, "id": creation_id}

    # ── 图片 ──────────────────────────────────────────────────────

    async def create(
        self,
        *,
        username: str,
        prompt: str,
        model: str = "",
        ratio: str = "1:1",
        style: str = "",
        reference_desc: str = "",
        source_image: Optional[bytes] = None,
        source_mime: str = "image/png",
    ) -> dict[str, Any]:
        text, final_prompt = self._prepare(prompt, style, reference_desc)
        if ratio not in IMAGE_RATIOS:
            raise ValidationError(
                f"不支持的画幅比例: {ratio}；可选：{', '.join(IMAGE_RATIOS)}"
            )
        size = IMAGE_RATIOS[ratio][0]
        resolved = self._resolve_media_model(model, kind="image")

        creation_id = repo.new_id()

        # ModelScope 是**单并发排队**的（实测同一账号排十几分钟很常见），
        # 把这段等待挂在 HTTP 请求上必然先撞网关超时 → 用户看到 502 但图其实成了。
        # 所以它走「提交任务 → 落 running 行 → 前端轮询」，与视频同一套。
        if generation.provider_of(resolved) == "modelscope":
            task_id, provider = await generation.submit_image_task(
                final_prompt,
                model=resolved,
                size=size,
                source_image=source_image,
                source_mime=source_mime,
            )
            row = repo.create_creation(
                creation_id=creation_id,
                username=username,
                prompt=text,
                final_prompt=final_prompt,
                model=resolved,
                size=size,
                ratio=ratio,
                style=style,
                reference_desc=(reference_desc or "")[:MAX_REFERENCE_CHARS],
                image_path="",
                mime="image/png",
                kind="image",
                status="running",
                task_id=task_id,
                provider=provider,
            )
            logger.info(f"出图任务已提交（排队中）: id={creation_id} task={task_id}")
            return _present(row)

        started = time.perf_counter()
        result = await generation.generate_image(
            final_prompt,
            model=resolved,
            size=size,
            timeout=float(get_model_settings().image_timeout_seconds),
            source_image=source_image,
            source_mime=source_mime,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        ext, mime = _sniff_ext(result.data)
        path = repo.image_dir() / f"{creation_id}.{ext}"
        try:
            path.write_bytes(result.data)
        except OSError as exc:  # noqa: BLE001
            raise ValidationError(f"文件保存失败：{exc}") from exc

        try:
            row = repo.create_creation(
                creation_id=creation_id,
                username=username,
                prompt=text,
                final_prompt=final_prompt,
                model=resolved,
                size=size,
                ratio=ratio,
                style=style,
                reference_desc=(reference_desc or "")[:MAX_REFERENCE_CHARS],
                image_path=str(path),
                mime=mime,
                size_bytes=len(result.data),
                elapsed_ms=elapsed_ms,
                kind="image",
                status="done",
                provider=result.provider,
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise

        logger.info(
            f"出图完成: user={username} id={creation_id} model={resolved} "
            f"provider={result.provider} {len(result.data) // 1024}KB {elapsed_ms}ms"
        )
        return _present(row)

    # ── 视频 ──────────────────────────────────────────────────────

    async def create_video(
        self,
        *,
        username: str,
        prompt: str,
        model: str = "",
        ratio: str = "16:9",
        style: str = "",
        reference_desc: str = "",
        source_image: Optional[bytes] = None,
        source_mime: str = "image/png",
    ) -> dict[str, Any]:
        text, final_prompt = self._prepare(
            prompt, style, reference_desc, allow_empty=bool(source_image)
        )
        if not text and source_image:
            # 图生视频可以不给指令（用户只想让这张图动起来），
            # 给一句中性描述，避免上游把空 prompt 当成错误
            text = "镜头缓慢推进，画面自然运动，保持主体一致"
            final_prompt = build_prompt(text, style=style, reference_desc=reference_desc)
        if ratio not in VIDEO_RATIOS:
            raise ValidationError(
                f"不支持的视频比例: {ratio}；可选：{', '.join(VIDEO_RATIOS)}"
            )
        size = VIDEO_RATIOS[ratio][0]
        resolved = self._resolve_media_model(model, kind="video")

        creation_id = repo.new_id()
        task_id, provider = await generation.submit_video(
            final_prompt,
            model=resolved,
            size=size,
            duration=VIDEO_DURATION_SECONDS,
            source_image=source_image,
            source_mime=source_mime,
        )

        row = repo.create_creation(
            creation_id=creation_id,
            username=username,
            prompt=text,
            final_prompt=final_prompt,
            model=resolved,
            size=size,
            ratio=ratio,
            style=style,
            reference_desc=(reference_desc or "")[:MAX_REFERENCE_CHARS],
            image_path="",
            mime="video/mp4",
            kind="video",
            status="running",
            task_id=task_id,
            provider=provider,
        )
        logger.info(
            f"视频任务已提交: user={username} id={creation_id} model={resolved} "
            f"task={task_id}"
        )
        return _present(row)

    async def _advance_image(self, row: dict[str, Any]) -> dict[str, Any]:
        """向上游问一次图像任务；完成了就下载落盘。

        排队期间每次查询只花一次 GET，前端可以放心地每 4 秒问一次 ——
        真正的等待成本在上游队列里，不在我们的连接上。
        """
        creation_id = row["id"]
        task = await generation.fetch_image_task(row["model"], row["task_id"])

        if task.status == "done":
            data = await generation.download_image(
                row.get("provider") or "modelscope", task.url
            )
            ext, mime = _sniff_ext(data)
            path = repo.image_dir() / f"{creation_id}.{ext}"
            path.write_bytes(data)
            # 排队时间也算进「用户等了多久」：它确实是用户的等待
            elapsed = int((time.time() - float(row.get("created_at") or time.time())) * 1000)
            updated = repo.update_creation(
                creation_id,
                image_path=str(path),
                mime=mime,
                bytes=len(data),
                status="done",
                error="",
                elapsed_ms=elapsed,
            )
            logger.info(
                f"出图完成: id={creation_id} model={row['model']} "
                f"{len(data) // 1024}KB 排队+生成 {elapsed // 1000}s"
            )
            return updated or row

        if task.status == "failed":
            updated = repo.update_creation(
                creation_id, status="failed", error=task.error or "生成失败"
            )
            logger.warning(f"出图失败: id={creation_id} {task.error or '未知原因'}")
            return updated or row

        return row

    async def _advance_video(self, row: dict[str, Any]) -> dict[str, Any]:
        """向上游问一次状态；完成了就下载落盘。

        失败**不删除记录**：用户需要看到「为什么失败」（尺寸不对 / 内容审核 /
        额度不足 / 平台未开放），静默消失比失败本身更难排查。
        """
        creation_id = row["id"]
        task = await generation.fetch_video_task(row["model"], row["task_id"])

        if task.status == "done":
            data = await generation.download_file(row.get("provider") or "modelscope", task.url)
            ext, mime = _sniff_ext(data)
            path = repo.image_dir() / f"{creation_id}.{ext}"
            path.write_bytes(data)
            updated = repo.update_creation(
                creation_id,
                image_path=str(path),
                mime=mime,
                bytes=len(data),
                status="done",
                error="",
            )
            logger.info(f"视频完成: id={creation_id} {len(data) // 1024}KB")
            return updated or row

        if task.status == "failed":
            updated = repo.update_creation(
                creation_id, status="failed", error=task.error or "生成失败"
            )
            logger.warning(f"视频失败: id={creation_id} {task.error or '未知原因'}")
            return updated or row

        return row

    # ── 内部 ──────────────────────────────────────────────────────

    def _owned(self, username: str, creation_id: str) -> dict[str, Any]:
        row = repo.get_creation(creation_id)
        # 别人的作品一律当作「不存在」：返回 403 等于告诉对方这个 id 是真的
        if row is None or row["username"] != username:
            raise NotFoundError("创作记录不存在")
        return row

    def _prepare(
        self,
        prompt: str,
        style: str,
        reference_desc: str,
        *,
        allow_empty: bool = False,
    ) -> tuple[str, str]:
        text = (prompt or "").strip()
        if not text and not allow_empty:
            raise ValidationError("请先描述你想要的画面")
        if style not in STYLE_PRESETS:
            raise ValidationError(f"未知的风格: {style}")
        return text, build_prompt(text, style=style, reference_desc=reference_desc)

    def _resolve_media_model(self, model: str, *, kind: str) -> str:
        """校验并解析生成模型。

        与对话模型同一原则：**不静默回退**。用户以为切到了 A，实际用 B 出图，
        得到的差异会被归因成「模型变差了」而不是「根本没切成功」。
        """
        pool = (
            generation.available_image_models()
            if kind == "image"
            else generation.available_video_models()
        )
        ids = [item["id"] for item in pool]
        if not ids:
            raise ValidationError(_no_provider_message())
        if not model:
            return ids[0]
        if model not in ids:
            kind_label = "图像" if kind == "image" else "视频"
            raise ValidationError(
                f"不支持的{kind_label}模型: {model}；可选：{', '.join(ids)}"
            )
        return model

    # ── 参考图 ────────────────────────────────────────────────────

    async def describe_reference(
        self, *, filename: str, contents: bytes
    ) -> dict[str, Any]:
        """把参考图读成画面描述（供提示词复用）。"""
        from app.rag import image_gen
        from app.utils.files import image_mime, validate_image_upload

        ext = validate_image_upload(filename, len(contents))
        description = await image_gen.describe_reference(contents, image_mime(ext))
        if not description:
            raise ValidationError("没能从这张图里读出画面描述，换一张试试")
        return {"description": description[:MAX_REFERENCE_CHARS], "filename": filename}


def _video_ratio_filter(videos: list[dict], ratio_id: str) -> bool:
    """True = 该比例要被排除掉。

    供应商之间的尺寸枚举并不相通（智谱不收 4:3），所以比例表要按
    首个可用视频模型的供应商裁剪 —— 否则用户选完才发现上游 400。
    """
    if not videos:
        return False
    allowed = generation.supported_video_ratios(str(videos[0].get("provider") or ""))
    return bool(allowed) and ratio_id not in allowed


def _no_provider_message() -> str:
    settings = get_model_settings()
    if not (settings.modelscope_api_key or "").strip():
        return (
            "当前没有可用的生成服务：请在 .env 中设置 MODELSCOPE_API_KEY"
            "（ModelScope 文生图 / 图生图）。修改后需重启后端。"
        )
    allowed = (settings.creation_providers or "").strip()
    return (
        "当前没有可用的生成服务：请检查 CREATION_PROVIDERS"
        f"（当前为 {allowed or '空'}）与对应供应商的密钥。修改后需重启后端。"
    )


def _video_unavailable_message() -> str:
    """视频不可用时给出一句能解释清楚的话。

    两种情况必须区分：密钥没配（用户的活）vs 平台没开放（不是用户的活）。
    实测该账号调用 Wan/CogVideoX 全部返回 40212，属于后者。
    """
    settings = get_model_settings()
    if "modelscope" not in (settings.creation_providers or ""):
        return "视频生成未启用：CREATION_PROVIDERS 中未包含 modelscope。"
    if not (settings.modelscope_api_key or "").strip():
        return "视频生成不可用：未配置 MODELSCOPE_API_KEY。"
    if not settings.modelscope_video_enabled:
        return (
            "视频生成不可用：ModelScope 暂未对当前账号开放视频 API（实测返回 40212），"
            "平台开放后设 MODELSCOPE_VIDEO_ENABLED=true 即可启用。"
        )
    return "视频生成不可用：当前没有可调用的视频模型。"


def _present(row: dict[str, Any]) -> dict[str, Any]:
    """把库里的行整理成响应结构（绝不带 `image_path` 这种本机路径）。"""
    kind = row.get("kind") or "image"
    ready = (row.get("status") or "done") == "done" and bool(row.get("image_path"))
    return {
        "id": row["id"],
        "kind": kind,
        "status": row.get("status") or "done",
        "error": row.get("error") or "",
        "provider": row.get("provider") or "",
        "prompt": row["prompt"],
        "final_prompt": row["final_prompt"],
        "model": row["model"],
        "size": row["size"],
        "ratio": row["ratio"],
        "style": row["style"],
        "reference_desc": row["reference_desc"],
        "mime": row["mime"],
        "bytes": row["bytes"],
        "elapsed_ms": row["elapsed_ms"],
        "created_at": row["created_at"],
        # 只有真正有文件时才给地址，避免前端对着 404 反复重试
        "media_url": f"/creations/{row['id']}/file" if ready else "",
        "image_url": f"/creations/{row['id']}/image" if ready and kind == "image" else "",
    }
