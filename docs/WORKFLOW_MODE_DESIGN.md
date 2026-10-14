# AI 工作流模式 · 功能设计

> 状态：设计定稿（经 grill-me 逐分支质询）  
> 范围：在现有 AI 对话功能之上新增独立的「工作流模式」  
> 设计基线：`backend/` FastAPI + LangGraph + Redis + 智谱 AI；`frontend/` Vue 3 + Vite（单路由 `/chat`）

---

## 0. 需求映射表

| 原始需求                         | 落在本设计的哪一节      | 核心机制                                           |
| ---------------------------- | -------------- | ---------------------------------------------- |
| 1 交互式提问 + 优化用户输入为可执行任务描述     | §5 澄清机制、§6 任务书 | 结构化问题卡（每题带推荐答案）≤3 轮 → 可编辑任务书                   |
| 2 工作区 + 临时文件引用（不入文档管理）       | §9 工作区与临时文件    | 独立 `workflow.db` + 磁盘目录，**不进 Chroma**，TTL 7 天  |
| 3 输入区参考 DeepSeek / WorkBuddy | §11 输入区设计      | 分段控件 + 工作空间/权限选择器 + `+` 文件 / `@` 引用 / `/` 技能   |
| 4 联网搜索返回真实可访问链接              | §10 联网搜索       | Tavily 返回的 `url` 直接落库，**LLM 禁止自写 URL**，只允许引用编号 |
| 5 思考过程与执行过程可视化               | §8 事件协议与呈现     | `thinking_delta` + 步骤卡时间线 + 任务书制品              |
| 6 人工审查 / AI 自动执行             | §7 执行控制        | run 级模式 + 步骤级 `requires_approval` + 强制审批白名单    |

---

## 1. 总体形态

### 1.1 承载形式：新路由 `/work` + 抽取共享外壳

```
frontend/src/
├─ layouts/AppLayout.vue     ★ 新：侧边栏 + 主题 + 用户区 + 「对话｜工作」分段控件
├─ views/Chat.vue            瘦身：移除 sidebar 区块，只保留消息区与输入区
├─ views/WorkView.vue        ★ 新：工作模式主视图
└─ components/workflow/*     ★ 新：见 §12
```

路由：

```js
{ path: '/chat', component: Chat, meta: { requiresAuth: true } }   // 不变
{ path: '/work', component: WorkView, meta: { requiresAuth: true } } // 新增
{ path: '/work/share/:token', component: WorkShareView, meta: { public: true } } // 新增
```

**为什么抽外壳而不是在 `Chat.vue` 里加 `v-if`**：`Chat.vue` 现为 3944 行；分支 5/7/8 会新增运行记录、步骤卡、审批按钮等一整套独立 UI，塞进同一组件维护成本持续上升。抽外壳是一次性成本，且不触碰任何后端契约。

### 1.2 侧边栏的内容按模式切换

| 区域     | 对话模式                         | 工作模式                            |
| ------ | ---------------------------- | ------------------------------- |
| 顶部     | `对话｜工作` 分段控件（AppLayout 统一承载） | 同左                              |
| 侧边栏列表  | 会话列表（**现状不动**）               | **任务列表**（run），复用会话列表的 CSS 与交互形态 |
| 侧边栏主按钮 | 新建对话                         | 新建任务                            |
| 输入区上方  | 长文本模板 pills + 模型选择（现状）       | 模型选择 + **联网搜索开关**               |
| 输入区下方  | —                            | **`选择工作空间 ⌄`** + **`执行权限 ⌄`**   |
| 工作区管理  | —                            | 从工作空间下拉的「管理工作区…」进抽屉             |

**工作区不是侧边栏的一级导航**，而是输入区的作用域选择器 —— 与参考截图（`选择工作空间 ⌄` 位于输入框下方、与 `默认权限 ⌄` 并排）保持一致。

### 1.3 共享与隔离的状态

| 状态         | 跨模式           | 说明                                                                            |
| ---------- | ------------- | ----------------------------------------------------------------------------- |
| 主题、登录态、用户区 | ✅ 共享          | 由 AppLayout 统一持有，`toggleTheme` 上提                                             |
| 模型选择       | ❌ **分别记忆**    | `preferredModel:chat` / `preferredModel:work`。共用会导致切换模式后模型被意外改掉，而工作流通常需要更强的模型 |
| 联网搜索       | ❌ 分别控制        | 对话＝按需智能触发（`force_search` + `should_search()`）；工作＝**显式开关**                     |
| 长文本模板面板    | ❌ 不带进 `/work` | 那是对话模式的产物；工作模式有自己的任务定义方式                                                      |
| 工作区选择      | 仅 `/work`     | —                                                                             |

---

## 2. 后端目录与依赖边界

```
backend/app/
├─ api/v1/workflow.py            ★ 路由层（薄，只做参数校验 + 调 service）
├─ schemas/workflow.py           ★ Pydantic 模型（请求/响应/SSE 事件类型）
├─ services/workflow_service.py  ★ 业务编排
├─ graph/workflow/               ★ 新图，独立于 qa_graph
│   ├─ build.py                   build_workflow_graph()
│   ├─ state.py                   WorkflowState（不复用 RAGState）
│   └─ nodes/
│       ├─ intake.py
│       ├─ clarify.py
│       ├─ plan.py
│       ├─ execute.py             单步执行（内含工具调用 + 生成 + 重试）
│       └─ deliver.py
├─ repositories/workflow_repo.py  ★ SQLite 读写（workspaces/runs/steps/run_files）
└─ workflow/
    ├─ file_context.py            临时文件加载 + 分块 + 摘要策略
    └─ cleanup.py                 过期文件/产物清理任务
```

**依赖方向（必须满足 `tests/unit/test_layering.py`）**

```
api/v1/workflow.py
   → services/workflow_service.py
       → graph/workflow/* · repositories/workflow_repo.py · workflow/*
           → rag/* · tools/* · agents/* · memory/* · core/*
```

硬性约束：

