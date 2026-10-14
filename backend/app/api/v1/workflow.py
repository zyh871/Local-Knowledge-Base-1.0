"""AI 工作流路由（P1：工作区 / 任务 / 临时文件引用）。

路径不带 `/api/v1` 前缀 —— 与其余端点一致（`Settings.api_prefix` 为空），
工作流使用独立的 `/workflow` 命名空间，**不改动任何既有端点**，
因此 `test_sse_contract.py` 与 v3 契约不受影响。

新增端点已同步登记到 `tests/data/openapi_v3_baseline.json`（硬约束 6）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import WorkflowServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.core.logging import get_trace_id
from app.schemas.workflow import (
    ArtifactListResponse,
    ClarifyAnswerRequest,
    ClarifySkipRequest,
    DeliverResponse,
    FsListResponse,
    ImportLocalFileRequest,
    ImportLocalFilesRequest,
    OpenFolderResponse,
    PlanSaveRequest,
    PlanSaveResponse,
    RejectRequest,
    RunActionResponse,
    RunCreate,
    RunFileListResponse,
    RunFileUploadResponse,
    RunInfo,
    RunListResponse,
    RunPatch,
    ShareRunRequest,
    ShareRunResponse,
    StepActionResponse,
    StepRejectResponse,
    WorkspaceBindFolder,
    WorkspaceCreate,
    WorkspaceDeleteResponse,
    WorkspaceInfo,
    WorkspaceListResponse,
    WorkspaceRename,
)

router = APIRouter()

_SSE_HEADERS = {
    # 关掉 Nginx 等反代的缓冲，否则事件会攒到流结束才一次性到达前端
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


# ---------------------------------------------------------------------------
#  能力与配置（前端初始化选择器用）
# ---------------------------------------------------------------------------


@router.get(
    "/workflow/capabilities",
    response_model=dict,
    summary="工作流能力与配置",
)
async def workflow_capabilities(
    current_user: CurrentUser, svc: WorkflowServiceDep, _: None = Depends(rate_limit("workflow"))
) -> dict:
    return svc.capabilities()


# ---------------------------------------------------------------------------
#  工作区
# ---------------------------------------------------------------------------


@router.get(
    "/workflow/workspaces",
    response_model=WorkspaceListResponse,
    summary="工作区列表",
)
async def list_workspaces(
    current_user: CurrentUser, svc: WorkflowServiceDep, _: None = Depends(rate_limit("workflow"))
) -> dict:
    return svc.list_workspaces()


@router.post(
    "/workflow/workspaces", response_model=WorkspaceInfo, summary="创建工作区"
)
async def create_workspace(
    payload: WorkspaceCreate,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.create_workspace(
        name=payload.name,
        username=getattr(current_user, "username", "") or "",
        local_dir=payload.local_dir,
    )


@router.put(
    "/workflow/workspaces/{workspace_id}/folder",
    response_model=WorkspaceInfo,
    summary="绑定 / 解绑本地文件夹",
)
async def bind_workspace_folder(
    workspace_id: str,
    payload: WorkspaceBindFolder,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """传空字符串即解绑。绑定的目录必须真实存在。"""
    return svc.bind_workspace_folder(
        workspace_id=workspace_id,
        local_dir=payload.local_dir,
        username=getattr(current_user, "username", "") or "",
    )


@router.post(
    "/workflow/workspaces/{workspace_id}/open",
    response_model=OpenFolderResponse,
    summary="在文件管理器中打开工作区文件夹",
)
async def open_workspace_folder(
    workspace_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.open_workspace_folder(
        workspace_id=workspace_id,
        username=getattr(current_user, "username", "") or "",
    )


# ---------------------------------------------------------------------------
#  本地文件夹浏览（供文件夹选择器）
# ---------------------------------------------------------------------------
# ⚠️ 这是**有意开放**的本地文件系统读能力，前提是「本机单用户」部署。
# 只列出名称/类型/大小/时间，**绝不返回文件内容**，也没有删除/改名/写入。
# 多用户或对外暴露时必须用 WORKFLOW_FS_BROWSE=false 关掉。


@router.get("/workflow/fs/list", response_model=FsListResponse, summary="浏览本地文件夹")
async def browse_fs(
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    path: str = "",
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """`path` 留空时返回一个合理的起点（主目录）与可用起始位置列表。"""
    return svc.browse_fs(path=path)


@router.patch(
    "/workflow/workspaces/{workspace_id}",
    response_model=WorkspaceInfo,
    summary="重命名工作区",
)
async def rename_workspace(
    workspace_id: str,
    payload: WorkspaceRename,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.rename_workspace(
        workspace_id=workspace_id,
        name=payload.name,
        username=getattr(current_user, "username", "") or "",
    )


@router.delete(
    "/workflow/workspaces/{workspace_id}",
    response_model=WorkspaceDeleteResponse,
    summary="删除工作区（级联任务与文件）",
)
async def delete_workspace(
    workspace_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.delete_workspace(
        workspace_id=workspace_id,
        username=getattr(current_user, "username", "") or "",
    )


# ---------------------------------------------------------------------------
#  任务（run）
# ---------------------------------------------------------------------------


@router.get("/workflow/runs", response_model=RunListResponse, summary="任务列表")
async def list_runs(
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    workspace_id: Optional[str] = Query(None, description="按工作区过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.list_runs(
        workspace_id=workspace_id, status=status, limit=limit, offset=offset
    )


@router.post("/workflow/runs", response_model=RunInfo, summary="创建任务")
async def create_run(
    payload: RunCreate,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.create_run(
        workspace_id=payload.workspace_id,
        raw_input=payload.raw_input,
        username=getattr(current_user, "username", "") or "",
        mode=payload.mode,
        model=payload.model,
        force_search=payload.force_search,
    )


@router.get("/workflow/runs/{run_id}", response_model=RunInfo, summary="任务详情")
async def get_run(
    run_id: str, current_user: CurrentUser, svc: WorkflowServiceDep, _: None = Depends(rate_limit("workflow"))
) -> dict:
    return svc.get_run(run_id=run_id)


@router.patch(
    "/workflow/runs/{run_id}", response_model=RunInfo, summary="修改任务（标题 / 执行模式）"
)
async def update_run(
    run_id: str,
    payload: RunPatch,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.update_run(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        title=payload.title,
        mode=payload.mode,
    )


@router.delete("/workflow/runs/{run_id}", response_model=dict, summary="删除任务")
async def delete_run(
    run_id: str, current_user: CurrentUser, svc: WorkflowServiceDep, _: None = Depends(rate_limit("workflow"))
) -> dict:
    return svc.delete_run(
        run_id=run_id, username=getattr(current_user, "username", "") or ""
    )


# ---------------------------------------------------------------------------
#  临时文件引用
# ---------------------------------------------------------------------------


@router.get(
    "/workflow/runs/{run_id}/files",
    response_model=RunFileListResponse,
    summary="任务的引用文件",
)
async def list_run_files(
    run_id: str, current_user: CurrentUser, svc: WorkflowServiceDep, _: None = Depends(rate_limit("workflow"))
) -> dict:
    return svc.list_files(run_id=run_id)


@router.post(
    "/workflow/runs/{run_id}/files",
    response_model=RunFileUploadResponse,
    summary="上传引用文件（仅作当前任务上下文，不入文档管理）",
)
async def upload_run_files(
    run_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    files: list[UploadFile] = File(...),
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    payload: list[tuple[str, bytes]] = []
    for file in files:
        payload.append((file.filename or "", await file.read()))
    return svc.upload_files(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        files=payload,
    )


@router.post(
    "/workflow/runs/{run_id}/files/import",
    response_model=RunFileUploadResponse,
    summary="从工作区文件夹引用一个本地文件（复制进工作区）",
)
async def import_local_file_to_run(
    run_id: str,
    payload: ImportLocalFileRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """只能引用**工作区绑定目录内**的文件。

    不做这条限制的话，这个端点就等于「任意路径读取服务器文件」——
    而它原本只是为了让用户不必把已有文件重传一遍。
    """
    return svc.import_local_file(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        path=payload.path,
    )


@router.post(
    "/workflow/runs/{run_id}/files/import-batch",
    response_model=RunFileUploadResponse,
    summary="从工作区文件夹批量引用文件（部分失败不影响其余）",
)
async def import_local_files_to_run(
    run_id: str,
    payload: ImportLocalFilesRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """逐个文件单独校验：一个不支持的类型不该让其余文件也加不进来。

    被跳过的文件连同原因一起在 `failed` 里返回，界面才能说清是哪几个没进来。
    """
    return svc.import_local_files(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        paths=payload.paths,
    )


@router.delete(
    "/workflow/runs/{run_id}/files/{file_id}",
    response_model=dict,
    summary="移除引用文件",
)
async def delete_run_file(
    run_id: str,
    file_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.delete_file(
        run_id=run_id,
        file_id=file_id,
        username=getattr(current_user, "username", "") or "",
    )


@router.get("/workflow/files/{file_id}/download", summary="下载引用文件")
async def download_run_file(
    file_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> FileResponse:
    path, filename = svc.resolve_file_path(file_id=file_id)
    return FileResponse(
        path, filename=filename, media_type="application/octet-stream"
    )


# ---------------------------------------------------------------------------
#  澄清（clarify 相位）
# ---------------------------------------------------------------------------
# 设计上澄清插在 plan 之前，但**不强制**：用户可以随时跳过直接生成任务书，
# 也可以在答完若干轮后停下。因此它是三个独立端点，而不是 plan 内部的一步。


@router.post("/workflow/runs/{run_id}/clarify/stream", summary="产出一轮澄清问题（SSE）")
async def stream_clarify(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> StreamingResponse:
    return StreamingResponse(
        svc.stream_clarify(
            run_id=run_id,
            username=getattr(current_user, "username", "") or "",
            trace_id=get_trace_id(request),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/workflow/runs/{run_id}/clarify/answer",
    response_model=dict,
    summary="提交本轮澄清答案",
)
async def answer_clarify(
    run_id: str,
    payload: ClarifyAnswerRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.answer_clarify(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        answers=[item.model_dump() for item in payload.answers],
    )


@router.post(
    "/workflow/runs/{run_id}/clarify/skip",
    response_model=RunActionResponse,
    summary="跳过澄清，直接进入规划",
)
async def skip_clarify(
    run_id: str,
    payload: ClarifySkipRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.skip_clarify(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        reason=payload.reason,
    )


# ---------------------------------------------------------------------------
#  任务书（plan 相位）
# ---------------------------------------------------------------------------
# 独立的事件命名空间：`workflow_phase` / `thinking_delta` / `plan_ready` / `final`。
# `/ask/stream` 的 `intent → sources → (tool_calls) → chunk* → final` 一行未改，
# 因此 test_sse_contract.py 不受影响。


@router.post("/workflow/runs/{run_id}/plan/stream", summary="生成任务书（SSE）")
async def stream_plan(
    run_id: str,
    request: Request,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> StreamingResponse:
    return StreamingResponse(
        svc.stream_plan(
            run_id=run_id,
            username=getattr(current_user, "username", "") or "",
            trace_id=get_trace_id(request),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.put(
    "/workflow/runs/{run_id}/plan",
    response_model=PlanSaveResponse,
    summary="保存用户编辑后的任务书",
)
async def save_plan(
    run_id: str,
    payload: PlanSaveRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.save_plan(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        plan=payload.model_dump(),
    )


@router.post(
    "/workflow/runs/{run_id}/approve",
    response_model=RunActionResponse,
    summary="批准任务书",
)
async def approve_plan(
    run_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.approve_plan(
        run_id=run_id, username=getattr(current_user, "username", "") or ""
    )


@router.post(
    "/workflow/runs/{run_id}/reject",
    response_model=RunActionResponse,
    summary="驳回任务书（回到待规划）",
)
async def reject_plan(
    run_id: str,
    payload: RejectRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.reject_plan(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        comment=payload.comment,
    )


# ---------------------------------------------------------------------------
#  步骤执行（execute 相位）
# ---------------------------------------------------------------------------
# ★ 一次请求只推进一步。这是设计文档 §4.1 的核心取舍：不用 interrupt() 挂起
#   等审批，而是让每一步成为可独立重试、可断点续跑的请求 —— 审批等待时间
#   不可控，长连接等不起；且步骤状态本来就落在 workflow.db，不该有第二份真相。


@router.post(
    "/workflow/runs/{run_id}/steps/{step_idx}/execute",
    summary="执行单个步骤（SSE）",
)
async def execute_step(
    run_id: str,
    step_idx: int,
    request: Request,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> StreamingResponse:
    return StreamingResponse(
        svc.stream_execute_step(
            run_id=run_id,
            idx=step_idx,
            username=getattr(current_user, "username", "") or "",
            trace_id=get_trace_id(request),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/workflow/runs/{run_id}/steps/{step_idx}/approve",
    response_model=StepActionResponse,
    summary="批准单个步骤",
)
async def approve_step(
    run_id: str,
    step_idx: int,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.approve_step(
        run_id=run_id,
        idx=step_idx,
        username=getattr(current_user, "username", "") or "",
    )


@router.post(
    "/workflow/runs/{run_id}/steps/{step_idx}/retry",
    response_model=StepActionResponse,
    summary="重试失败的步骤",
)
async def retry_step(
    run_id: str,
    step_idx: int,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.retry_step(
        run_id=run_id,
        idx=step_idx,
        username=getattr(current_user, "username", "") or "",
    )


@router.post(
    "/workflow/runs/{run_id}/steps/{step_idx}/skip",
    response_model=StepActionResponse,
    summary="跳过某个步骤",
)
async def skip_step(
    run_id: str,
    step_idx: int,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.skip_step(
        run_id=run_id,
        idx=step_idx,
        username=getattr(current_user, "username", "") or "",
    )


@router.post(
    "/workflow/runs/{run_id}/steps/{step_idx}/reject",
    response_model=StepRejectResponse,
    summary="驳回某一步并从该步起重新规划",
)
async def reject_step(
    run_id: str,
    step_idx: int,
    payload: RejectRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.reject_step(
        run_id=run_id,
        idx=step_idx,
        username=getattr(current_user, "username", "") or "",
        comment=payload.comment,
    )


@router.post(
    "/workflow/runs/{run_id}/cancel",
    response_model=RunActionResponse,
    summary="终止任务",
)
async def cancel_run(
    run_id: str,
    payload: RejectRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.cancel_run(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        reason=payload.comment,
    )


# ---------------------------------------------------------------------------
#  交付 / 产物 / 分享（P7）
# ---------------------------------------------------------------------------


@router.post(
    "/workflow/runs/{run_id}/deliver",
    response_model=DeliverResponse,
    summary="生成交付物（Markdown）",
)
async def deliver_run(
    run_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """刻意**不用 SSE**：交付物是确定性拼装，毫秒级完成，
    为它架一条事件流只会增加前端复杂度而没有收益。
    """
    return svc.deliver_run(
        run_id=run_id, username=getattr(current_user, "username", "") or ""
    )


@router.get(
    "/workflow/runs/{run_id}/artifacts",
    response_model=ArtifactListResponse,
    summary="产物列表",
)
async def list_artifacts(
    run_id: str,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    return svc.list_artifacts(run_id=run_id)


@router.post(
    "/workflow/runs/{run_id}/share",
    response_model=ShareRunResponse,
    summary="生成只读分享链接",
)
async def share_run(
    run_id: str,
    payload: ShareRunRequest,
    current_user: CurrentUser,
    svc: WorkflowServiceDep,
    _: None = Depends(rate_limit("workflow")),
) -> dict:
    """分享读取复用既有公开端点 `GET /share/{token}`（无需新增公开接口）。

    payload 里带 `kind=workflow`，前端分享页据此按工作流渲染。
    """
    return await svc.share_run(
        run_id=run_id,
        username=getattr(current_user, "username", "") or "",
        ttl_hours=payload.ttl_hours,
    )
