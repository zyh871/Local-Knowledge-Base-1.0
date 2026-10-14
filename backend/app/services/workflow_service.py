"""AI 工作流用例层（P1：工作区 / 任务 / 临时文件引用）。

P1 只做「落库 + 上下文准备」：创建 run 后停在 `draft`，澄清（P4）、计划（P2）、
执行（P3）后续接管。因此本层的职责边界很清楚 —— **不做任何 LLM 调用**，
所以它整体是同步的、可预测的、易测的。

两个刻意的设计：

1. **不碰检索引擎。** 临时文件只作当前 run 的上下文，**不进 Chroma**。
   否则用户在对话模式提问时会突然命中工作流的临时文件 —— 那是错误行为，
   而且向量库文档增删要清 `cache:retrieval:*`，代价不对称。

2. **文件解析失败不阻断上传。** 文件本身是用户的资产，引擎/语言包缺失
   属于环境问题（`OcrUnavailableError`）。此处把原因作为 warning 返回，
   让用户知道「这个文件不会贡献上下文」，而不是让上传整个失败。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from app.core.config import get_workflow_settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    FileTooLargeError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.repositories import workflow_repo as repo
from app.utils.files import sanitize_filename, validate_extension
from app.workflow.executor import (
    needs_gate,
    plan_step_shape,
    runnable_next_step,
    settled_steps,
    tool_names,
    tool_required_args,
)

logger = get_logger(__name__)

_MODE_LABELS = {
    "manual": "人工审查",
    "auto": "AI 自动执行",
}
_MODES = [{"value": value, "label": label} for value, label in _MODE_LABELS.items()]

#: 澄清流**允许**写状态的那些 run 状态。
#:
#: 澄清与「生成任务书」是两条独立的 SSE 流，用户完全可能在澄清还没回来时
#: 就点了生成任务书。后写的一方获胜 —— 如果不管，澄清那条流会把已经进入
#: 「待批准」的任务改回「澄清中」，界面上批准按钮直接消失且没有任何错误。
#: 状态推进必须单调，所以这里用白名单而不是"总是写"。
_CLARIFY_WRITABLE_STATUSES = frozenset({"draft", "intake", "clarifying"})

#: 规划流允许回写状态的范围 —— 与澄清同理：任务书一旦就绪（`awaiting_approval`）
#: 或任务已开始执行，就不允许再被错误路径拉回去。
#: 比澄清多一个 `planning`：规划进行中报错，本来就该记成失败。
_PLAN_WRITABLE_STATUSES = frozenset({"draft", "intake", "clarifying", "planning"})

#: 阶段级互斥锁冲突时的提示文案。
_SCOPE_LABELS = {"clarify": "澄清", "plan": "生成任务书"}


def _serialize_per_run(scope: str):
    """装饰 SSE 流方法：同一条 run 的同一阶段，只允许一条流在跑。

    ★ 为什么要它
    -----------
    `clarify` / `plan` / `execute` 是三条**独立的 SSE 流**。用户完全可能在
    澄清还没回来时就点了「生成任务书」，于是两条流同时跑：重复烧 LLM 调用，
    同时争抢同一条 run 的状态。

    状态那半边已由 `_write_status_monotonic` 兜住（不会被写回退），
    但那解决的是「写坏」，不是「重复执行」—— 这两件事得分别处理。

    `execute` 一直有锁（`stream_execute_step`），`clarify` / `plan` 此前没有，
    这里补齐。

    ⚠️ 为什么用装饰器，而不是在函数体里写 `async with`：
    这两个函数各有 150+ 行，加一层 `async with` 意味着整段代码多缩进一级。
    纯缩进改动是最容易改出事故的一类改动 —— 装饰器让原有函数体一行都不动。

    Redis 不可用时退化为无锁（与 `execute` 的既有行为一致）：单机单进程
    本来就不存在跨 worker 并发的可能。
    """

    def decorator(method):
        @functools.wraps(method)
        async def wrapper(
            self, *, run_id: str, username: str, trace_id: str = "-", **extra
        ):
            from app.core.redis import LockNotAcquired, distributed_lock
            from app.schemas.qa import sse_frame

            lock_ctx = (
                distributed_lock(
                    self.redis,
                    f"workflow:{scope}:{run_id}",
                    ttl=int(getattr(self.config, "step_timeout_seconds", 180)) + 120,
                )
                if self.redis is not None
                else _no_lock()
            )

            try:
                async with lock_ctx:
                    async for frame in method(
                        self,
                        run_id=run_id,
                        username=username,
                        trace_id=trace_id,
                        **extra,
                    ):
                        yield frame
            except LockNotAcquired:
                # 响应头可能已经发出，所以只能转成 final 事件（与既有约定一致）
                label = _SCOPE_LABELS.get(scope, scope)
                yield sse_frame(
                    {
                        "type": "final",
                        "content": {
                            "ok": False,
                            "error": f"该任务正在{label}中，请等待它结束再试",
                        },
                        "trace_id": trace_id,
                    }
                )

        return wrapper

    return decorator


class WorkflowService:
    def __init__(
        self,
        *,
        settings: Any = None,
        chain: Any = None,
        graph: Any = None,
        redis: Any = None,
    ) -> None:
        self._settings = settings
        # P1 时本层完全不碰 LLM；P2 起 plan 相位需要模型与图。
        # 两者都是可选注入，因此工作区/任务/文件相关的用例
        # 依然可以在无图、无 Redis 的环境里独立测试。
        self.chain = chain
        self.graph = graph
        self.redis = redis

    # ------------------------------------------------------------------
    #  配置
    # ------------------------------------------------------------------

    @property
    def config(self) -> Any:
        return get_workflow_settings()

    def _require_graph(self) -> Any:
        if self.graph is None:
            raise DependencyUnavailableError(
                "工作流状态图未初始化（应用启动流程未完成）"
            )
        return self.graph

    @property
    def allowed_extensions(self) -> set[str]:
        from app.document import SUPPORTED_EXTENSIONS

        return {ext.lower() for ext in SUPPORTED_EXTENSIONS}

    def capabilities(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "enabled": cfg.enabled,
            "default_mode": cfg.default_mode,
            "modes": list(_MODES),
            "max_steps": cfg.max_steps,
            "max_clarify_rounds": cfg.max_clarify_rounds,
            "max_file_mb": cfg.max_file_mb,
            "file_inline_max_chars": cfg.file_inline_max_chars,
            "require_approval_tools": cfg.approval_tool_list,
            "tools": self._tool_names(),
            # 允许引用的文件类型。**必须下发给前端**：否则选择器只能列出所有文件，
            # 用户看得见、点得动、一导入就被拒 —— 会误以为「访问不了本地文件」。
            "allowed_extensions": sorted(self.allowed_extensions),
        }

    def _ensure_enabled(self) -> None:
        if not self.config.enabled:
            raise ValidationError("工作流模式已在配置中关闭（WORKFLOW_ENABLED=false）")

    # ------------------------------------------------------------------
    #  工作区
    # ------------------------------------------------------------------

    def list_workspaces(self) -> dict[str, Any]:
        """全局共享：所有登录用户可见，不按 username 过滤（见设计 §3.2）。"""
        self._ensure_enabled()
        return {"workspaces": repo.list_workspaces()}

    def create_workspace(
        self, *, name: str, username: str, local_dir: str = ""
    ) -> dict[str, Any]:
        self._ensure_enabled()
        clean = (name or "").strip()
        if not clean:
            raise ValidationError("工作区名称不能为空")
        bound = self._resolve_local_dir(local_dir)
        workspace = repo.create_workspace(clean, username, bound)
        # 目录在首次上传文件时才真正创建，避免空工作区留下一堆空目录
        logger.info(
            f"工作区已创建: {workspace['id']} name={clean} owner={username}"
            + (f" local_dir={bound}" if bound else "")
        )
        return workspace

    def _resolve_local_dir(self, raw: str) -> str:
        """校验要绑定的本地文件夹。

        绑定的是一个**真实存在**的目录才能通过 —— 否则用户会得到一个
        「看起来绑好了、点开文件夹却是空的」的工作区，比直接报错难排查得多。
        """
        text = (raw or "").strip()
        if not text:
            return ""
        if not self.config.fs_browse:
            raise ValidationError("当前部署已禁用本地文件夹访问")
        from app.workflow.local_fs import ensure_local_dir

        return ensure_local_dir(text)

    def bind_workspace_folder(
        self, *, workspace_id: str, local_dir: str, username: str
    ) -> dict[str, Any]:
        """绑定 / 解绑本地文件夹（传空串解绑）。"""
        workspace = self._require_workspace(workspace_id)
        self._assert_owner(workspace, username, action="修改")
        bound = self._resolve_local_dir(local_dir)
        updated = repo.set_workspace_local_dir(workspace_id, bound)
        if not updated:
            raise NotFoundError("工作区不存在")
        logger.info(
            f"工作区 {workspace_id} "
            + (f"已绑定本地文件夹 {bound}" if bound else "已解绑本地文件夹")
        )
        return updated

    def open_workspace_folder(
        self, *, workspace_id: str, username: str
    ) -> dict[str, Any]:
        """在系统文件管理器里打开工作区目录。

        未绑定本地文件夹时打开服务端分配的目录 —— 那里面确实躺着这个工作区的
        临时文件与产物，用户想看也是合理的。
        """
        workspace = self._require_workspace(workspace_id)
        self._assert_owner(workspace, username, action="打开")
        from app.workflow.local_fs import open_in_file_manager

        target = workspace.get("local_dir") or repo.workspace_dir(workspace_id)
        if not workspace.get("local_dir"):
            # 服务端目录可能是懒创建的，打开前先建出来，否则会报「目录不存在」
            Path(target).mkdir(parents=True, exist_ok=True)
        opened = open_in_file_manager(target)
        return {"path": opened, "opened": True}

    def browse_fs(self, *, path: str = "") -> dict[str, Any]:
        """列出目录内容（供文件夹选择器）。`path` 为空时返回起始位置。"""
        if not self.config.fs_browse:
            raise ValidationError("当前部署已禁用本地文件夹访问")
        from app.workflow.local_fs import list_dir, list_roots

        roots = list_roots()
        if not (path or "").strip():
            # 没有指定路径时给一个合理的起点，省掉用户第一跳
            start = roots[0]["path"] if roots else os.path.expanduser("~")
            data = list_dir(start)
        else:
            data = list_dir(path)
        data["roots"] = roots
        return data

    def _resolve_bound_file(
        self, run: dict[str, Any], source: str
    ) -> dict[str, Any]:
        """校验「这个路径属于本任务工作区的绑定目录」并读出内容。

        ⚠️ 没绑定文件夹就直接拒绝。
        曾经这里是「绑定了才校验包含性」—— 于是未绑定的工作区反倒成了后门：
        任何路径都能读。**没有绑定就没有可引用的来源**，拒绝是唯一正确的语义。
        """
        workspace = self._require_workspace(run["workspace_id"])
        bound = workspace.get("local_dir") or ""
        target = str(source or "").strip()

        if not bound:
            raise ValidationError(
                "该工作区还没有绑定本地文件夹，无法引用本地文件；"
                "请先在输入区为工作区选择文件夹"
            )

        # 只允许从**绑定目录内**取文件。不加这条限制，这个端点就等于
        # 「任意路径读取服务器文件」—— 而它本来是给工作区文件夹用的。
        try:
            inside = os.path.commonpath(
                [os.path.realpath(target), os.path.realpath(bound)]
            ) == os.path.realpath(bound)
        except ValueError:
            inside = False  # 不同盘符时 commonpath 会抛错
        if not inside:
            raise ValidationError(f"只能引用工作区文件夹（{bound}）内的文件")

        from app.workflow.local_fs import import_local_file as read_local

        return read_local(target, Path(repo.workspace_dir(run["workspace_id"])))

    def import_local_file(
        self, *, run_id: str, username: str, path: str
    ) -> dict[str, Any]:
        """把工作区绑定目录里的一个文件引用到任务里（复制一份）。

        复用 `upload_files` 的全部逻辑：同样的体积限制、类型白名单、
        解析与 TTL。区别只是内容来自本地文件而不是浏览器上传。
        """
        if not self.config.fs_browse:
            raise ValidationError("当前部署已禁用本地文件夹访问")

        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "添加文件")

        payload = self._resolve_bound_file(run, path)
        return self.upload_files(
            run_id=run_id,
            username=username,
            files=[(payload["filename"], payload["data"])],
        )

    def import_local_files(
        self, *, run_id: str, username: str, paths: list[str]
    ) -> dict[str, Any]:
        """一次引用工作区文件夹里的**多个**文件。

        为什么要有批量入口：用户绑好文件夹后的自然诉求是「把这些文件都加进来」，
        逐个点 6 次既慢又容易漏。而「全部加入」在界面上必须**如实说明代价** ——
        上下文预算只有 8,000 字符，文件越多每个分到的越少（见 `file_context.py`）。

        逐个文件单独校验，**部分失败不影响其余**：一个不支持的类型不该让
        另外 5 个文件也加不进来。失败项连同原因一起回报，界面才能说清是哪几个没进来。
        """
        if not self.config.fs_browse:
            raise ValidationError("当前部署已禁用本地文件夹访问")

        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "添加文件")
        if not paths:
            raise ValidationError("没有选择任何文件")

        cfg = self.config
        max_bytes = cfg.max_file_mb * 1024 * 1024
        allowed = self.allowed_extensions

        payloads: list[tuple[str, bytes]] = []
        failed: list[dict[str, str]] = []
        for raw in paths:
            name = os.path.basename(str(raw or "").strip()) or str(raw)
            try:
                payload = self._resolve_bound_file(run, raw)
            except Exception as exc:  # noqa: BLE001 - 单个文件的问题不该拖垮整批
                failed.append({"path": str(raw), "name": name, "reason": str(exc)})
                continue
            ext = os.path.splitext(payload["filename"])[1].lower()
            if ext not in allowed:
                # 界面上这些项本已置灰，这里兜底再说清楚一次
                failed.append(
                    {
                        "path": str(raw),
                        "name": payload["filename"],
                        "reason": f"不支持的文件类型 {ext or '（无扩展名）'}",
                    }
                )
                continue
            if len(payload["data"]) > max_bytes:
                failed.append(
                    {
                        "path": str(raw),
                        "name": payload["filename"],
                        "reason": f"超过 {cfg.max_file_mb}MB 限制",
                    }
                )
                continue
            payloads.append((payload["filename"], payload["data"]))

        if not payloads:
            detail = "；".join(f"{f['name']}：{f['reason']}" for f in failed[:3])
            raise ValidationError(f"没有可引用的文件（{detail}）" if failed else "没有可引用的文件")

        result = self.upload_files(run_id=run_id, username=username, files=payloads)
        result["failed"] = failed
        return result

    def _require_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = repo.get_workspace(workspace_id)
        if not workspace:
            raise NotFoundError("工作区不存在")
        return workspace

    def rename_workspace(
        self, *, workspace_id: str, name: str, username: str
    ) -> dict[str, Any]:
        workspace = self._require_workspace(workspace_id)
        self._assert_owner(workspace, username, action="重命名")
        clean = (name or "").strip()
        if not clean:
            raise ValidationError("工作区名称不能为空")
        updated = repo.rename_workspace(workspace_id, clean)
        if not updated:
            raise NotFoundError("工作区不存在")
        return updated

    def delete_workspace(
        self, *, workspace_id: str, username: str
    ) -> dict[str, Any]:
        workspace = self._require_workspace(workspace_id)
        self._assert_owner(workspace, username, action="删除")

        run_count = int(workspace.get("run_count") or 0)
        file_count = int(workspace.get("file_count") or 0)
        paths = repo.delete_workspace(workspace_id)

        # 数据库已提交后才动磁盘：反过来会出现「文件没了、记录还在」的中间态
        for path in paths:
            _safe_unlink(path)
        directory = repo.workspace_dir(workspace_id)
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)

        logger.info(
            f"工作区已删除: {workspace_id} runs={run_count} files={file_count}"
        )
        return {
            "success": True,
            "deleted_runs": run_count,
            "deleted_files": file_count,
        }

    @staticmethod
    def _assert_owner(
        workspace: dict[str, Any], username: str, *, action: str
    ) -> None:
        """读是全局的，改/删只归创建者。

        当前系统只有 `ADMIN_USERNAME` 单账号（无注册端点），这条校验实际不生效 ——
        但字段与判定先落，避免以后加多用户时改表改逻辑。
        """
        owner = workspace.get("owner") or ""
        if owner and username and owner != username:
            raise PermissionDeniedError(f"只有创建者可以{action}该工作区")

    # ------------------------------------------------------------------
    #  运行记录
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        workspace_id: str,
        raw_input: str,
        username: str,
        mode: Optional[str] = None,
        model: Optional[str] = None,
        force_search: bool = False,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        self._require_workspace(workspace_id)

        text = (raw_input or "").strip()
        if not text:
            raise ValidationError("请输入任务描述")

        cfg = self.config
        resolved_mode = mode or cfg.default_mode
        run = repo.create_run(
            workspace_id=workspace_id,
            username=username,
            raw_input=text,
            mode=resolved_mode,
            model=model or "",
            force_search=force_search,
            status="draft",
        )
        return self._with_files(run)

    def list_runs(
        self,
        *,
        workspace_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        runs = repo.list_runs(
            workspace_id=workspace_id, status=status, limit=limit, offset=offset
        )
        return {"runs": runs, "total": len(runs)}

    def get_run(self, *, run_id: str) -> dict[str, Any]:
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        return self._with_files(run)

    def update_run(
        self,
        *,
        run_id: str,
        username: str,
        title: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> dict[str, Any]:
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        if run.get("username") and username and run["username"] != username:
            raise PermissionDeniedError("只有创建者可以修改该任务")

        patch: dict[str, Any] = {}
        if title is not None:
            patch["title"] = title.strip()
        if mode is not None:
            # 执行中允许切换：立即对尚未开始的步骤生效（设计 §7.3）
            patch["mode"] = mode
        updated = repo.update_run(run_id, **patch) if patch else run
        if not updated:
            raise NotFoundError("任务不存在")
        return self._with_files(updated)

    def delete_run(self, *, run_id: str, username: str) -> dict[str, Any]:
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        if run.get("username") and username and run["username"] != username:
            raise PermissionDeniedError("只有创建者可以删除该任务")

        paths = repo.delete_run(run_id)
        for path in paths:
            _safe_unlink(path)
        logger.info(f"任务已删除: {run_id} files={len(paths)}")
        return {"success": True, "deleted_files": len(paths)}

    # ------------------------------------------------------------------
    #  临时文件引用
    # ------------------------------------------------------------------

    def upload_files(
        self,
        *,
        run_id: str,
        username: str,
        files: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        if run.get("username") and username and run["username"] != username:
            raise PermissionDeniedError("只有创建者可以为该任务添加文件")

        cfg = self.config
        max_bytes = cfg.max_file_mb * 1024 * 1024
        target_dir = Path(repo.workspace_dir(run["workspace_id"])) / "files"
        target_dir.mkdir(parents=True, exist_ok=True)

        saved: list[dict[str, Any]] = []
        warnings: list[str] = []
        ttl = time.time() + cfg.file_ttl_days * 86400

        for raw_name, contents in files:
            safe_name = sanitize_filename(raw_name)
            validate_extension(safe_name, self.allowed_extensions)
            if len(contents) > max_bytes:
                raise FileTooLargeError(
                    f"文件 {safe_name} 超过 {cfg.max_file_mb}MB 限制"
                )

            digest = hashlib.sha256(contents).hexdigest()
            parsed_chars, warning = self._parse_for_context(
                safe_name, contents, target_dir
            )
            if warning:
                warnings.append(warning)

            # ID 先定，再落盘：文件名与库记录天然对齐，
            # 不需要「写完临时名再改名」那一步
            file_id = repo.new_id("rf")
            stored_path = target_dir / f"{file_id}_{safe_name}"
            # 先落盘再记库：反过来会出现「库里有记录、文件不存在」的幻觉条目
            stored_path.write_bytes(contents)

            record = repo.add_run_file(
                run_id=run_id,
                file_id=file_id,
                filename=safe_name,
                stored_path=str(stored_path),
                size_bytes=len(contents),
                sha256=digest,
                kind="input",
                parsed_chars=parsed_chars,
                ttl_expires_at=ttl,
            )
            saved.append(record)

        logger.info(f"任务 {run_id} 已添加 {len(saved)} 个引用文件")
        return {"success": True, "files": saved, "warnings": warnings}

    def _parse_for_context(
        self, filename: str, contents: bytes, directory: Path
    ) -> tuple[Optional[int], Optional[str]]:
        """试解析以统计字符数；失败不阻断上传，只回报原因。

        返回值 (parsed_chars, warning)。parsed_chars 为 None 表示未解析成功。
        """
        tmp = directory / f".probe_{int(time.time() * 1000)}_{filename}"
        try:
            tmp.write_bytes(contents)
            from app.document import load_any

            docs = load_any(str(tmp))
            text = "\n".join(d.page_content for d in docs)
            chars = len(text.strip())
            if chars == 0:
                return None, f"{filename}：未能解析出文本，该文件不会参与上下文"
            return chars, None
        except AppError as exc:
            # OCR 不可用 / 类型不支持等 —— 是环境或格式问题，不是上传失败
            return None, f"{filename}：{exc.message}"
        except Exception as exc:  # noqa: BLE001 - 解析是尽力而为
            logger.warning(f"解析探测失败 {filename}: {exc}")
            return None, f"{filename}：解析失败（{exc}）"
        finally:
            _safe_unlink(str(tmp))

    def list_files(self, *, run_id: str) -> dict[str, Any]:
        if not repo.get_run(run_id, with_steps=False):
            raise NotFoundError("任务不存在")
        return {"files": repo.list_run_files(run_id)}

    def delete_file(self, *, run_id: str, file_id: str, username: str) -> dict[str, Any]:
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        if run.get("username") and username and run["username"] != username:
            raise PermissionDeniedError("只有创建者可以移除该文件")

        record = repo.get_run_file(file_id)
        if not record or record["run_id"] != run_id:
            raise NotFoundError("文件不存在")
        path = repo.delete_run_file(file_id)
        if path:
            _safe_unlink(path)
        return {"success": True}

    def resolve_file_path(self, *, file_id: str) -> tuple[str, str]:
        """返回 (落盘路径, 原始文件名) 供下载端点使用。"""
        record = repo.get_run_file(file_id)
        if not record:
            raise NotFoundError("文件不存在")
        if record["ttl_expires_at"] and record["ttl_expires_at"] < time.time():
            raise NotFoundError("文件已过期")
        path = record["stored_path"]
        if not os.path.isfile(path):
            raise NotFoundError("文件已不存在")
        return path, record["filename"]

    def _with_files(self, run: dict[str, Any]) -> dict[str, Any]:
        """补全衍生字段：引用文件、每个步骤是否需要人工确认、下一个可执行步骤。

        这些都由**服务端**算好下发（`app/workflow/executor.py`），前端只展示。
        策略只实现一次 —— 否则前端会「以为可以自动跑」而连续撞 409，
        或者自己实现一套顺序判断而与服务端不一致。
        """
        run["files"] = repo.list_run_files(run["id"])
        forced = self.config.approval_tool_list
        mode = run.get("mode", "manual")
        steps = run.get("steps") or []
        for step in steps:
            step["needs_gate"] = needs_gate(step=step, mode=mode, forced_tools=forced)

        nxt = runnable_next_step(steps, mode=mode, forced_tools=forced)
        run["next_runnable_idx"] = nxt["idx"] if nxt else None
        run["file_context"] = self._file_context_preview(run)
        run["folder"] = self._folder_summary(run)
        run["usage"] = self._usage_summary(run)
        return run

    #: token 用量的字段名在不同上游叫法不一（OpenAI 风格 vs langchain 归一化）
    _USAGE_KEYS = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }

    @classmethod
    def _normalize_usage(cls, usage: dict[str, Any]) -> dict[str, int]:
        """把用量归一成 input / output / total 三个数。"""
        out: dict[str, int] = {}
        for target, candidates in cls._USAGE_KEYS.items():
            for key in candidates:
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[target] = int(value)
                    break
        if "total_tokens" not in out and ("input_tokens" in out or "output_tokens" in out):
            out["total_tokens"] = out.get("input_tokens", 0) + out.get("output_tokens", 0)
        return out

    def _usage_summary(self, run: dict[str, Any]) -> Optional[dict[str, Any]]:
        """本任务累计 token 用量：**澄清 + 规划 + 各步骤**。

        为什么三部分都要算：只统计步骤会系统性偏小 —— 澄清可能多轮、规划可能
        因驳回重来，每次都是真实计费。**偏小的数字比没有数字更容易误导决策。**

        完全拿不到用量时返回 None（前端不显示），而不是显示 0 ——
        0 会被读成"不花钱"。
        """
        totals: dict[str, int] = {}
        calls = 0

        def accumulate(bucket: dict[str, int]) -> None:
            nonlocal calls
            if not bucket:
                return
            calls += 1
            for key, value in bucket.items():
                totals[key] = totals.get(key, 0) + value

        phases_raw = (run.get("usage_raw") or {}).get("phases") or {}
        phase_totals = {name: self._normalize_usage(u or {}) for name, u in phases_raw.items()}
        for name, raw in phases_raw.items():
            bucket = phase_totals.get(name) or {}
            if not bucket:
                continue
            # 同一相位可能被调用多次（澄清多轮 / 规划驳回重来）。bucket 里已经是
            # 各次的 token 之和，所以 token 只加一次，但**次数要按实际调用次数加** ——
            # 否则「调了 3 次」和「调了 1 次」在汇总里长得一模一样。
            calls += int((raw or {}).get("_calls") or 0) or 1
            for key, value in bucket.items():
                totals[key] = totals.get(key, 0) + value

        steps: list[dict[str, Any]] = []
        for step in run.get("steps") or []:
            bucket = self._normalize_usage((step.get("output") or {}).get("usage") or {})
            if not bucket:
                continue
            accumulate(bucket)
            steps.append(
                {
                    "idx": step.get("idx"),
                    "title": step.get("title") or "",
                    "kind": step.get("kind") or "",
                    **bucket,
                }
            )

        if not calls:
            return None
        return {
            "calls": calls,
            "input_tokens": totals.get("input_tokens", 0),
            "output_tokens": totals.get("output_tokens", 0),
            "total_tokens": totals.get("total_tokens", 0),
            "phases": {k: v for k, v in phase_totals.items() if v},
            "steps": steps,
        }

    def _write_context(self, run: dict[str, Any], cfg: Any) -> dict[str, Any]:
        """`file_write` 的写入上下文：写到哪、能写什么、能写多大。

        为什么由服务层算：工具层拿不到数据库（分层约束），
        也不该自己猜"该写到哪"。让它自己找一个默认路径，
        就等于把"能往哪写"交给了一个无状态的工具。
        """
        ctx: dict[str, Any] = {
            "artifact_dir": str(
                Path(repo.workspace_dir(run["workspace_id"])) / "artifacts"
            ),
            "allowed_extensions": cfg.write_extension_list,
            "max_bytes": int(cfg.write_max_kb) * 1024,
            "bound_allowed": bool(cfg.write_to_bound_folder and cfg.fs_browse),
        }
        if ctx["bound_allowed"]:
            workspace = repo.get_workspace(run.get("workspace_id") or "")
            bound = (workspace or {}).get("local_dir") or ""
            if bound:
                ctx["bound_dir"] = bound
        return ctx

    def _register_written_file(
        self, run_id: str, output: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """把 `file_write` 落盘的文件登记成产物，让它出现在产物列表里。

        登记失败**不影响本步成功** —— 文件已经在磁盘上了，
        为了记账失败而把整个步骤判失败是本末倒置。所以只记 warning。
        """
        written = output.get("written")
        if not isinstance(written, dict) or not written.get("stored_path"):
            return None
        try:
            record = repo.add_run_file(
                run_id=run_id,
                filename=str(written.get("filename") or "artifact"),
                stored_path=str(written["stored_path"]),
                size_bytes=int(written.get("size_bytes") or 0),
                kind="artifact",
                ttl_expires_at=time.time() + self.config.file_ttl_days * 86400,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"产出文件登记失败（文件已落盘，不影响本步）: {exc}")
            return None
        if not record:
            return None
        logger.info(
            f"[产出文件] {record.get('filename')} ({record.get('size_bytes')} 字节)"
            f" run={run_id}"
        )
        return {
            "id": record.get("id"),
            "filename": record.get("filename"),
            "size_bytes": record.get("size_bytes"),
            "target": written.get("target"),
            "renamed": bool(written.get("renamed")),
        }

    def _write_status_monotonic(
        self,
        run_id: str,
        *,
        status: str,
        writable: frozenset[str],
        **fields: Any,
    ) -> dict[str, Any]:
        """写 run 状态，但**绝不把已经推进过的状态拉回来**。

        ★ 与 `_write_clarify_status` 同一个根因，只是入口不同：

        澄清 / 规划 / 生成任务书是**三条独立的 SSE 流**。用户在一条还没跑完时
        点了另一条，两条流同时写 run 状态，后写的赢。若后写的那条正好带着
        错误回来，就会把已经推进到「待批准」的任务**回退成 failed** ——
        批准按钮直接消失，界面上没有任何错误，用户看到的是"卡住了"。

        实测复现（修复前）：

            任务书已就绪  status → awaiting_approval
            澄清流报错    status → failed            ← 任务书被静默降级

        所以状态推进必须是**单调**的：只有当前状态仍在 `writable` 内（也就是
        "还没推进到这一步之后"）才允许改状态。错误信息本身照常落库 ——
        丢错误比晚一点展示更糟，但"晚到的错误"不该推翻"已经就绪的结果"。
        """
        current = repo.get_run(run_id, with_steps=False)
        current_status = (current or {}).get("status")
        if current_status in writable:
            return repo.update_run(run_id, status=status, **fields)
        logger.warning(
            f"状态回写晚于后续阶段（当前 {current_status}，拟写 {status}），"
            f"只落数据、不回退状态 run={run_id}"
        )
        return repo.update_run(run_id, **fields)

    def _write_clarify_status(self, run_id: str, **fields: Any) -> dict[str, Any]:
        """写澄清结果，但**绝不把已经推进的状态拉回去**。

        ★ 这里修的是一个真实的竞态（实测复现）：

            09:58:14  任务书已生成 run=... （status → awaiting_approval）
            09:58:25  澄清第 1 轮（status → clarifying）   ← 晚了 11 秒

        澄清与生成任务书是**两条独立的 SSE 流**。用户在澄清还没回来时就点了
        「生成任务书」，于是两条流同时在跑；后写的一方获胜。
        澄清那条流把已进入「待批准」的任务改回了「澄清中」，
        结果是：**批准按钮直接消失，而界面上没有任何错误**。
        用户看到的就是"卡住了、不动了"。

        状态推进必须是**单调**的：澄清只允许在还没进入规划之后的阶段时写状态。
        `clarify` 数据本身照常落库 —— 丢数据比晚一点展示更糟。
        """
        return self._write_status_monotonic(
            run_id,
            status="clarifying",
            writable=_CLARIFY_WRITABLE_STATUSES,
            **fields,
        )

    def _folder_summary(self, run: dict[str, Any]) -> Optional[dict[str, Any]]:
        """工作区绑定文件夹的清单（未绑定/不可读时 None）。

        给界面用：用户绑了文件夹却看不到 AI 读它，界面上必须能回答
        「文件夹里有什么、有几个」。每次详情都算一次 —— 只是一次 scandir，
        远低于「用户以为程序坏了」的代价；不做缓存是因为文件夹随时会变。
        """
        workspace = repo.get_workspace(run.get("workspace_id") or "")
        bound = (workspace or {}).get("local_dir") or ""
        if not bound or not self.config.fs_browse:
            return None
        try:
            from app.workflow.local_fs import summarize_dir

            return summarize_dir(bound)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"工作区文件夹摘要失败，按未绑定处理: {exc}")
            return None

    def _file_context_preview(self, run: dict[str, Any]) -> Optional[dict[str, Any]]:
        """「这些引用文件实际会进去多少字」的预览。

        用**执行时同一个** `build_file_context` 来计算，因此不会出现
        「界面说全文进去了、实际只进了 12%」这种最伤信任的偏差。

        ⚠️ 为什么必须让用户看到这个数：上下文总预算只有 8,000 字符，
        文件一多就按等额份额分摊 —— 6 个文件时每个只剩约 1,300 字符。
        「全部加入」不等于「全部内容进上下文」，这个量级必须摆在明面上。

        没有引用文件时返回 None（前端据此不做展示）。
        """
        files = [f for f in run.get("files") or [] if f.get("kind") != "artifact"]
        if not files:
            return None
        inline_max = self.config.file_inline_max_chars
        try:
            from app.workflow.file_context import build_file_context

            context, notes = build_file_context(
                files, goal=run.get("raw_input", ""), inline_max_chars=inline_max
            )
        except Exception as exc:  # noqa: BLE001 - 预览失败不该让任务详情打不开
            logger.warning(f"引用文件上下文预览失败，按不展示处理: {exc}")
            return None
        return {
            "budget_chars": inline_max,
            "total_chars": sum(int(n.get("chars") or 0) for n in notes),
            "injected_chars": sum(int(n.get("injected_chars") or 0) for n in notes),
            "notes": notes,
            "preview_chars": len(context),
        }

    # ------------------------------------------------------------------
    #  步骤执行（execute 相位）
    # ------------------------------------------------------------------

    #: 允许执行步骤的 run 状态
    _EXECUTABLE_RUN_STATUSES = ("approved", "running", "paused", "failed")
    #: 允许被执行的步骤状态
    _EXECUTABLE_STEP_STATUSES = ("pending", "approved")

    def step_needs_gate(self, run: dict[str, Any], step: dict[str, Any]) -> bool:
        return needs_gate(
            step=step,
            mode=run.get("mode", "manual"),
            forced_tools=self.config.approval_tool_list,
        )

    def _require_step(self, run_id: str, idx: int) -> dict[str, Any]:
        step = repo.get_step(run_id, idx)
        if not step:
            raise NotFoundError(f"步骤 {idx} 不存在")
        return step

    def _assert_can_execute(
        self, run: dict[str, Any], step: dict[str, Any]
    ) -> None:
        if run["status"] not in self._EXECUTABLE_RUN_STATUSES:
            raise ConflictError(
                f"当前任务状态（{run['status']}）不能执行步骤；"
                f"请先批准任务书"
            )

    def _assert_step_unblocked(self, run_id: str, idx: int) -> None:
        """禁止跳步执行。

        步骤之间有真实依赖（前序产出会拼进提示词），跳过前面直接跑后面，
        模型会在**不知道前面发生了什么**的情况下继续生成，产出的东西
        看起来像模像样但基于不存在的输入 —— 比报错更难发现。
        """
        for step in repo.list_steps(run_id):
            if step["idx"] >= idx:
                break
            if step["status"] not in ("done", "skipped"):
                raise ConflictError(
                    f"第 {step['idx']} 步（{step['status']}）尚未结束，"
                    f"不能跳到第 {idx} 步"
                )

    def _advance_run_after_step(self, run_id: str) -> tuple[str, bool]:
        """按所有步骤的终结情况推进 run 状态。返回 (状态, 是否全部终结)。"""
        steps = repo.list_steps(run_id)
        remaining = [s for s in steps if s["status"] not in ("done", "skipped")]
        if remaining:
            return "running", False

        # 全部终结 → run 完成。汇总取最后一个有产出的步骤，
        # deliver 相位（P7）会替换成更正式的产物清单。
        summary = ""
        for step in reversed(steps):
            output = step.get("output") or {}
            if step["status"] == "done" and output.get("summary"):
                summary = str(output["summary"])[:4000]
                break
        repo.update_run(run_id, status="done", summary=summary or None, error=None)
        return "done", True

    def _sync_run_after_step_change(self, run_id: str) -> str:
        """跳过 / 失败等原因改变了步骤状态后，重新对齐 run 状态。"""
        steps = repo.list_steps(run_id)
        if not steps:
            return "draft"
        remaining = [s for s in steps if s["status"] not in ("done", "skipped")]
        if not remaining:
            status, _ = self._advance_run_after_step(run_id)
            return status
        # 还有未完成的步骤：若之前是「全部完成」的假象，退回 running
        run = repo.get_run(run_id, with_steps=False) or {}
        if run.get("status") == "done":
            repo.update_run(run_id, status="running")
            return "running"
        return str(run.get("status") or "running")

    async def stream_execute_step(
        self, *, run_id: str, idx: int, username: str, trace_id: str = "-"
    ) -> AsyncIterator[str]:
        """执行单个步骤并以 SSE 帧产出。

        **一次调用只推进一步**（设计 §4.1）：并发安全靠两层保障 ——
        `run_id:step_idx` 的分布式锁（跨 worker 互斥）+
        临界区内重新读取状态做的乐观检查（防止锁 TTL 过期后的重复执行）。
        """
        from app.core.redis import LockNotAcquired, distributed_lock
        from app.graph.workflow.runner import stream_phase
        from app.schemas.qa import sse_frame
        from app.workflow.executor import search_calls_used

        cfg = self.config
        settled = False
        step_idx = int(idx)

        try:
            run = repo.get_run(run_id)
            if not run:
                raise NotFoundError("任务不存在")
            self._require_owner(run, username, "执行步骤")
            step = self._require_step(run_id, step_idx)
            self._assert_can_execute(run, step)
            self._assert_step_unblocked(run_id, step_idx)

            if step["status"] not in self._EXECUTABLE_STEP_STATUSES:
                raise ConflictError(
                    f"步骤 {step_idx} 当前状态为 {step['status']}，不能执行"
                )

            # 门禁：需要人工确认且尚未批准 → 把状态落成 awaiting_approval
            # （让「在等谁」这件事在数据里可见，而不是只活在前端内存里）
            if self.step_needs_gate(run, step) and step["status"] != "approved":
                repo.update_step(run_id, step_idx, status="awaiting_approval")
                settled = True
                yield sse_frame(
                    {
                        "type": "final",
                        "content": {
                            "ok": False,
                            "code": "approval_required",
                            "status": "awaiting_approval",
                            "error": f"步骤 {step_idx} 需要人工确认后才能执行",
                            "idx": step_idx,
                        },
                        "trace_id": trace_id,
                    }
                )
                return

            graph = self._require_graph()
            file_context, _, _ = self._assemble_context(run)

            lock_ttl = cfg.step_timeout_seconds + 120
            lock_ctx = (
                distributed_lock(
                    self.redis, f"workflow:step:{run_id}:{step_idx}", ttl=lock_ttl
                )
                if self.redis is not None
                else _no_lock()
            )

            try:
                async with lock_ctx:
                    # 乐观并发：进入临界区后重读，确认没有被别的请求推进过
                    fresh = repo.get_step(run_id, step_idx)
                    if not fresh or fresh["status"] not in self._EXECUTABLE_STEP_STATUSES:
                        raise ConflictError(f"步骤 {step_idx} 正在执行或已完成")

                    repo.update_step(
                        run_id,
                        step_idx,
                        status="running",
                        started_at=time.time(),
                        error=None,
                    )
                    if run["status"] != "running":
                        repo.update_run(run_id, status="running")

                    steps = repo.list_steps(run_id)
                    state: dict[str, Any] = {
                        "phase": "execute",
                        "run_id": run_id,
                        "workspace_id": run["workspace_id"],
                        "username": username,
                        "mode": run.get("mode", "manual"),
                        "model": run.get("model", ""),
                        "force_search": bool(run.get("force_search")),
                        "plan": run.get("plan"),
                        "step": fresh,
                        "steps": steps,
                        "search_calls_used": search_calls_used(steps),
                        "search_limit": cfg.search_max_calls_per_run,
                        "forced_tools": cfg.approval_tool_list,
                        "file_context": file_context,
                        # 写入上下文由服务层算好（工具层拿不到数据库，
                        # 也不该自己猜"该写到哪"）。没有它 file_write 只会报
                        # 「缺少写入上下文」—— 所以这个键必须在 state schema 里声明。
                        "write_ctx": self._write_context(run, cfg),
                    }

                    final_state: dict[str, Any] = {}
                    async for event in stream_phase(graph, node="execute", state=state):
                        if event.get("type") == "_state":
                            final_state = event.get("content") or {}
                            continue
                        yield sse_frame({**event, "trace_id": trace_id})
            except LockNotAcquired as exc:
                raise ConflictError(f"步骤 {step_idx} 正在执行中，请稍候") from exc

            error = final_state.get("error")
            output = final_state.get("step_output")

            if error or not output:
                message = error or "步骤没有产出内容"
                repo.update_step(
                    run_id,
                    step_idx,
                    status="failed",
                    error=message,
                    ended_at=time.time(),
                )
                # 失败不自动重试：既避免烧钱，也把「怎么办」交给用户
                repo.update_run(run_id, status="paused", error=message)
                settled = True
                yield sse_frame(
                    {
                        "type": "final",
                        "content": {
                            "ok": False,
                            "code": "step_failed",
                            "status": "paused",
                            "idx": step_idx,
                            "error": message,
                        },
                        "trace_id": trace_id,
                    }
                )
                return

            # `file_write` 真的落了盘 —— 把它登记成产物，用户才能在任务里
            # 看到并下载到它。只让工具写文件却不登记，用户拿到的仍然
            # 只是一个「已写入 xx 字节」的句子，等于没生成。
            # ⚠️ 必须在 update_step 之前做：output 是同一个 dict，
            #    登记结果要跟着一起落库，否则刷新后就看不到了。
            registered = self._register_written_file(run_id, output)
            if registered:
                output["artifact"] = registered

            repo.update_step(
                run_id,
                step_idx,
                status="done",
                output=output,
                ended_at=time.time(),
                error=None,
            )
            run_status, all_done = self._advance_run_after_step(run_id)

            logger.info(
                f"[{trace_id}] 步骤 {step_idx} 完成 run={run_id} kind={fresh['kind']} "
                f"run_status={run_status} all_done={all_done}"
            )
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "ok": True,
                        "status": run_status,
                        "idx": step_idx,
                        "output": output,
                        "model": final_state.get("used_model", ""),
                        "all_done": all_done,
                    },
                    "trace_id": trace_id,
                }
            )
        except asyncio.CancelledError:
            logger.info(f"[{trace_id}] 步骤 {step_idx} 执行被客户端取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 步骤 {step_idx} 执行失败: {exc}", exc_info=True)
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "ok": False,
                        "code": "error",
                        "status": "error",
                        "idx": step_idx,
                        "error": str(exc),
                    },
                    "trace_id": trace_id,
                }
            )
        finally:
            # 流没走到任何一个落定分支 → 客户端断开或生成器被关闭。
            # 把步骤与 run 从「执行中」里捞出来，否则会永远卡住。
            if not settled:
                logger.info(f"[{trace_id}] 步骤 {step_idx} 流未正常收尾，回退状态")
                try:
                    current = repo.get_step(run_id, step_idx)
                    if current and current["status"] == "running":
                        repo.update_step(
                            run_id,
                            step_idx,
                            status="failed",
                            error="执行被中断（客户端断开或服务重启）",
                            ended_at=time.time(),
                        )
                        repo.update_run(run_id, status="paused")
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(f"回退步骤状态失败: {rollback_exc}")

    # ------------------------------------------------------------------
    #  步骤级操作：批准 / 重试 / 跳过
    # ------------------------------------------------------------------

    def approve_step(self, *, run_id: str, idx: int, username: str) -> dict[str, Any]:
        """批准单个步骤（人工审查的落点）。"""
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "批准步骤")
        step = self._require_step(run_id, idx)
        if step["status"] not in ("pending", "awaiting_approval"):
            raise ConflictError(f"步骤当前状态为 {step['status']}，不能批准")

        updated = repo.update_step(
            run_id,
            idx,
            status="approved",
            approved_by=username or "",
            approved_at=time.time(),
        )
        logger.info(f"步骤 {idx} 已批准 run={run_id}")
        return {"step": updated, "run": self._with_files(repo.get_run(run_id))}

    def retry_step(self, *, run_id: str, idx: int, username: str) -> dict[str, Any]:
        """重试失败的步骤。

        需要门禁的步骤重试后回到 `pending`（要重新确认）——
        否则「重试」会变成绕过审批的后门。
        """
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "重试步骤")
        step = self._require_step(run_id, idx)
        if step["status"] != "failed":
            raise ConflictError("只有失败的步骤可以重试")

        gated = self.step_needs_gate(run, step)
        updated = repo.update_step(
            run_id,
            idx,
            status="pending" if gated else "approved",
            retry_count=int(step.get("retry_count") or 0) + 1,
            error=None,
            output=None,
            started_at=None,
            ended_at=None,
            approved_by=None,
            approved_at=None,
        )
        repo.update_run(run_id, status="running", error=None)
        logger.info(f"步骤 {idx} 已重置待重试 run={run_id} gated={gated}")
        return {"step": updated, "run": self._with_files(repo.get_run(run_id))}

    def skip_step(self, *, run_id: str, idx: int, username: str) -> dict[str, Any]:
        """跳过某一步。

        刻意**不阻断** —— 用户比我们更清楚这一步可不可以省。但跳过后
        后续步骤会被告知「这步没有产出」（见 `executor.prior_context`），
        而不是让模型以为前面成功过。
        """
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "跳过步骤")
        step = self._require_step(run_id, idx)
        if step["status"] in ("done", "skipped", "running"):
            raise ConflictError(f"步骤当前状态为 {step['status']}，不能跳过")

        updated = repo.update_step(
            run_id, idx, status="skipped", ended_at=time.time(), error=None
        )
        self._sync_run_after_step_change(run_id)
        logger.info(f"步骤 {idx} 已跳过 run={run_id}")
        return {"step": updated, "run": self._with_files(repo.get_run(run_id))}

    def reject_step(
        self, *, run_id: str, idx: int, username: str, comment: str = ""
    ) -> dict[str, Any]:
        """驳回某一步并准备从这一步起重新规划（设计 §7.4）。

        **保留该步之前已终结的步骤**，只清掉该步及其后续 —— 这是本动作与
        「整表重规划」的本质区别：用户驳回第 3 步，往往只是「后面几步不对」，
        而不是想重跑已经花过钱的前两步。

        真正的新计划由随后的 `plan/stream` 产出（它会把保留步骤告知模型）。
        """
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "驳回步骤")
        step = self._require_step(run_id, idx)
        if step["status"] in ("done", "skipped", "running"):
            raise ConflictError(
                f"步骤当前状态为 {step['status']}，不能驳回"
                "（已完成或正在执行的步骤请改用「重试」或直接执行下一步）"
            )

        removed = repo.delete_steps_from(run_id, idx)
        remaining = repo.list_steps(run_id)

        # run.plan 必须与 steps 表同步，否则界面上的任务书会残留已被删掉的步骤
        plan = run.get("plan")
        if isinstance(plan, dict):
            plan = {**plan, "steps": [plan_step_shape(s) for s in remaining]}
        else:
            plan = None

        updated = repo.update_run(
            run_id,
            status="planning",
            plan=plan,
            reject_comment=(comment or "").strip() or None,
            error=None,
        )
        logger.info(
            f"步骤 {idx} 已驳回 run={run_id} 清除 {removed} 步，"
            f"保留 {len(remaining)} 步待重新规划之后"
        )
        return {
            "run": self._with_files(updated),
            "rejected_from": idx,
            "removed": removed,
            "preserved": len(remaining),
        }

    def cancel_run(
        self, *, run_id: str, username: str, reason: str = ""
    ) -> dict[str, Any]:
        """终止任务（设计 §7.4 的第四个选项）。

        已完成步骤的产出**保留**：它们已经花过钱，而且可能仍有参考价值。
        终止只表示「不再继续推进」。
        """
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "终止任务")
        if run["status"] in ("done", "cancelled"):
            raise ConflictError(f"任务已经是 {run['status']} 状态，无需终止")

        updated = repo.update_run(
            run_id,
            status="cancelled",
            error=(reason or "").strip() or None,
        )
        logger.info(f"任务已终止 run={run_id} reason={bool(reason)}")
        return {"run": self._with_files(updated), "status": "cancelled"}

    # ------------------------------------------------------------------
    #  交付（deliver 相位）
    # ------------------------------------------------------------------

    #: 允许生成交付物的 run 状态 —— 至少要有步骤被真正推进过
    _DELIVERABLE_STATUSES = ("running", "paused", "done", "cancelled", "failed")

    def deliver_run(self, *, run_id: str, username: str) -> dict[str, Any]:
        """把整条流程的产出组装成一份 Markdown 交付物并落盘。

        **不需要 LLM**：最后一步已经产出了结论，交付物的职责是整理与固化。
        确定性 = 可单测 + 不花钱 + 不会失败，而「交付物生成失败」是用户最不能接受的一类失败。
        """
        from app.workflow.delivery import build_deliverable, deliverable_filename

        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "生成交付物")
        steps = run.get("steps") or []
        if not steps:
            raise ConflictError("任务还没有步骤，无法生成交付物")
        if not any(s["status"] in ("done", "skipped") for s in steps):
            raise ConflictError(
                f"当前状态（{run['status']}）还没有任何已完成的步骤，无法生成交付物"
            )

        markdown = build_deliverable(
            run.get("plan"),
            steps,
            run_title=run.get("title") or "",
            raw_input=run.get("raw_input") or "",
        )

        directory = Path(repo.workspace_dir(run["workspace_id"])) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        file_id = repo.new_id("rf")
        filename = deliverable_filename(str((run.get("plan") or {}).get("goal") or ""))
        stored_path = directory / f"{file_id}_{filename}"
        stored_path.write_text(markdown, encoding="utf-8")

        record = repo.add_run_file(
            run_id=run_id,
            filename=filename,
            stored_path=str(stored_path),
            size_bytes=len(markdown.encode("utf-8")),
            kind="artifact",
            parsed_chars=len(markdown),
            ttl_expires_at=time.time() + self.config.file_ttl_days * 86400,
            file_id=file_id,
        )
        # 刻意**不覆盖** run.summary：它的语义是「结论」（最后一步的产出），
        # 而交付物是另一份东西。把整份文档写进 summary 会让界面上的
        # 「结论」卡变成整篇文档，与「执行明细」重复 —— 用户看到两份一样的东西。
        repo.update_run(run_id, error=None)

        logger.info(
            f"交付物已生成 run={run_id} 字符={len(markdown)} 文件={filename}"
        )
        return {
            "artifact": record,
            "run": self._with_files(repo.get_run(run_id)),
            "chars": len(markdown),
        }

    def list_artifacts(self, *, run_id: str) -> dict[str, Any]:
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        items = [f for f in repo.list_run_files(run_id) if f.get("kind") == "artifact"]
        return {"artifacts": items, "count": len(items)}

    async def share_run(
        self,
        *,
        run_id: str,
        username: str,
        ttl_hours: Optional[int] = None,
    ) -> dict[str, Any]:
        """生成只读分享链接（设计 §9.5）。

        ⚠️ **文件原始二进制不进分享页**。分享 payload 是**显式构造**的白名单字段，
        不是把 run 直接序列化过去 —— 后者会把落盘路径、上传文件的内容一并公开出去。
        这也是为什么这里不复用「导出会话消息」那条路径。

        `kind = "workflow"` 让前端分享页知道该按工作流渲染，而不是当对话渲染。
        """
        from app.services.share_service import ShareService
        from app.workflow.delivery import collect_final_sources

        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "分享任务")

        plan = run.get("plan") or {}
        steps = run.get("steps") or []
        sources = collect_final_sources(steps)
        title = str(plan.get("goal") or run.get("title") or "工作流任务").strip()

        from app.workflow.delivery import conclusion_of

        share_conclusion_idx, share_conclusion_text = conclusion_of(steps)

        payload = {
            "kind": "workflow",
            "status": run["status"],
            "goal": plan.get("goal") or run.get("raw_input") or "",
            "audience": plan.get("audience") or "",
            "constraints": plan.get("constraints") or [],
            "assumptions": plan.get("assumptions") or [],
            "raw_input": run.get("raw_input") or "",
            # 澄清补充（问题 → 用户选择），让分享页也能体现需求是怎么被收敛的
            "clarify": self._share_clarify(run),
            # 结论用**交付物同一套判定**（优先最后一个 synthesis 步）取全文。
            # 不用 runs.summary：它被截到 4000 字符，而明细里要点回「见结论」，
            # 拿一份被截断的正文当权威会把后面的内容整段丢掉。
            "summary": share_conclusion_text or run.get("summary") or "",
            #: 结论落在第几步 —— 分享页据此避免在「执行明细」里重复展示同一段正文
            "conclusion_idx": share_conclusion_idx,
            "steps": [
                {
                    "idx": step["idx"],
                    "kind": step["kind"],
                    "title": step["title"],
                    "status": step["status"],
                    "summary": str((step.get("output") or {}).get("summary") or ""),
                    # ★ 截断要下发给分享页：否则「模型没写完」的部分
                    # 在分享出去的页面上被当成完整内容读
                    "truncated": bool((step.get("output") or {}).get("truncated")),
                    "error": step.get("error"),
                }
                for step in steps
            ],
            "sources": [
                {
                    "ref": item.get("ref"),
                    "title": item.get("title"),
                    "href": item.get("href"),
                    "reachable": item.get("reachable"),
                }
                for item in sources
            ],
            # 产物只给**文件名与大小**，不给路径、不给内容
            "artifacts": [
                {"filename": f["filename"], "size_bytes": f.get("size_bytes", 0)}
                for f in repo.list_run_files(run_id)
                if f.get("kind") == "artifact"
            ],
        }

        service = ShareService(settings=self._settings, redis=self.redis)
        result = await service.create(
            conversation_id=run_id,
            username=username,
            title=title,
            payload=payload,
            messages=None,
            ttl_hours=ttl_hours,
        )
        logger.info(
            f"工作流分享链接已创建 run={run_id} token={str(result.get('token'))[:8]}..."
        )
        return {**result, "title": title}

    def _share_clarify(self, run: dict[str, Any]) -> list[dict[str, str]]:
        """把澄清轮次压成 [{'question','answer'}] 供分享页展示。"""
        out: list[dict[str, str]] = []
        for item in self._clarify_state(run).get("rounds") or []:
            answers = item.get("answers") or {}
            for question in item.get("questions") or []:
                answer = answers.get(question.get("id")) or {}
                text = str(answer.get("text") or "").strip()
                value = str(answer.get("value") or "").strip()
                label = text
                if not label and value:
                    hit = next(
                        (
                            o
                            for o in question.get("options") or []
                            if o.get("value") == value
                        ),
                        None,
                    )
                    label = str(hit.get("label")) if hit else value
                if label:
                    out.append(
                        {"question": str(question.get("question") or ""), "answer": label}
                    )
        return out

    # ------------------------------------------------------------------
    #  澄清（clarify 相位）
    # ------------------------------------------------------------------

    #: 允许发起澄清的 run 状态
    _CLARIFIABLE_STATUSES = (
        "draft",
        "clarifying",
        "planning",
        "awaiting_approval",
        "paused",
        "failed",
    )

    def _clarify_state(self, run: dict[str, Any]) -> dict[str, Any]:
        state = run.get("clarify") or {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault("rounds", [])
        state.setdefault("skipped", False)
        state.setdefault("sufficient", False)
        return state

    @_serialize_per_run("clarify")
    async def stream_clarify(
        self, *, run_id: str, username: str, trace_id: str = "-"
    ) -> AsyncIterator[str]:
        """产出一轮澄清问题（SSE）。

        轮次上限到顶时**不再追问**，直接把控制权交回前端去生成任务书 ——
        无限追问是 LLM 的典型反模式，用户会直接关掉页面。
        """
        from app.graph.workflow.runner import stream_phase
        from app.schemas.qa import sse_frame

        cfg = self.config
        settled = False
        try:
            run = repo.get_run(run_id)
            if not run:
                raise NotFoundError("任务不存在")
            self._require_owner(run, username, "发起澄清")
            if run["status"] not in self._CLARIFIABLE_STATUSES:
                raise ConflictError(
                    f"当前状态（{run['status']}）不能发起澄清；"
                    f"已批准或执行中的任务请先驳回"
                )

            state_data = self._clarify_state(run)
            round_no = int(run.get("clarify_round") or 0) + 1
            if state_data.get("skipped"):
                raise ConflictError("该任务已跳过澄清")
            if round_no > cfg.max_clarify_rounds:
                raise ConflictError(
                    f"澄清轮次已达上限（{cfg.max_clarify_rounds} 轮），请直接生成任务书"
                )

            graph = self._require_graph()
            _, _, summaries = self._assemble_context(run)

            self._write_clarify_status(run_id)

            state: dict[str, Any] = {
                "phase": "clarify",
                "run_id": run_id,
                "username": username,
                "raw_input": run.get("raw_input", ""),
                "mode": run.get("mode", "manual"),
                "model": run.get("model", ""),
                "file_summaries": summaries,
                "folder_listing": self._folder_listing(run),
                "clarify_history": state_data.get("rounds") or [],
                "round_no": round_no,
                "max_rounds": cfg.max_clarify_rounds,
            }

            final_state: dict[str, Any] = {}
            async for event in stream_phase(graph, node="clarify", state=state):
                if event.get("type") == "_state":
                    final_state = event.get("content") or {}
                    continue
                yield sse_frame({**event, "trace_id": trace_id})

            # 用量先记：即使这次调用失败也已经计费，漏记会让总数偏小
            repo.add_run_usage(run_id, "clarify", final_state.get("usage") or {})

            error = final_state.get("error")
            if error:
                # 状态单调：任务书若已就绪（awaiting_approval），澄清流的错误
                # 不能把它拉回 failed —— 否则批准按钮消失且无任何提示。
                self._write_status_monotonic(
                    run_id,
                    status="failed",
                    writable=_CLARIFY_WRITABLE_STATUSES,
                    error=error,
                )
                settled = True
                yield sse_frame(
                    {
                        "type": "final",
                        "content": {"ok": False, "status": "failed", "error": error},
                        "trace_id": trace_id,
                    }
                )
                return

            questions = final_state.get("questions") or []
            sufficient = bool(final_state.get("sufficient"))
            issues = final_state.get("issues") or []

            # 只有真的问出了问题才推进轮次 —— 否则「信息已足够」也算一轮，
            # 用户会在什么都没看到的情况下被扣掉一次机会
            if questions:
                rounds = list(state_data.get("rounds") or [])
                rounds.append(
                    {
                        "round": round_no,
                        "questions": questions,
                        "answers": {},
                        "asked_at": time.time(),
                        "answered_at": None,
                    }
                )
                state_data["rounds"] = rounds
                state_data["sufficient"] = False
                self._write_clarify_status(
                    run_id,
                    clarify=state_data,
                    clarify_round=round_no,
                    error=None,
                )
            else:
                state_data["sufficient"] = True
                self._write_clarify_status(
                    run_id,
                    clarify=state_data,
                    error=None,
                )

            logger.info(
                f"[{trace_id}] 澄清第 {round_no} 轮: sufficient={sufficient} "
                f"questions={len(questions)}"
            )
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "ok": True,
                        "status": "clarifying",
                        "round": round_no,
                        "max_rounds": cfg.max_clarify_rounds,
                        "sufficient": sufficient,
                        "questions": questions,
                        "issues": issues,
                        "model": final_state.get("used_model", ""),
                    },
                    "trace_id": trace_id,
                }
            )
        except asyncio.CancelledError:
            logger.info(f"[{trace_id}] 澄清生成被客户端取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 澄清生成失败: {exc}", exc_info=True)
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {"ok": False, "status": "error", "error": str(exc)},
                    "trace_id": trace_id,
                }
            )
        finally:
            if not settled:
                logger.info(f"[{trace_id}] 澄清流未正常收尾")

    def answer_clarify(
        self,
        *,
        run_id: str,
        username: str,
        answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """提交本轮澄清答案。

        允许只回答部分问题 —— 用户对某题没想法是正常的，
        强行要求全填会让「跳过澄清」变成更省事的选择。
        """
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "提交澄清答案")

        state_data = self._clarify_state(run)
        rounds = list(state_data.get("rounds") or [])
        if not rounds:
            raise ConflictError("还没有待回答的澄清问题")
        if state_data.get("skipped"):
            raise ConflictError("该任务已跳过澄清")

        index = len(rounds) - 1
        current = dict(rounds[index])
        if current.get("answered_at"):
            raise ConflictError("本轮澄清已经提交过了")

        known = {q.get("id") for q in current.get("questions") or []}
        merged: dict[str, Any] = dict(current.get("answers") or {})
        unknown: list[str] = []
        for item in answers or []:
            qid = str(item.get("id") or "")
            if not qid:
                continue
            if qid not in known:
                unknown.append(qid)
                continue
            merged[qid] = {
                "value": str(item.get("value") or ""),
                "text": str(item.get("text") or ""),
            }

        if not merged:
            raise ValidationError("请至少回答一个问题（或选择跳过澄清）")

        current["answers"] = merged
        current["answered_at"] = time.time()
        rounds[index] = current
        state_data["rounds"] = rounds

        # 同一条单调性约束：回答澄清也不该把已推进的状态拉回去
        updated = self._write_clarify_status(
            run_id, clarify=state_data, error=None
        )
        logger.info(
            f"澄清第 {current.get('round')} 轮已提交 run={run_id} "
            f"answered={len(merged)}/{len(known)}"
        )
        return {
            "run": self._with_files(updated),
            "round": current.get("round"),
            "answered": len(merged),
            "unknown": unknown,
        }

    def skip_clarify(
        self, *, run_id: str, username: str, reason: str = ""
    ) -> dict[str, Any]:
        """跳过澄清，直接进入规划。

        刻意不删掉已有轮次 —— 用户可能答了一轮才觉得够了，
        那些答案仍要进任务书提示词。
        """
        run = repo.get_run(run_id)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "跳过澄清")
        if run["status"] in ("approved", "running", "done", "cancelled"):
            raise ConflictError(f"当前状态（{run['status']}）不能跳过澄清")

        state_data = self._clarify_state(run)
        state_data["skipped"] = True
        if reason.strip():
            state_data["skip_reason"] = reason.strip()

        # 把轮次推到上限：后续再调用 clarify 会被上限挡住，
        # 不必依赖前端每次都记得检查
        updated = repo.update_run(
            run_id,
            clarify=state_data,
            clarify_round=self.config.max_clarify_rounds,
            status="draft" if run["status"] == "clarifying" else run["status"],
        )
        logger.info(f"澄清已跳过 run={run_id} reason={bool(reason)}")
        return {"run": self._with_files(updated)}

    # ------------------------------------------------------------------
    #  任务书（plan 相位）
    # ------------------------------------------------------------------

    #: 允许重新生成任务书的状态。`approved` / `running` 不在其中 ——
    #: 想改已批准的计划应先驳回，这样状态机的迁移路径保持单向、可追溯。
    #: `clarifying` 在内：用户随时可以从澄清阶段直接跳到生成任务书。
    _PLANNABLE_STATUSES = (
        "draft",
        "clarifying",
        "planning",
        "awaiting_approval",
        "paused",
        "failed",
    )

    def _require_owner(self, run: dict[str, Any], username: str, action: str) -> None:
        if run.get("username") and username and run["username"] != username:
            raise PermissionDeniedError(f"只有创建者可以{action}该任务")

    def _tool_names(self) -> list[str]:
        return tool_names()

    def _tool_required_args(self) -> dict[str, list[str]]:
        return tool_required_args()

    def _folder_listing(self, run: dict[str, Any]) -> str:
        """工作区绑定文件夹的**清单**（只有名称与大小，绝无内容）。

        为什么需要：用户绑好文件夹后说「整理**这个**文件夹里的文件」是很自然的说法，
        但绑定文件夹不等于把内容交给了模型（内容要显式引用进任务）。
        此前模型完全不知道文件夹存在，只能反问「哪个文件夹？」——
        而用户以为「程序访问不了我的本地文件」。给出清单后，澄清问题就能变准。

        ⚠️ 只给清单不给内容：目录内容是无界的（真实案例 300 KB）。
        渲染时还带一句明确声明，防止模型以为已经读过而编造内容。

        目录不可读时返回空串 —— 一个坏掉的目录不该让澄清/规划整体失败。
        """
        workspace = repo.get_workspace(run.get("workspace_id") or "")
        bound = (workspace or {}).get("local_dir") or ""
        if not bound or not self.config.fs_browse:
            return ""
        try:
            from app.workflow.file_context import render_folder_listing
            from app.workflow.local_fs import summarize_dir

            # 只列**可引用**的文件：清单是给模型用来问用户要文件的，
            # 列出加不进来的类型等于把「点了才发现不支持」搬进提示词
            return render_folder_listing(
                summarize_dir(bound), allowed=self.allowed_extensions
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"工作区文件夹清单生成失败，按无文件夹处理: {exc}")
            return ""

    def _assemble_context(
        self, run: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """准备参考文件上下文。返回 (context, notes, summaries)。"""
        from app.workflow.file_context import build_file_context

        files = repo.list_run_files(run["id"])
        inputs = [f for f in files if f.get("kind") != "artifact"]
        summaries = [
            {
                "id": f["id"],
                "filename": f["filename"],
                "parsed_chars": f.get("parsed_chars"),
            }
            for f in inputs
        ]
        try:
            context, notes = build_file_context(
                inputs,
                goal=run.get("raw_input", ""),
                inline_max_chars=self.config.file_inline_max_chars,
            )
        except Exception as exc:  # noqa: BLE001 - 上下文组装失败降级为无文件
            logger.warning(f"参考文件上下文组装失败，按无文件处理: {exc}")
            return "", [], summaries
        return context, notes, summaries

    @_serialize_per_run("plan")
    async def stream_plan(
        self, *, run_id: str, username: str, trace_id: str = "-"
    ) -> AsyncIterator[str]:
        """生成任务书并以 SSE 帧产出。

        ⚠️ 响应头一旦发出就无法改状态码，因此这里**吞掉所有异常**并转成
        `final` 事件 —— 与 `qa_service.stream_ask` 的既有约定一致。
        """
        from app.graph.workflow.runner import stream_phase
        from app.schemas.qa import sse_frame

        # 进入 planning 前先记下原状态：客户端中途断开时要把状态**退回**，
        # 否则 run 会永远停在「规划中」，看起来像有活没干完。
        #
        # ⚠️ 客户端断开有两种表现：`asyncio.CancelledError`（任务被取消）与
        # `GeneratorExit`（生成器被 aclose / GC）。只处理前者会漏掉后者，
        # 因此这里用 `settled` 标记 + `finally` 统一兜住 —— 只要没走到
        # 任何一个「已落定」的分支，就回退状态。
        previous_status = "draft"
        settled = False
        try:
            run = repo.get_run(run_id, with_steps=False)
            if not run:
                raise NotFoundError("任务不存在")
            self._require_owner(run, username, "生成任务书")
            if run["status"] not in self._PLANNABLE_STATUSES:
                raise ConflictError(
                    f"当前状态（{run['status']}）不能生成任务书；"
                    f"已批准或执行中的任务请先驳回"
                )

            previous_status = run["status"]

            graph = self._require_graph()
            file_context, notes, summaries = self._assemble_context(run)

            repo.update_run(run_id, status="planning", error=None)

            state: dict[str, Any] = {
                "run_id": run_id,
                "workspace_id": run["workspace_id"],
                "username": username,
                "raw_input": run.get("raw_input", ""),
                "mode": run.get("mode", "manual"),
                "model": run.get("model", ""),
                "force_search": bool(run.get("force_search")),
                "file_refs": [item["id"] for item in summaries],
                "file_summaries": summaries,
                "folder_listing": self._folder_listing(run),
                "file_context": file_context,
                "tools": self._tool_names(),
                # 澄清结论必须进计划提示词，否则用户前面做的选择会被丢掉
                "clarify_history": self._clarify_state(run).get("rounds") or [],
                # 重新规划：已终结的前序步骤要保留并告知模型
                "steps": repo.list_steps(run_id),
                "reject_comment": run.get("reject_comment") or "",
            }

            final_state: dict[str, Any] = {}
            async for event in stream_phase(graph, node="plan", state=state):
                if event.get("type") == "_state":
                    final_state = event.get("content") or {}
                    continue
                yield sse_frame({**event, "trace_id": trace_id})

            # 同澄清：先记录用量（重新规划会再次调用，累加才不丢）
            repo.add_run_usage(run_id, "plan", final_state.get("usage") or {})

            error = final_state.get("error")
            if error or not final_state.get("plan"):
                message = error or "模型没有产出有效的任务书"
                # 同上：规划流的错误不能推翻已经就绪的状态。
                self._write_status_monotonic(
                    run_id,
                    status="failed",
                    writable=_PLAN_WRITABLE_STATUSES,
                    error=message,
                )
                settled = True
                yield sse_frame(
                    {
                        "type": "final",
                        "content": {
                            "ok": False,
                            "status": "failed",
                            "error": message,
                            "reasoning": final_state.get("reasoning", ""),
                        },
                        "trace_id": trace_id,
                    }
                )
                return

            plan = final_state["plan"]
            issues = final_state.get("issues") or []

            # 重新规划：**保留已终结的前序步骤**，新步骤按偏移追加。
            # 否则「驳回第 N 步重规划」会把前几步已经跑出来的产出一起丢掉，
            # 用户得从头再等一遍 —— 而驳回的本意只是「后面这几步不对」。
            preserved = settled_steps(repo.list_steps(run_id))
            offset = len(preserved)
            new_steps = plan["steps"]
            for position, step in enumerate(new_steps, start=1):
                step["idx"] = offset + position

            if offset:
                # 幂等兜底：清掉未终结的残留（reject_step 通常已经清过）
                repo.delete_steps_from(run_id, offset + 1)
                stored = repo.append_steps(run_id, new_steps)
            else:
                stored = repo.replace_steps(run_id, new_steps)

            # run.plan.steps 必须与 steps 表逐字一致 ——
            # 两者不一致时，「界面上的任务书」与「会被执行的步骤」就分叉了
            plan["steps"] = [plan_step_shape(step) for step in stored]

            repo.update_run(
                run_id,
                status="awaiting_approval",
                plan=plan,
                error=None,
                # 驳回意见已被这次重新规划消费掉，清空避免一直挂在界面上误导用户
                reject_comment=None,
            )

            logger.info(
                f"[{trace_id}] 任务书已生成 run={run_id} steps={len(plan['steps'])} "
                f"issues={len(issues)}"
            )
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "ok": True,
                        "status": "awaiting_approval",
                        "plan": plan,
                        "issues": issues,
                        "files": notes,
                        "model": final_state.get("used_model", ""),
                        "goal": plan.get("goal", ""),
                    },
                    "trace_id": trace_id,
                }
            )
        except asyncio.CancelledError:
            logger.info(f"[{trace_id}] 任务书生成被客户端取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 任务书生成失败: {exc}", exc_info=True)
            settled = True
            yield sse_frame(
                {
                    "type": "final",
                    "content": {"ok": False, "error": str(exc), "status": "error"},
                    "trace_id": trace_id,
                }
            )
        finally:
            # 走到这里说明流不是正常收尾（客户端断开 / 生成器被关闭）；
            # 把状态退回，避免任务永远卡在「规划中」。
            if not settled:
                logger.info(
                    f"[{trace_id}] 任务书流未正常收尾，状态回退为 {previous_status}"
                )
                try:
                    repo.update_run(run_id, status=previous_status)
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(f"回退 run 状态失败: {rollback_exc}")

    def save_plan(
        self, *, run_id: str, username: str, plan: dict[str, Any]
    ) -> dict[str, Any]:
        """保存用户编辑后的任务书。

        用户手改的内容**同样要过严格校验** —— 否则前端的自由编辑会变成
        绕过工具白名单的后门（例如手填 `{"tool": "rm_rf"}`）。
        """
        from app.workflow.planner import PlanParseError, normalize_plan

        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "修改任务书")
        # 各状态下给出**可操作**的拒绝理由 —— 只说「不能改」会让用户卡住。
        if run["status"] in ("running", "paused"):
            raise ConflictError(
                "执行已开始，不能整体替换任务书；"
                "请用「驳回并重新规划」修改后续步骤（前序产出会保留）"
            )
        if run["status"] == "approved":
            raise ConflictError("任务书已批准并锁定；要修改请先驳回回到待规划")
        if run["status"] in ("done", "cancelled"):
            raise ConflictError(f"任务已结束（{run['status']}），不能修改任务书")

        # 已经有跑完的步骤时不再允许整表替换 —— `replace_steps` 会把它们重置成
        # `pending`，那些已经拿到的产出一旦丢失就找不回来了。
        # 要改后续步骤请走「驳回并重新规划」（它会保留前序产出、只重规划后面）。
        if settled_steps(repo.list_steps(run_id)):
            raise ConflictError(
                "执行已开始，不能整体替换任务书；请用「驳回并重新规划」修改后续步骤"
            )

        cfg = self.config
        try:
            normalized, issues = normalize_plan(
                plan if isinstance(plan, dict) else {},
                available_tools=self._tool_names(),
                max_steps=cfg.max_steps,
                fallback_goal=run.get("raw_input", ""),
                file_refs=[f["id"] for f in repo.list_run_files(run_id)],
                forced_approval_tools=cfg.approval_tool_list,
                tool_required_args=self._tool_required_args(),
            )
        except PlanParseError as exc:
            raise ValidationError(f"任务书不合法：{exc.message}") from exc

        repo.replace_steps(run_id, normalized["steps"])
        updated = repo.update_run(
            run_id,
            plan=normalized,
            status="awaiting_approval",
            reject_comment=None,
        )
        return {"run": self._with_files(updated), "plan": normalized, "issues": issues}

    def approve_plan(self, *, run_id: str, username: str) -> dict[str, Any]:
        """批准任务书。

        ⚠️ 人工模式与自动模式**都要走这一步**（设计 §7.2）：
        任务书审批是成本与方向的双重确认点，不该因模式不同而省略。
        批准后进入 `approved`（而非 `running`）—— 执行器在 P3 接入，
        现在宣称「正在执行」是谎报。
        """
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "批准任务书")
        if not run.get("plan"):
            raise ConflictError("还没有任务书可以批准")
        if run["status"] not in ("awaiting_approval", "planning", "failed", "paused"):
            raise ConflictError(f"当前状态（{run['status']}）不能批准任务书")

        updated = repo.update_run(
            run_id, status="approved", error=None, reject_comment=None
        )
        logger.info(f"任务书已批准 run={run_id} mode={run.get('mode')}")
        return {"run": self._with_files(updated), "status": "approved"}

    def reject_plan(
        self, *, run_id: str, username: str, comment: str = ""
    ) -> dict[str, Any]:
        """驳回任务书 → 回到 `planning`，保留任务书供用户编辑后重新提交。

        刻意**不删除**任务书：驳回的常见理由只是「某一步不对」，
        清掉重建会让用户前面的编辑白费。
        """
        run = repo.get_run(run_id, with_steps=False)
        if not run:
            raise NotFoundError("任务不存在")
        self._require_owner(run, username, "驳回任务书")
        if run["status"] in ("running", "done", "cancelled"):
            raise ConflictError("执行中或已结束的任务不能驳回任务书")

        updated = repo.update_run(
            run_id,
            status="planning",
            reject_comment=(comment or "").strip() or None,
            error=None,
        )
        logger.info(f"任务书已驳回 run={run_id} comment={bool(comment)}")
        return {"run": self._with_files(updated), "status": "planning"}


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError as exc:  # noqa: BLE001 - 清理失败不应让主流程失败
        logger.warning(f"删除文件失败 {path}: {exc}")


@asynccontextmanager
async def _no_lock():
    """Redis 不可用时的占位锁（与项目其它地方一致的降级姿态）。

    降级为**单进程语义**：多 worker 下同一时刻可能有两次执行，
    靠临界区内重读状态的乐观检查兜底。
    """
    yield