- `core/` **禁止** import services / graph / api / repositories
- `graph/workflow/*` 复用 `graph/nodes/{Deps, bind}`、`graph/checkpointer.make_checkpointer`、`graph/nodes/respond.emit`、`tools/base.get_registry`，但 **不 import `qa_graph` 的任何节点**（`route_by_intent`、`RAGState` 一律不复用）
- `rag/` 禁止 import `app/graph`（历史约束，`Document` 转换器在 `app/utils/documents.py`）

### 2.1 为什么不复用 `qa_graph`

`qa_graph` 的入口是 `classify_intent → route_by_intent`，7 条分支全是「一问一答」语义，终态统一 `persist → END`（见 `graph/build.py`）。工作流是**多相位 + 门禁 + 可中断可恢复**，拓扑与语义都不同。混在一起的三个具体代价：

1. `route_by_intent` 从 7 分支变 8+，`edges.py` 的条件函数开始承载两种语义
2. `RAGState` 要新增 `plan` / `steps[]` / `current_step` / `pending_approval` 等字段，而 `RAGState` 同时被 v3 契约与 `qa_graph` 消费，会变成上帝对象
3. 单轮问答不存在「等审批」状态，而工作流的核心状态就是它

---

## 3. 数据模型

新增独立 SQLite 文件 `data/workflow.db`（与 `data/conversations.db`、`data/user_profile.db` 并列）。

**为什么不复用 `conversations` 表**：`GET /conversations` 的返回结构是对外契约（`tests/test_openapi_contract.py` 兜底），加 `kind` 字段会直接撞契约；且步骤状态是状态机数据，塞进消息 JSON 会让「查所有等待审批的 run」变成全表扫。

```sql
-- 工作区：全局共享（visibility 预留 private，本期只有 shared）
CREATE TABLE workspaces (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    owner        TEXT NOT NULL,              -- 创建者 username
    visibility   TEXT NOT NULL DEFAULT 'shared',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

-- 运行记录
CREATE TABLE runs (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL REFERENCES workspaces(id),
    username       TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    raw_input      TEXT NOT NULL,            -- 用户原始输入（用于"原话 → 任务书"对比展示）
    status         TEXT NOT NULL,            -- draft|clarifying|planning|awaiting_approval|running|paused|done|failed|cancelled
    mode           TEXT NOT NULL,            -- manual|auto
    model          TEXT NOT NULL DEFAULT '',
    force_search   INTEGER NOT NULL DEFAULT 0,
    clarify_round  INTEGER NOT NULL DEFAULT 0,
    plan_json      TEXT,                     -- 任务书（用户可编辑，真相源）
    summary        TEXT,                     -- deliver 相位产出的结论文本
    error          TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

-- 步骤：run 的状态机主体
CREATE TABLE steps (
    id                 TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    idx                INTEGER NOT NULL,      -- 顺序号，从 1 开始
    kind               TEXT NOT NULL,         -- tool|llm|synthesis|unplanned
    title              TEXT NOT NULL,         -- 用户看得懂的一句话
    action_json        TEXT NOT NULL,         -- 声明式 action（见 §6.2）
    requires_approval  INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL,         -- pending|awaiting_approval|approved|running|done|failed|skipped|rejected
    output_json        TEXT,                  -- {summary, sources[], artifact_ref}
    error              TEXT,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    approved_by        TEXT,
    approved_at        REAL,
    started_at         REAL,
    ended_at           REAL,
    UNIQUE (run_id, idx)
);

-- 临时文件引用（生命周期绑定 run，不进向量库）
CREATE TABLE run_files (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,             -- 用户看到的原始文件名
    stored_path    TEXT NOT NULL,             -- data/workspaces/{ws}/files/{id}_{name}
    size_bytes     INTEGER NOT NULL,
    sha256         TEXT NOT NULL,             -- 去重 + 完整性
    kind           TEXT NOT NULL,             -- input|artifact
    parsed_chars   INTEGER,                   -- 解析后字符数（用于选择上下文注入策略）
    ttl_expires_at REAL NOT NULL,
    created_at     REAL NOT NULL
);

CREATE INDEX idx_runs_username    ON runs(username, updated_at DESC);
CREATE INDEX idx_runs_status      ON runs(status);           -- "待审批列表"核心查询
CREATE INDEX idx_steps_run        ON steps(run_id, idx);
CREATE INDEX idx_files_ttl        ON run_files(ttl_expires_at);
```

### 3.1 ID 与隔离约定

- 主键统一 `uuid4().hex` 短格式（与现有会话 ID 风格保持一致）
- 用户维度仍是 `username`
- 图执行 `thread_id = f"{username}:run:{run_id}"`（沿用 `runner.thread_id_for` 的形态，**不要用 `checkpoint_ns`** —— 那会被 LangGraph 解释为子图命名空间）
- 状态真相源 = `workflow.db.steps`；checkpointer 只承载单次调用内的中间态

### 3.2 权限边界（先落字段，不落校验）

| 操作         | 规则                                                                 |
| ---------- | ------------------------------------------------------------------ |
| 读工作区 / run | 所有登录用户可见（这就是"全局共享"）                                                |
| 改 / 删工作区   | 仅 `owner`；删除前若工作区内含他人 run，前端弹确认后级联删除（不阻断）                          |
| 改 / 删 run  | 仅创建者                                                               |
| 不做的事       | 不做 `visibility` 开关 UI —— 当前系统只有 `ADMIN_USERNAME` 单账号（无注册端点），做出来是空转 |

参照现有范式：`services/conversation_service.py::_assert_owner`。字段现在就写，避免以后加多用户时改表。

---

## 4. 运行生命周期

```
intake ──▶ clarify ──▶ plan ──▶ gate ──▶ execute ──▶ deliver
                    ▲            │        (循环)
                    └── 驳回重新规划 ┘
```

| 相位      | 终态 `runs.status`                         | 产出                                   |
| ------- | ---------------------------------------- | ------------------------------------ |
| intake  | `draft`                                  | run 记录 + 文件引用清单                      |
| clarify | `clarifying`                             | 一组澄清问答（可能多轮，≤3）                      |
| plan    | `planning`                               | **任务书** `plan_json`                  |
| gate    | `awaiting_approval`                      | 用户审批通过 → `running`；驳回 → 回 `planning` |
| execute | `running` → `done` / `failed` / `paused` | 每步 `output_json`                     |
| deliver | `done`                                   | `summary` + 产物清单                     |

