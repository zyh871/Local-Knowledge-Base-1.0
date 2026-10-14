"""AI 工作流的请求/响应模型。

与对话侧（`schemas/qa.py`）刻意分开：工作流使用独立的事件命名空间与
独立端点，`/ask`、`/conversations` 一行不改，v3 契约不受影响。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RunMode = Literal["manual", "auto"]


# ── 工作区 ──────────────────────────────────────────────────────────


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    #: 绑定的本地文件夹（绝对路径；留空 = 用服务端分配的隐藏目录）
    local_dir: str = Field("", max_length=500)


class WorkspaceRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class WorkspaceBindFolder(BaseModel):
    """绑定 / 解绑本地文件夹。传空串即解绑。"""

    local_dir: str = Field("", max_length=500)


class FsRoot(BaseModel):
    name: str
    path: str


class FsEntry(BaseModel):
    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    mtime: float = 0.0


class FsListResponse(BaseModel):
    path: str
    name: str = ""
    parent: Optional[str] = None
    entries: list[FsEntry] = Field(default_factory=list)
    #: 起始位置（盘符 / 主目录），让前端不必知道平台差异
    roots: list[FsRoot] = Field(default_factory=list)
    truncated: bool = False


class OpenFolderResponse(BaseModel):
    path: str
    opened: bool = True


class ImportLocalFileRequest(BaseModel):
    #: 要引用到任务里的本地文件绝对路径
    path: str = Field(..., max_length=500)


class ImportLocalFilesRequest(BaseModel):
    """一次引用多个本地文件（工作区文件夹批量的入口）。"""

    paths: list[str] = Field(..., min_length=1, max_length=200)


class UsageStep(BaseModel):
    """单个步骤的 token 用量。"""

    idx: int = 0
    title: str = ""
    kind: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class RunUsage(BaseModel):
    """本任务的 token 用量汇总（澄清 + 规划 + 各步骤）。

    存在的意义是**成本可见**：没有它，一次 10 次调用、9 万字符的产出
    到底花了多少完全是黑盒，也就无从判断"该优化哪一步"。

    拿不到用量时整个字段为 None，而不是给 0 —— 0 会被读成"不花钱"。
    """

    #: 有用量数据的调用次数（上游没返回 usage 的那些不计入）
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    #: 按相位（clarify / plan）
    phases: dict[str, dict[str, int]] = Field(default_factory=dict)
    steps: list[UsageStep] = Field(default_factory=list)


class FolderSummary(BaseModel):
    """工作区绑定文件夹的清单摘要（**只有名称与大小，绝无内容**）。"""

    path: str = ""
    name: str = ""
    files: list[dict[str, Any]] = Field(default_factory=list)
    dirs: list[str] = Field(default_factory=list)
    total_bytes: int = 0
    total_files: int = 0
    truncated: bool = False


class FileContextNote(BaseModel):
    """单个引用文件最终「进了多少字」。"""

    id: str = ""
    filename: str = ""
    chars: int = 0
    injected_chars: int = 0
    #: full = 全文；retrieved = 按相关性截取；empty = 没解析出文本
    mode: str = ""


class FileContextPreview(BaseModel):
    """引用文件进入模型上下文的总览（由服务端用执行时同一套逻辑算出）。"""

    budget_chars: int = 0
    total_chars: int = 0
    injected_chars: int = 0
    preview_chars: int = 0
    notes: list[FileContextNote] = Field(default_factory=list)


class WorkspaceInfo(BaseModel):
    id: str
    name: str
    owner: str = ""
    visibility: str = "shared"
    #: 绑定的本地文件夹（空 = 用服务端分配的隐藏目录）
    local_dir: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    run_count: int = 0
    file_count: int = 0


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceInfo] = Field(default_factory=list)


class WorkspaceDeleteResponse(BaseModel):
    success: bool = True
    deleted_runs: int = 0
    deleted_files: int = 0


# ── 运行记录 ────────────────────────────────────────────────────────


class RunCreate(BaseModel):
    """创建一次工作流任务。

    P1 只落库，`status` 停在 `draft`；澄清 / 计划 / 执行由 P2–P3 接管。
    """

    workspace_id: str = Field(..., min_length=1)
    raw_input: str = Field(..., min_length=1, max_length=20000)
    mode: RunMode = "manual"
    model: Optional[str] = None
    force_search: bool = False


class RunPatch(BaseModel):
    """局部更新。`mode` 允许执行中修改 —— 立即对尚未开始的步骤生效。"""

    title: Optional[str] = Field(None, min_length=1, max_length=120)
    mode: Optional[RunMode] = None


class StepInfo(BaseModel):
    id: str
    idx: int
    kind: str
    title: str = ""
    action: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    status: str = "pending"
    #: 执行前是否需要人工确认 —— 由**服务端**统一计算后下发，
    #: 前端只展示不判断（策略只能有一份实现）
    needs_gate: bool = False
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


class StepActionResponse(BaseModel):
    step: StepInfo
    run: "RunInfo"

class RunFileInfo(BaseModel):
    id: str
    filename: str
    size_bytes: int = 0
    kind: str = "input"
    parsed_chars: Optional[int] = None
    ttl_expires_at: float = 0.0
    created_at: float = 0.0


class PlanStepInfo(BaseModel):
    """任务书里的一步。

    `action` 必须是**声明式**的：计划阶段只写「将要调用什么」，
    执行阶段才真的调用（设计 §6.2）。若放自然语言描述，
    后端就无法按步骤调度工具，审批也就无处落地。
    """

    idx: int = 0
    kind: str = "llm"
    title: str = ""
    action: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    status: str = "pending"


class PlanInfo(BaseModel):
    goal: str = ""
    audience: str = ""
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    file_refs: list[str] = Field(default_factory=list)
    steps: list[PlanStepInfo] = Field(default_factory=list)


class PlanSaveRequest(BaseModel):
    """用户编辑后的任务书。

    服务端会**重新过一遍严格校验** —— 前端的自由编辑不能成为
    绕过工具白名单的后门。
    """

    goal: str = ""
    audience: str = ""
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    file_refs: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)


class PlanSaveResponse(BaseModel):
    run: "RunInfo"
    plan: PlanInfo
    # 校验期做过的修正，必须展示给用户
    issues: list[str] = Field(default_factory=list)


class RejectRequest(BaseModel):
    comment: str = Field("", max_length=2000)


# ── 澄清（clarify 相位）────────────────────────────────────────────


class ClarifyOption(BaseModel):
    label: str
    value: str


class ClarifyQuestion(BaseModel):
    id: str
    question: str
    options: list[ClarifyOption] = Field(default_factory=list)
    #: 推荐答案（某个 option 的 value）。**每题必带** ——
    #: 这是把澄清从「审讯」变成「确认」的关键，前端会默认选中它。
    recommended: str = ""
    reason: str = ""


class ClarifyRoundInfo(BaseModel):
    round: int = 1
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    #: 题目 id → {value, text}
    answers: dict[str, Any] = Field(default_factory=dict)
    asked_at: float = 0.0
    answered_at: Optional[float] = None


class ClarifyStateInfo(BaseModel):
    rounds: list[ClarifyRoundInfo] = Field(default_factory=list)
    #: 用户主动跳过澄清
    skipped: bool = False
    #: 跳过时填的原因（可选）
    skip_reason: str = ""
    #: 模型判定「信息已足够」
    sufficient: bool = False


class ClarifyAnswerItem(BaseModel):
    id: str
    value: str = Field("", max_length=200)
    text: str = Field("", max_length=2000)


class ClarifyAnswerRequest(BaseModel):
    answers: list[ClarifyAnswerItem] = Field(default_factory=list)


class ClarifySkipRequest(BaseModel):
    reason: str = Field("", max_length=500)


# ── 交付 / 分享（P7）──────────────────────────────────────────────


class ArtifactListResponse(BaseModel):
    artifacts: list[RunFileInfo] = Field(default_factory=list)
    count: int = 0


class DeliverResponse(BaseModel):
    run: "RunInfo"
    artifact: RunFileInfo
    chars: int = 0


class ShareRunRequest(BaseModel):
    ttl_hours: Optional[int] = Field(None, ge=1, le=24 * 14)


class ShareRunResponse(BaseModel):
    success: bool = True
    token: str = ""
    url_path: str = ""
    title: str = ""
    expires_at: float = 0.0
    ttl_hours: int = 0


class RunActionResponse(BaseModel):
    run: "RunInfo"
    status: str = ""


class StepRejectResponse(BaseModel):
    """驳回某一步的返回。

    `preserved` / `removed` 要回传：前端据此告诉用户
    「已保留前 N 步的产出，重新规划了后 M 步」——
    这是本动作与「整表重规划」的关键差别，不展示出来用户会以为全白跑了。
    """

    run: "RunInfo"
    rejected_from: int = 0
    removed: int = 0
    preserved: int = 0


class RunInfo(BaseModel):
    id: str
    workspace_id: str
    username: str = ""
    title: str = ""
    raw_input: str = ""
    status: str = "draft"
    mode: str = "manual"
    model: str = ""
    force_search: bool = False
    clarify_round: int = 0
    plan: Optional[dict[str, Any]] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    # 上一轮任务书被驳回时用户写的意见
    reject_comment: Optional[str] = None
    # 澄清问答（原话 → 澄清补充 → 任务书 三段对比的中间那段）
    clarify: ClarifyStateInfo = Field(default_factory=ClarifyStateInfo)
    created_at: float = 0.0
    updated_at: float = 0.0
    steps: list[StepInfo] = Field(default_factory=list)
    files: list[RunFileInfo] = Field(default_factory=list)
    #: 下一个**可以立刻执行**的步骤序号。
    #: 为 None 表示卡住了（在等审批、有失败步骤、或已全部结束）——
    #: 顺序与门禁策略都在服务端算，前端只照着走。
    next_runnable_idx: Optional[int] = None
    #: 引用文件实际会进入模型上下文多少字（服务端用执行时同一套逻辑算出）。
    #: 没有引用文件时为 None。存在的意义是让「全部加入 ≠ 全部进上下文」可见。
    file_context: Optional["FileContextPreview"] = None
    #: 工作区绑定文件夹的清单（未绑定为 None）。
    #: 界面用它回答一个用户必然会问的问题：「文件夹里有东西，为什么 AI 看不到？」
    folder: Optional["FolderSummary"] = None
    #: 本任务累计 token 用量（拿不到时为 None，而不是 0）
    usage: Optional["RunUsage"] = None


class RunListResponse(BaseModel):
    runs: list[RunInfo] = Field(default_factory=list)
    total: int = 0


class RunFileListResponse(BaseModel):
    files: list[RunFileInfo] = Field(default_factory=list)


class RunFileUploadResponse(BaseModel):
    success: bool = True
    files: list[RunFileInfo] = Field(default_factory=list)
    # 未能解析出文本的文件（例如未装 OCR 的图片）会在这里拿到原因，
    # 不静默当作「空上下文」进流程
    warnings: list[str] = Field(default_factory=list)
    #: 批量引用时**被跳过的**文件及原因（部分失败不影响其余）。
    #: 界面需要说清「是哪几个没进来、为什么」，否则用户以为全都加上了。
    failed: list[dict[str, str]] = Field(default_factory=list)


# ── 能力与配置（供前端初始化选择器）──────────────────────────────────


class WorkflowCapabilities(BaseModel):
    enabled: bool = True
    default_mode: str = "manual"
    modes: list[dict[str, str]] = Field(default_factory=list)
    max_steps: int = 12
    max_clarify_rounds: int = 3
    max_file_mb: int = 20
    file_inline_max_chars: int = 8000
    # 无论什么模式都强制单独审批的工具
    require_approval_tools: list[str] = Field(default_factory=list)
    # 当前注册的工具清单 —— 任务书编辑器用它渲染工具下拉，
    # 避免用户手打出后端不存在的工具名
    tools: list[str] = Field(default_factory=list)
    # 允许引用的文件类型（形如 `.pdf`）。前端据此限制可选范围并把
    # 不可引用的文件置灰 —— 让「能不能引用」在点击**之前**就可见
    allowed_extensions: list[str] = Field(default_factory=list)


# PlanSaveResponse / RunActionResponse 里用了前向引用 "RunInfo"，
# 而 RunInfo 定义在它们之后 —— 在这里显式重建模型，
# 否则首次序列化时才报错（pydantic v2 默认不提前解析未定义名）。
PlanSaveResponse.model_rebuild()
RunActionResponse.model_rebuild()
StepActionResponse.model_rebuild()
StepRejectResponse.model_rebuild()
DeliverResponse.model_rebuild()
RunInfo.model_rebuild()
