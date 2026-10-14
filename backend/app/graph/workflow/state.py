"""工作流状态图的数据契约。

**关键约束：State 必须可 JSON 序列化**（与 `graph/state.py` 同源）。
两个危险对象在这里被刻意排除：

  * **文件正文不进 State** —— 只带 `file_summaries`（文件名 + 字数）与
    组装好的 `file_context` 字符串。原文本可能几万字，进 State 会让
    每一步的载荷都膨胀。
  * **工具返回值不进 State** —— 计划的 `action` 是声明式的（`{tool, args}`），
    真正执行留给 P3，届时返回值也必须转成 dict 才能回写。

与 `RAGState` 刻意分开：工作流是多相位 + 门禁 + 可中断可恢复的状态机，
而 `RAGState` 服务于单轮问答，且被 v3 契约共享。
"""

from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    """工作流图状态。字段按流转阶段分组。"""

    # ── 输入（图调用前由 runner 写入）────────────────────────
    # 注意：**没有 workspace_name**。工作区名是归类标签而非任务主题，
    # 在原始输入缺乏具体主题时会被模型当成主题（实测过），因此不进提示词。
    run_id: str
    workspace_id: str
    username: str
    raw_input: str
    mode: str                      # manual | auto
    model: str                     # 按请求覆盖模型，空串 = 服务端默认
    force_search: bool

    # ── 上下文装载 ──────────────────────────────────────────
    file_refs: list[str]           # 仅 file_id，正文不进来
    file_summaries: list[dict[str, Any]]  # [{id, filename, parsed_chars}]
    file_context: str              # 已按策略裁剪的参考文件内容
    #: 工作区绑定文件夹的**清单**（只有名称与大小，绝无内容）。
    #: 解决「绑定文件夹后说『整理这个文件夹』，模型却反问『哪个文件夹』」——
    #: 绑定不等于把内容交给了模型，但模型至少该知道那里有什么。
    #:
    #: ⚠️ 新增 state 字段必须**在这里声明**：LangGraph 会按本 schema 过滤，
    #: 未声明的键会被**静默丢弃** —— 表现为「参数加了但没生效」，极难排查。
    folder_listing: str
    tools: list[str]               # 可用工具白名单（供提示词约束模型）

    # ── clarify 相位（P4）──────────────────────────────────
    #: 此前的澄清轮次（含问题与用户的回答）—— 拼进提示词才能不重复提问
    clarify_history: list[dict[str, Any]]
    round_no: int
    max_rounds: int
    #: 模型判定「信息已足够」—— 据此结束澄清直接进规划
    sufficient: bool
    questions: list[dict[str, Any]]
    #: 本相位这次调用的 token 用量（尽力而为，上游没给就是空 dict）。
    #: 服务层把它累加进 runs.usage_json —— 没有这个数，
    #: 一次运行到底花了多少完全是黑盒，也就无从判断该优化哪一步。
    usage: dict[str, Any]

    # ── plan 相位产物 ──────────────────────────────────────
    plan: dict[str, Any] | None
    issues: list[str]              # 校验期做过的修正，必须展示给用户
    reasoning: str                 # 模型的规划思路（可视化用）
    raw_output: str                # 模型完整输出（排障用）
    #: 上一轮驳回时用户写的意见 —— 重新规划必须遵循它
    reject_comment: str

    # ── execute 相位（P3）──────────────────────────────────
    #: 入口选择：`plan` / `execute`。图用条件入口按它选相位。
    phase: str
    #: 本次要执行的那一步（含 idx / kind / title / action）
    step: dict[str, Any]
    #: 该 run 的全部步骤 —— 前序产出要拼进提示词，步骤之间才有真实依赖
    steps: list[dict[str, Any]]
    #: 本步产出（{summary, sources, truncated?}）
    step_output: dict[str, Any] | None
    #: 联网搜索配额：已用次数与上限
    search_calls_used: int
    search_limit: int
    #: 无论什么模式都必须单独审批的工具
    forced_tools: list[str]
    #: `file_write` 的写入上下文（产物目录 / 绑定目录 / 后缀白名单 / 大小上限）。
    #: 由服务层按 run 算好后注入 —— 工具层拿不到数据库，
    #: 也不该自己猜"该写到哪"。
    #: ⚠️ 同样必须在这里声明，否则会被 LangGraph 静默丢掉，
    #: 表现为 file_write 一直报「缺少写入上下文」。
    write_ctx: dict[str, Any]

    # ── 可观测性 ───────────────────────────────────────────
    used_model: str
    error: str | None


__all__ = ["WorkflowState"]