### 4.1 关键取舍：HTTP 驱动的显式状态机，而不是 `interrupt()` 挂起

**不采用** LangGraph `interrupt()` + 长连接等待审批的常见 HITL 写法，改用**每次 HTTP 请求推进一个相位/一步**。

理由（按重要性排序）：

1. **审批等待时间不可控。** 人工审查意味着人类思考时间可能是分钟到小时级。用 SSE 长连接等审批，客户端一断线（刷新、切标签、断网、换设备）状态就悬空。
2. **避免双重真相源。** `steps` 表本来就存步骤状态（§3），若再让 LangGraph 在 checkpointer 里维护一份挂起态，同一个事实有两个存储、两套更新路径，必然漂移。
3. **多 worker 部署下不依赖 worker 亲和性。** 恢复审批的新请求可能落到另一个 worker；显式状态机只读数据库，天然无亲和性要求。
4. **「待审批列表」是核心查询。** 侧边栏要能列出"哪些任务卡在等你"，这需要数据库可查，而不是遍历 checkpointer。

**一致性说明**：`workflow_graph` 仍编译一次并复用（拓扑固定）。`plan` 与 `execute_step` 作为**两个可独立调用的图入口**，各自 `ainvoke` 到 END。图负责「一步之内」的编排（工具调用 + 生成 + 重试），HTTP 负责「步骤之间」的推进与审批。**不使用 `interrupt()` 的挂起语义。**

---

## 5. 澄清机制（需求 1）

### 5.1 形态：结构化问题卡 + 自由输入兜底

每轮产出 **1–3 个问题**，每个问题：

```jsonc
{
  "id": "q1",
  "question": "这份对比报告的读者是谁？",
  "options": [
    { "label": "技术决策者", "value": "tech_lead" },
    { "label": "非技术管理层", "value": "exec" },
    { "label": "我自己存档", "value": "self" }
  ],
  "recommended": "tech_lead",     // ★ 每题必带推荐答案，可一键采纳
  "reason": "你提到需要推理成本对比，这通常是技术选型场景"
}
```

**「每题必带推荐答案」是这一节的核心设计**：它把澄清从「审讯」变成「确认」，用户大部分情况只需点 3 次就完成一轮。这直接对应需求里说的"像 `/grill-me` 这类技能"——该技能的交互契约就是「先给推荐答案，再提问」。

### 5.2 终止条件

**三项必填信息齐备即停止追问**：

1. **交付目标** —— 最终要产出什么形态的东西
2. **受众 / 用途** —— 决定详略、语气、是否需要引用
3. **约束** —— 格式 / 长度 / 语言 / 时间范围 / 必须包含或排除的内容

### 5.3 轮次上限与兜底

- `WORKFLOW_MAX_CLARIFY_ROUNDS` 默认 **3**
- 到达上限仍不齐备 → **不再追问**，直接把「当前理解 + 明确的假设清单」写成任务书，并在任务书顶部标注「以下为假设，请确认」
- 理由：无限追问是 LLM 的典型反模式；用户随时可以关掉

### 5.4 可跳过

- 输入区提供「跳过澄清，直接生成任务书」
- `mode = auto` 时**默认仍保留 1 轮**（信息不足必然导致任务书失败），但可在权限选择器里选「完全跳过」

### 5.5 「优化用户输入」的可见化

任务书页同时展示三段，形成对比：

```
原始输入        「帮我看看这几家推理成本」           ← 用户原话，只读
澄清补充        · 读者=技术决策者 · 范围=国产 · 时间=2026Q3
──────────────────────────────────────────────
任务书（可编辑）目标 / 约束 / 步骤清单 / 引用文件
```

这就是需求 1 里"优化用户的输入，并将其整理为更清晰、可执行的任务描述"的落地形态 —— 优化结果不是一句改写后的 prompt，而是**一份结构化的、可编辑的制品**。

---

## 6. 任务书（plan 相位产物）

### 6.1 结构

```jsonc
{
  "goal": "产出 2026 年国产大模型推理成本对比报告",
  "audience": "技术决策者",
  "constraints": ["中文", "含每千 token 价格表", "必须给出可访问的来源链接"],
  "assumptions": [],                    // 跳过/超轮澄清时填写
  "file_refs": ["rf_8a3c..."],
  "steps": [
    {
      "idx": 1, "kind": "tool",
      "title": "搜索 2026Q3 国产大模型定价信息",
      "action": { "tool": "web_search", "args": { "query": "2026 国产大模型 API 定价 每千 token", "max_results": 6 } },
      "requires_approval": false
    },
    {
      "idx": 2, "kind": "llm",
      "title": "从搜索结果抽取各模型定价并结构化",
      "action": { "prompt_ref": "extract_pricing", "inputs": ["step1"] },
      "requires_approval": false
    },
    {
      "idx": 3, "kind": "synthesis",
      "title": "整合为对比报告并附来源",
      "action": { "prompt_ref": "compose_report", "inputs": ["step2"] },
      "requires_approval": true
    }
  ]
}
```


### 6.2 `action` 必须是声明式的

计划阶段只写「**将要**调用 `web_search`，关键词 = X」，执行阶段才真的调用。

这是「计划可审批」成立的前提：用户审批时读到的是**一段人能读懂的承诺**（"她打算去搜这个关键词"），而不是一句模糊的"她打算做点研究"。如果 `action` 里放的是自然语言描述，后端就无法按步骤调度工具，审批也就无处落地。

### 6.3 用户可编辑

`PUT /workflow/runs/{id}/plan` 允许用户修改：步骤标题、顺序（拖拽）、增删步骤、`requires_approval` 标记、`action.args`。保存后**以用户版本为准**（`plan_json` 是真相源，重新进 LLM 上下文）。

### 6.4 步数上限

`WORKFLOW_MAX_STEPS` 默认 **12**。超出时 `plan` 节点不发车，直接把计划摆出来要求用户裁剪或改用自动模式。理由：步数失控既是 LLM 规划的典型失败模式，也是成本失控点。

### 6.5 四类步骤粒度

| `kind`      | 说明                 | 例子                      |
| ----------- | ------------------ | ----------------------- |
| `tool`      | 调用一个已注册工具，有入参有返回   | `web_search(query=...)` |
| `llm`       | 一次纯生成，无外部副作用       | "根据搜索结果起草对比表"           |
| `synthesis` | 产出最终可交付内容          | "整合为最终报告"               |
| `unplanned` | 执行中临时插入，**永远单独审批** | "发现还需查一次汇率"             |

**明确排除**：模型的「思考过程」不算 step。它是可视化层的一条 thinking 流（§8），不参与审批。若算成 step，一个 8 步任务会变成 40 次审批。

### 6.6 `unplanned` 逃生口

执行中若发现计划不足，允许插入 `unplanned` 步骤，但必须携带一句「为什么需要额外这一步」，且**无论什么模式都强制单独审批**。既保留灵活性，又不让用户失去控制。

---

## 7. 执行控制：人工审查 vs AI 自动执行（需求 6）

### 7.1 模式粒度：run 级默认 + 步骤级覆盖

| 层级   | 字段                        | 谁决定                       |
| ---- | ------------------------- | ------------------------- |
| run  | `mode: manual \| auto`    | 用户在输入区「执行权限」选择器设定         |
| step | `requires_approval: bool` | 计划阶段由 AI 建议，**用户可在任务书里改** |

生效规则：`需要停下 = (mode == manual && step.requires_approval) || step 命中强制白名单 || gate == "plan"`

### 7.2 断点位置

| 断点                             | 人工模式     | 自动模式   | 备注                        |
| ------------------------------ | -------- | ------ | ------------------------- |
| `plan` 产出后（任务书审批）              | **强制**   | **强制** | 两种模式都停一次 —— 这是成本与方向的双重确认点 |
| `requires_approval = true` 的步骤 | 执行**前**停 | 不停     |                           |
| `kind == unplanned` 的步骤        | **强制**   | **强制** | 逃生口必须单独审批                 |
| 命中强制审批白名单的步骤                   | **强制**   | **强制** | 安全兜底，见下                   |

**强制审批白名单**：`WORKFLOW_REQUIRE_APPROVAL_TOOLS` 默认 `code_executor,file_write`。这两个工具能执行任意代码 / 写文件，无论用户把模式设成什么，都停下等确认。这是唯一一处"模式设置被覆盖"的地方，也是必须的一处。

### 7.2.1 产出文件：`file_write` 与两步式写法（P14）

工作流此前**没有任何写文件的通路** —— `code_executor` 明确禁止 `open(`，而
`llm`/`synthesis` 步的产出只是一段字符串（不落盘、不执行）。用户说「让 AI 写个脚本
保存成文件」时，得到的只有页面上一段文本。`file_write` 补的就是这一条。

**两条落点，语义不同**

| `target`        | 落点                                | 适用                        |
| --------------- | --------------------------------- | ------------------------- |
| `artifacts`（默认） | `data/workspaces/<ws>/artifacts/` | 交付一个文件：随即出现在产物列表，可下载、可分享 |
| `bound_folder`  | 工作区**已绑定**的本地文件夹                  | 直接落到用户自己的目录               |

`bound_folder` ⚠️ **未绑定就直接拒绝**，而不是"没绑定就不校验" —— 后者等于给未绑定的
工作区开一条任意路径写入的后门。路径只接受**文件名**（剥目录分隔符 + `abspath` 后
`commonpath` 包含性校验），后缀白名单与"允许引用"的读白名单**是两回事**：
读白名单里有 `.pdf`/`.png` 这类不可能生成的二进制格式，写白名单必须有 `.py`/`.js`。
同名文件**不覆盖**，改名加序号并把改名事实回报给用户。

**推荐写成两步**（规划提示词已这样引导）：

```
1. kind: llm        —— 写出完整内容（不要用省略号占位）
2. kind: tool       —— file_write {"path": "fib.py", "content_from_step": 1}
```

用步骤编号引用比让模型把代码**再抄一遍**进计划 JSON 更可靠：代码里的换行与引号极易
破坏 JSON，而且白白多花一次输出 token。落盘前会**剥掉 markdown 围栏** —— 模型被训练成
用 ` ``` ` 输出代码，不剥的话文件第一行是 ` ```python `，**根本无法运行**；
`.md` 家族不剥（在 markdown 里围栏是内容本身）。

**写入上下文不来自计划**：产物目录 / 绑定目录 / 后缀白名单 / 大小上限由服务层经
`state["write_ctx"]` 注入。让模型自己指定写到哪里，等于把路径决定权交给它。

### 7.2.2 代码沙箱策略（P14 重写）

`code_executor` 的校验从**子串黑名单**改成 **AST 白名单**（`app/tools/code_sandbox.py`）。
旧做法两头都不成立：`from math import sqrt`、注释里写 `# import 说明` 全被拒（实测误伤 4/4），
而 `object.__subclasses__()`（可达 542 个类，含 `Popen`）却放行。

- **允许 import 一份标准库白名单**（`CODE_EXEC_ALLOWED_MODULES`，默认
  `math`/`json`/`re`/`csv`/`statistics`…；`os`/`sys`/`subprocess`/`socket`/`ctypes`/`importlib`
  永不允许）。空 = 用默认，`none` = 显式完全禁用。
  放开这一口是"生成代码可用"的前提 —— 不放行的话模型写的每一句 `import` 都会被拒。
- 运行时 `builtins` 必须提供**受限的 `__import__`**：AST 放行而运行时没有，会变成
  「校验通过、执行报 `__import__ not found`」，这是最坏的一种不一致。
- 禁逃逸属性（`__subclasses__`/`__bases__`/`__globals__`…）与危险调用
  （`getattr`/`eval`/`exec`/`open`…）；**报错带行号**，且拒绝 `open()` 时直接指向 `file_write`。
- 超时是**真的**：`PyThreadState_SetAsyncExc` 向工作线程注入异常。旧的
  `thread.join(timeout)` 只是"不再等待"，线程会继续占着 CPU 核。

⚠️ **这仍然不是安全边界**：它防的是"模型写错"，防不了"有人故意攻击"。要对外部署，
必须换成子进程 + 资源限制 + 只读文件系统。

### 7.2.3 状态推进必须单调（并发流之间的竞态）

澄清与「生成任务书」是**两条独立的 SSE 流**，用户完全可能在澄清还没返回时就点了
生成任务书。实测到的时序：

```
09:58:14  任务书已生成      → status = awaiting_approval
09:58:25  澄清第 1 轮返回   → status = clarifying        ← 晚了 11 秒，把状态拉回去了
```

结果是**批准按钮直接消失，而界面上没有任何错误** —— 用户看到的就是"卡住了"。
因此澄清流写状态前必须检查当前状态（`_write_clarify_status()`，白名单
`{draft, intake, clarifying}`），其它情况只落库澄清数据、不回退状态。
**凡是"并发的两方都会写同一个字段"的地方，都要先问一句：后写的那方有没有资格写。**

### 7.3 执行中可切换模式

`PATCH /workflow/runs/{id}` 改 `mode`，**立即生效于尚未开始的步骤**。

理由：用户常在看到前两步质量后决定放权（或反过来收紧）。把模式锁死在整个 run 上是过度约束。

### 7.4 驳回与重试

| 动作   | 端点                             | 语义                                                  |
| ---- | ------------------------------ | --------------------------------------------------- |
| 批准单步 | `POST .../steps/{idx}/approve` | 该步 `approved`，可执行                                   |
| 驳回单步 | `POST .../steps/{idx}/reject`  | 带一段修改意见；该步标 `rejected`，**从该步起重新规划后续步骤**（保留已完成步骤的产物） |
| 重试   | `POST .../steps/{idx}/retry`   | 仅 `failed` 且 `retryable` 的步骤可用；`retry_count` 计数     |
| 跳过   | `POST .../steps/{idx}/skip`    | 标 `skipped`，下游步骤缺失输入时由 `execute` 自行降级或报 `failed`    |

**失败处理策略**：单步失败不自动重试（避免烧钱），停止推进并把 `run.status` 置 `paused`，前端展示错误 + 「重试 / 跳过 / 驳回重规划 / 终止」四个选项。`WORKFLOW_STEP_TIMEOUT_SECONDS` 默认 180 秒。

---

## 8. 过程可视化与事件协议（需求 5）

### 8.1 契约隔离原则

现有 SSE 契约被 `tests/test_sse_contract.py` 钉死为 `intent → sources → (tool_calls) → chunk* → final`。工作流**不修改任何现有事件语义**，而是使用**独立的事件命名空间 + 独立端点**。`/ask/stream` 一行不改。

### 8.2 事件类型

```jsonc
{ "type": "workflow_phase",    "content": { "phase": "clarify|plan|execute|deliver", "status": "start|end" } }
{ "type": "thinking_delta",    "content": { "text": "..." } }            // 思考流，可折叠展示
{ "type": "clarify_questions", "content": { "round": 1, "questions": [ /* §5.1 */ ] } }
{ "type": "plan_ready",        "content": { "plan": { /* §6.1 */ } } }
{ "type": "step_start",        "content": { "idx": 2, "title": "...", "kind": "tool", "action": {} } }
{ "type": "step_delta",        "content": { "idx": 2, "text": "..." } }  // 该步的流式输出
{ "type": "step_done",         "content": { "idx": 2, "output": { /* §8.3 */ } } }
{ "type": "step_failed",       "content": { "idx": 2, "error": "...", "retryable": true } }
{ "type": "approval_needed",   "content": { "gate": "plan|step", "idx": 2, "payload": {} } }
{ "type": "artifact_ready",    "content": { "name": "...", "file_id": "rf_..." } }
{ "type": "final",             "content": { "run_id": "...", "summary": "...", "sources": [], "artifacts": [] } }
```

**流式分片不得 `trim()`**（会吃掉词间空格，英文粘成一坨）—— 沿用现有 `cleanLLMChunk(text, { trim: false })` 的既有结论。

### 8.3 步骤输出结构

```jsonc
{
  "summary": "从 6 个来源提取到 5 家厂商的定价",
  "sources": [
    { "title": "…", "href": "https://…", "snippet": "…", "reachable": true }
  ],
  "artifact_ref": null        // 若产出文件 → run_files.id（kind=artifact）
}
```

### 8.4 呈现形态（三层）

| 层   | 组件                                  | 交互                                                                                                |
| --- | ----------------------------------- | ------------------------------------------------------------------------------------------------- |
| 思考流 | `ThinkingStream.vue`                | 生成时展开为灰色斜体块（DeepSeek 风格）；结束后自动折叠为「已思考 N 秒」，可点开                                                    |
| 步骤卡 | `StepTimeline.vue` + `StepCard.vue` | 垂直时间线。每卡 = 序号 + 标题 + `kind` 徽标 + 状态圆点（待执行 / 执行中 / 完成 / 失败 / **等待审批**）。展开可见 action 明细、工具返回、来源链接、错误 |
| 任务书 | `PlanEditor.vue`                    | `gate` 时呈现；步骤可拖拽排序 / 删除 / 加注 / 切换 `requires_approval`；「原话 vs 任务书」对比视图                             |

**用户不需要在"结果"和"过程"之间切换视图** —— 步骤时间线本身就是结果页，最终产物作为最后一张卡出现在时间线末端。这与「思考过程与执行过程都要可见」的要求一致。

---

## 9. 工作区与临时文件（需求 2）

### 9.1 工作区 = 逻辑容器 + 一个磁盘目录

```
data/workspaces/{workspace_id}/
├─ files/{file_id}_{原始文件名}     用户上传的引用文件（kind=input）
└─ outputs/{run_id}/...             工作流生成的产物（kind=artifact）
```

逻辑容器用于隔离与选择器展示，磁盘目录用于实际存储。**删除工作区 = 删表行 + `shutil.rmtree` 目录**。

### 9.2 文件解析：复用解析器，不复用入库路径

- 解析直接调 `app/document/parser.py::load_any()`（已支持 pdf / docx / pptx / xlsx / csv / md / txt / 图片）
- **绝不走 `DocumentService` 的入库流程** —— 需求明确要求"不作为文档存入文档管理"
- **绝不写入 Chroma**

**为什么不进 Chroma**：向量库文档的增删要清 `cache:retrieval:*`，且会污染知识库检索结果 —— 用户在对话模式提问时会突然命中工作流的临时文件，这是错误行为。工作流的临时文件只对**当前 run** 可见。

### 9.3 文件进入上下文的策略（按解析后大小自动选择）

| 解析后字符数         | 策略                                                                                              | 配置项                                   |
| -------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------- |
| ≤ 8,000        | **全文直接进 prompt**                                                                                | `WORKFLOW_FILE_INLINE_MAX_CHARS=8000` |
| 8,000 – 60,000 | 用现有 `chunk_text()` 分块 → **在 run 内做一次性关键词/嵌入召回**（嵌入复用 `rag/providers.get_embedder()`，但**只算不落库**） | `CHUNK_SIZE` / `CHUNK_OVERLAP`（现有）    |
| > 60,000       | 先自动插入一个 `llm` 步骤做「文件摘要」，摘要进上下文，正文按需读                                                            | —                                     |

单文件上限 `WORKFLOW_MAX_FILE_MB` 默认 20。

### 9.4 生命周期：TTL 7 天

- `run_files.ttl_expires_at = now + WORKFLOW_FILE_TTL_DAYS(默认 7) 天`
- 清理任务 `workflow/cleanup.py`：**每小时扫一次** `idx_files_ttl`，删表行 + 删磁盘文件
- ⚠️ **现有代码没有可蹭的扫描机制**（`RedisSettings` 只有 Redis 侧 TTL 与 `checkpoint_ttl`），必须自己写定时任务。启动方式沿用 `app/main.py` 里现有的后台任务挂载方式，并遵循 `REDIS_FALLBACK_TO_MEMORY` 那套"降级不致命"的写法
- run 的产物（`kind=artifact`）与输入文件**同一套 TTL**；用户可手动「保留」把 `ttl_expires_at` 置空

### 9.5 分享与文件的关系

- `POST /workflow/runs/{id}/share` 生成 `share:{token}`（复用现有 Redis 键规范，TTL 默认 `ttl_share_default`）
- 分享页**只读**，包含：任务书、步骤时间线、来源链接、结论文本
- **文件原始二进制不进分享页** —— 文件单独走一次性下载链接
- 理由：`share:{token}` 现实现对会话消息是"一把梭全给"；工作流若照抄，等于把用户丢进工作区的本地文件内容一并公开

---

## 10. 联网搜索（需求 4）

### 10.1 复用现有资产

`app/tools/web_search.py` 已实现 Tavily 集成（`WebSearchToolAdapter`），`.env` 里 `TAVILY_API_KEY` 已配置且非空。**不需要新写搜索层**。

### 10.2 「真实可访问链接」的保证机制

这是需求 4 里"必须返回真实可访问的搜索链接，而非仅生成内容"的技术落点，三条规则：

1. **链接只能来自 Tavily 响应的 `url` 字段。** 后端把 `results[].url` 原样写入 `steps.output_json.sources[].href`。
2. **LLM 禁止自行书写 URL。** 拼进 prompt 的上下文里，来源标注为 `【来源 1】…【来源 6】`，并明确指示「只允许以 `[1]` 形式引用编号，**不得输出任何 URL 字符串**」。生成的正文中若出现 `http` 开头且不在 Tavily 返回集合内的串，后端做一次**剔除并告警**（`[非法链接剔除]`）。
3. **后端做一次可达性标记**：对每个 `href` 发 `HEAD` 请求（超时 3 秒，失败降级为"未校验"而不报错），写回 `sources[].reachable`。前端对 `reachable=false` 的链接标注「可能失效」。

### 10.3 前端呈现

- 来源列表渲染为 `<a target="_blank" rel="noopener noreferrer">`，展示 **域名 + 标题**（域名可快速判断可信度）
- 正文里的 `[1]` 编号做成可点击锚点，跳到对应来源
- 来源卡挂在**产出它的那张步骤卡内部**，而不是全部堆在答案末尾 —— 用户能看到"这个链接是哪一步搜到的"

### 10.4 触发方式

| 模式  | 行为                                                                     |
| --- | ---------------------------------------------------------------------- |
| 对话  | 保持现状：`force_search` + `should_search()` 智能触发（对应截图 4 的 tooltip「按需搜索网页」） |
| 工作流 | **显式开关**（对应截图 4 的「智能搜索」pill 亮/灭），计划阶段默认给含搜索的步骤                         |

### 10.5 风险提示

Tavily 是外部付费 API，配额与限流不受本系统控制。`web_search.py` 已有内存级 LRU（256 条 / 5 分钟）与 3 次指数退避重试。建议补充：`WORKFLOW_SEARCH_MAX_CALLS_PER_RUN` 默认 **5**，超出则该步失败并提示用户，而不是静默烧配额。

---

## 11. 输入区设计（需求 3）

参考截图：DeepSeek（输入框内 pills + 附件 + 发送）与 WorkBuddy（输入框下方工作空间 / 权限选择器）。

```
┌──────────────────────────────────────────────────────────────┐
│  今天帮你做些什么？@ 引用文件，/ 调用技能                       │   ← 多行自适应
│                                                              │
│  [+]                                    模型 ⌄   智能搜索  ⬆  │   ← 左下 / 右下
└──────────────────────────────────────────────────────────────┘
  工作空间 ⌄            执行权限 ⌄                                ← 输入框下方（对齐截图 5）
```

| 元素         | 位置   | 行为                                                                   |
| ---------- | ---- | -------------------------------------------------------------------- |
| `+` 添加文件   | 左下   | 打开文件选择器 → 上传为 `run_files(kind=input)`；上传后以 chip 形式显示在输入框上方，可移除       |
| `@` 引用文件   | 输入框内 | 唤起当前 run 已上传文件的候选列表；等价于 `+` 但更快（对齐截图 5 的「@ 添加上下文」）                   |
| `/` 技能与指令  | 输入框内 | 预留：列出内置任务模板（可先接现有 `summary/translate/qa` 三件套）                        |
| 模型选择 `⌄`   | 右下   | 复用现有 `GET /models` 与选择器组件，但**读写 `preferredModel:work`**              |
| 智能搜索       | 右下   | 开关 pill；亮 = `force_search=true` 且计划必须包含搜索步骤                          |
| 发送 `⬆`     | 右下   | Enter 发送 / Shift+Enter 换行                                            |
| 选择工作空间 `⌄` | 下方   | 下拉列出全部工作区 + 「新建工作区…」+「管理工作区…」                                        |
| 执行权限 `⌄`   | 下方   | 两个选项：**人工审查**（manual）/ **AI 自动执行**（auto）；默认取 `WORKFLOW_DEFAULT_MODE` |

**发送后的行为**：创建 run → 进 `clarify` 相位 → 若 `auto` 且开了「完全跳过澄清」，直接进 `plan`。

---

## 12. API 接口清单

统一前缀 `/api/v1/workflow`。全部需要 `current_user`；写操作挂 Redis 限流（复用 `core.deps.rate_limit`）。

### 工作区

| 方法       | 路径                  | 说明                             |
| -------- | ------------------- | ------------------------------ |
| `POST`   | `/workspaces`       | 创建                             |
| `GET`    | `/workspaces`       | 列表（含 run 数、文件数统计）              |
| `PATCH`  | `/workspaces/{wid}` | 改名                             |
| `DELETE` | `/workspaces/{wid}` | 删除（级联 run/steps/files + 删磁盘目录） |

### 运行

| 方法       | 路径                   | 说明                                                                   |
| -------- | -------------------- | -------------------------------------------------------------------- |
| `POST`   | `/runs`              | 创建 run（`workspace_id`, `raw_input`, `mode`, `model`, `force_search`） |
| `GET`    | `/runs`              | 列表（可按 `workspace_id` / `status` 过滤）                                  |
| `GET`    | `/runs/{rid}`        | 详情（含 steps 全量）                                                       |
| `PATCH`  | `/runs/{rid}`        | 改 title / **mode（执行中可切）**                                            |
| `DELETE` | `/runs/{rid}`        | 删除                                                                   |
| `GET`    | `/runs/{rid}/export` | 导出为 Markdown                                                         |

### 澄清

| 方法     | 路径                           | 说明           |
| ------ | ---------------------------- | ------------ |
| `POST` | `/runs/{rid}/clarify/stream` | SSE：产出一轮澄清问题 |
| `POST` | `/runs/{rid}/clarify/answer` | 提交本轮答案       |
| `POST` | `/runs/{rid}/clarify/skip`   | 跳过澄清         |

### 任务书

| 方法     | 路径                        | 说明                        |
| ------ | ------------------------- | ------------------------- |
| `POST` | `/runs/{rid}/plan/stream` | SSE：生成任务书                 |
| `PUT`  | `/runs/{rid}/plan`        | 保存用户编辑后的任务书（服务端校验 schema） |
| `POST` | `/runs/{rid}/approve`     | 任务书审批放行                   |
| `POST` | `/runs/{rid}/reject`      | 驳回任务书 → 回 `planning`      |

### 步骤

| 方法     | 路径                                | 说明                          |
| ------ | --------------------------------- | --------------------------- |
| `POST` | `/runs/{rid}/steps/{idx}/execute` | SSE：执行单步（**HTTP 驱动的状态机核心**） |
| `POST` | `/runs/{rid}/steps/{idx}/approve` | 批准单步                        |
| `POST` | `/runs/{rid}/steps/{idx}/reject`  | 驳回单步（带意见）→ 从该步起重规划          |
| `POST` | `/runs/{rid}/steps/{idx}/retry`   | 重试失败步骤                      |
| `POST` | `/runs/{rid}/steps/{idx}/skip`    | 跳过                          |

### 交付 / 文件 / 分享

| 方法       | 路径                           | 说明             |
| -------- | ---------------------------- | -------------- |
| `POST`   | `/runs/{rid}/deliver/stream` | SSE：汇总产物       |
| `GET`    | `/runs/{rid}/artifacts`      | 产物列表           |
| `POST`   | `/runs/{rid}/files`          | 上传临时文件引用       |
| `DELETE` | `/runs/{rid}/files/{fid}`    | 移除引用           |
| `GET`    | `/files/{fid}/download`      | 下载（校验归属 + TTL） |
| `POST`   | `/runs/{rid}/share`          | 生成分享链接         |
| `GET`    | `/share/{token}`             | 只读分享页数据（公开接口）  |

### 12.1 对现有契约的影响（必读）

- **必须同步更新 `tests/data/openapi_v3_baseline.json`** —— 硬约束 6，否则 `test_openapi_contract.py` 会拦
- `tests/test_sse_contract.py` **不受影响**（`/ask/stream` 一行不改）
- **`/ask` 的 400 + `answer` 字段校验错误语义保持不变**，不得改成 422
- `tests/unit/test_layering.py` 需新增 `api/v1/workflow.py → services/workflow_service.py` 的依赖方向断言


## 14. 分期实施建议

| 期      | 交付内容                                                                  | 可独立验证                       |
| ------ | --------------------------------------------------------------------- | --------------------------- |
| **P0** | 抽 `AppLayout.vue` + 新增 `/work` 路由 + 分段控件；`WorkflowView` 骨架            | 前端能切换两个模式，对话功能零回归           |
| **P1** | `workflow.db` + `workflow_repo` + 工作区 CRUD + 输入区（工作空间/权限选择器）+ 任务列表侧边栏 | 能建工作区、建 run、上传文件、看到空 run 列表 |
| **P2** | `plan` 相位 + 任务书（生成 / 编辑 / 审批）                                         | 输入一句话 → 拿到可编辑任务书            |
| **P3** | `execute` 相位 + 步骤状态机 + SSE 事件协议 + `StepTimeline`                      | 自动模式下能跑完多步任务，能看到步骤卡与思考流     |
| **P4** | `clarify` 相位 + 结构化问题卡                                                 | 澄清 ≤3 轮后进 plan              |
| **P5** | 执行控制：手动审批 / 模式切换 / 驳回重规划 / 重试跳过                                       | 人工模式能逐步批准跑完                 |
| **P6** | 联网搜索接线 + 来源链接可达性校验 + 前端来源卡                                            | 能拿到可点击的真实链接                 |
| **P7** | `deliver` + 产物下载 + 分享页 + 文件 TTL 清理任务                                  | 分享链接可打开；过期文件被清理             |

P0–P3 构成可用的最小闭环（自动执行）；P4–P5 补上人工审查；P6–P7 补外部能力与生命周期。

---

## 15. 风险与未决项

| # | 风险 / 未决                                           | 影响                             | 处理建议                                                                                                              |
| - | ------------------------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| 1 | **临时文件解析能力不完整** —— Tesseract OCR 未安装，图片类文件解析为 0 页 | 用户传图片会得到空上下文                   | 上传时检测解析字符数为 0 → 明确提示「该文件未能解析出文本」，不静默进流程                                                                           |
| 2 | **Tesseract 未安装**（现有遗留）                           | 同上                             | 本期不装；在 §9.2 的解析结果上做显式失败提示                                                                                         |
| 3 | **清理任务与现有后台任务的协调** —— 现有代码没有周期性扫描机制               | 可能重复实现                         | 复用 `app/main.py` 现有的后台任务挂载方式，遵循"降级不致命"范式                                                                          |
| 4 | **多 worker 下的并发推进** —— 两个请求同时执行同一步                | 重复调用工具 / 写冲突                   | `execute` 端点执行前用现有分布式锁（`core/redis.py` 的锁原语）按 `run_id:step_idx` 加锁；DB 侧 `UPDATE ... WHERE status='approved'` 做乐观锁 |
| 5 | **Tavily 配额**                                     | 成本                             | §10.5 的 `search_max_calls_per_run` 硬上限                                                                            |
| 6 | **智谱内容风控 1301** 可能在生成中途掐断且不给 `finish_reason`      | 步骤输出不完整                        | 沿用现有 `truncated` 标记机制；步骤卡显示「输出可能被截断」                                                                              |
| 7 | **未决**：产物格式                                       | 用户可能期望 docx / xlsx 而非 Markdown | 本期先出 Markdown + txt；导出格式后续按需接 `tencent-docx` / `sheetagent` 类能力                                                   |
| 8 | **未决**：`/` 技能面板的具体技能来源                            | 需求 3 提到"/调用技能与指令"              | 本期先接现有三件套（summary/translate/qa），后续再定技能注册机制                                                                        |
| 9 | **未决**：run 是否支持多轮对话式追加                            | 用户可能想在 run 内继续追问               | 本期不做（run 是"一次任务"语义）；如需，作为下一期的 `follow_up` 相位                                                                      |

---

## 附录 A：我在本次设计中替你做的判断

以下 6 条是我在你未明确表态时按推荐值定下的。**任一条不认可，改动成本都很低**（多为一行配置或一个字段），列出以便你复核：

| #  | 判断                                                         | 备选                              |
| -- | ---------------------------------------------------------- | ------------------------------- |
| A1 | 模型选择**按模式分别记忆**（`preferredModel:chat` / `work`）            | 全局共用一个 key                      |
| A2 | `WORKFLOW_MAX_STEPS` = **12**，`MAX_CLARIFY_ROUNDS` = **3** | 各自调整                            |
| A3 | 文件 TTL = **7 天**，`FILE_INLINE_MAX_CHARS` = **8000**        | 各自调整                            |
| A4 | 采用 **HTTP 驱动的显式状态机**，不用 LangGraph `interrupt()` 挂起         | 用 `interrupt()` + 长连接（代价见 §4.1） |
| A5 | 强制审批工具白名单 = `code_executor,file_write`                     | 增删工具                            |
| A6 | 分享页**不含**文件原始二进制，文件单独走一次性下载链接                              | 分享范围放宽                          |
| A7 | 产出文件默认落在任务产物目录；写进绑定文件夹需显式 `target="bound_folder"`            | 用 `WORKFLOW_WRITE_TO_BOUND_FOLDER=false` 关掉 |
| A8 | 写文件后缀用独立的**写白名单**（`WORKFLOW_WRITE_ALLOWED_EXTENSIONS`）        | 允许产出更多格式                        |
| A9 | 代码沙箱允许一份标准库白名单（`CODE_EXEC_ALLOWED_MODULES`）               | 设 `none` 可完全禁止 import             |

## 附录 B：与现有硬约束的一致性核对

| 现有硬约束                                                         | 本设计是否遵守     | 说明                                                                                      |
| ------------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------- |
| 接口路径与响应结构与 v3 一致，前端零改动                                        | ✅           | 全部新增端点在新命名空间 `/workflow`；`/ask`、`/conversations`、`/documents` 一行不改                      |
| `/ask` 校验错误保持 400 + `answer` 字段                               | ✅           | 未触碰                                                                                     |
| SSE 事件序列固定 `intent → sources → (tool_calls) → chunk* → final` | ✅           | 工作流用独立事件命名空间与独立端点                                                                       |
| 每个请求恰好发一次 `intent` 事件                                         | ✅           | 工作流不产生 `intent` 事件                                                                      |
| 换嵌入模型必须重建向量库                                                  | ✅ 不受影响      | 工作流不使用向量库持久层                                                                            |
| State 必须可 JSON 序列化                                            | ✅           | `WorkflowState` 复用 `app/utils/documents.py` 的 dict 转换；文件正文**不进 State**，只存 `run_file_id` |
| `thread_id = f"{username}:{conversation_id}"`                 | ✅           | 工作流用 `f"{username}:run:{run_id}"`                                                       |
| 新增配置分组必须声明 `env_prefix`                                       | ✅           | `WorkflowSettings.env_prefix = "WORKFLOW_"` + 配置绑定测试                                    |
| 新增端点必须同步更新 `openapi_v3_baseline.json`                         | ✅ 已列入 §12.1 |                                                                                         |
| 同步代码路径的缓存必须用 `sync_client`                                    | ⚠️ 注意       | `execute` 图入口若走同步节点，工具层的缓存必须用 `bind_sync_client()` 绑定的 `sync_client`                    |
| 文档增删后必须清 `cache:retrieval:*`                                  | ✅ 不受影响      | 临时文件不进 Chroma，不产生检索缓存                                                                   |
