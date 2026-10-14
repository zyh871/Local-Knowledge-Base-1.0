# 排查：模型输出的答案不全

> 排查日期：2026-10-09
> 现象：回答在句子中间戛然而止（例：以「并且」「性能优化：Cl」结尾），
> 且页面已显示「参考来源」，即该消息已被标记为**完成**。

---

## 0. 结论先行

本次实测的结论是：**后端产出多少，页面就显示多少 —— 前端渲染不是原因**。
真正的可疑点集中在模型侧，且已定位到一条**此前会静默发生的失败路径**（见 §4.3）。

| 排查项 | 实测结果 | 判定 |
|--------|---------|------|
| 前端渲染丢内容 | `final` 事件会用完整答案**覆盖**流式拼接结果 | ❌ 非原因 |
| CSS 裁剪 | `.markdown-body` 无 `max-height` / `overflow: hidden` | ❌ 非原因 |
| 模型能力（写不下去） | 同步调用 754 字、`finish_reason=stop`，正常收尾 | ⚠️ 部分场景 |
| 上下文长度限制 | 3000 字请求产出 2050 字 / 1018 token，仍 `stop` | ❌ 非原因 |
| `max_tokens` 截断 | 未配置 → 用 provider 默认；实测未触发 `length` | ⚠️ 潜在风险 |
| **流式中途断开** | 实测存在**静默返回半截**的代码路径 + 智谱风控可中断生成 | ✅ **最可疑** |

---

## 1. 四层成因与判据（核心表）

排查顺序要**从下游往上游**走：先确认前端拿到的数据是否完整，再判断后端产出是否被截断，
最后才怀疑模型。顺序反了会在无关层浪费大量时间。

| # | 成因 | 判据（决定性证据） | 如何验证 |
|---|------|-------------------|---------|
| ① | **模型能力** | `finish_reason = stop`，且 `output_tokens` 远未触顶，但内容确实结束 | 同步调用同一 prompt，看 `finish_reason`；换 `glm-4-plus` 对比 |
| ② | **`max_tokens` 截断** | `finish_reason = length`；`output_tokens` 恰好等于（或贴着）上限 | 同步调用读 `usage.output_tokens`，与配置的 `MAX_TOKENS` 比对 |
| ③ | **流式输出被截断** | 流式与同步的总字数**明显不等**；或流中 `finish_reason` 缺失/为 `length`；或日志有回退记录 | 对比同步/流式字数；看日志 `[截断]` 标记；看是否有 4xx/网络异常 |
| ④ | **前端渲染** | 后端 `final.answer` 完整，但页面显示的更短 | 浏览器 Network 面板取最后一帧 `final`，与页面文本逐字比对 |

### 快速判别口诀

```
先看 final.answer —— 短？ → 后端/模型问题（①②③）
                 —— 长？ → 前端渲染问题（④）

后端短的情况下再看 finish_reason：
    length  → ② max_tokens
    stop    → ① 模型自己写完了（那就是模型能力/风格）
    缺失    → ③ 流在中途断了（网络 / 风控 / 超时）
```

---

## 2. 逐层排查流程

### 第 1 步：确认前端是否丢内容（排除 ④）

本项目的前端逻辑是决定性的（`frontend/src/views/Chat.vue`）：

```js
} else if (data.type === 'final') {
  const finalAnswer = cleanLLMChunk(data.content.answer) || messages.value[assistantIdx].content
  messages.value[assistantIdx].content = finalAnswer   // ← 用最终答案覆盖流式拼接结果
```

因此 **页面显示的最终文本 === 后端 `final.answer`**。

验证方法（绕开浏览器，最干净）：

```bash
curl -N -X POST http://127.0.0.1:8000/ask/stream \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"question":"介绍一下 Clash Verge","conversation_id":"diag"}' > sse.txt

# 取 final 帧的 answer，与页面所见逐字比对
grep '^data: ' sse.txt | tail -1
```

若两者一致 → 排除 ④。若帧里完整、页面短 → 才去查 Markdown 渲染、CSS 截断、虚拟滚动。

本项目的补充检查（已确认无问题）：
- `renderMarkdown()` 只做 `marked.parse + DOMPurify.sanitize`，无长度截断
- 消息气泡 `.markdown-body` **没有** `max-height` / `-webkit-line-clamp` / `overflow: hidden`
  （唯一带 `max-height: 320px` 的是长文本工具面板 `.longtext-result`，与聊天消息无关）

### 第 2 步：确认后端产出是否被截断（区分 ①②③）

用 `generate` 节点的日志。**本次已在代码里补上了必要的字段**：

```
[生成完成] model=glm-4-flash finish_reason=stop chars=661 usage=n/a
[截断]     model=glm-4-flash finish_reason=length chars=812 usage=... → 回答可能不完整
```

排查命令：

```bash
grep -E "\[生成完成\]|\[截断\]" backend/logs/app.log | tail -20
```

再配合独立诊断脚本（不依赖应用，直接压模型）：

```bash
cd backend
python scripts/diagnose_truncation.py                       # 同步 + 流式对比
python scripts/diagnose_truncation.py --max-tokens 4096     # 指定上限再试
python scripts/diagnose_truncation.py --prompt "写一篇2000字的文章"
```

脚本会打印 `finish_reason`、`usage`、正文字数、结尾片段，并直接给出判定。

### 第 3 步：定位具体是哪种截断

| 观察 | 结论 | 处置 |
|------|------|------|
| `finish_reason=length` | 撞 `max_tokens` | 调大 `MAX_TOKENS`（≤ 模型上限，glm-4 系列约 4095） |
| `finish_reason=stop` 但内容短 | 模型自己收尾 | 换 `glm-4-plus`；或在 prompt 里明确要求篇幅 |
| `finish_reason` 缺失 + 有 4xx | 上游拒绝/风控 | 看错误码（智谱内容风控为 `1301`），换措辞或换模型 |
| 流式 << 同步字数 | 传输层中断 | 看日志回退记录与网络异常 |
| 流式 ≈ 同步 | 模型侧问题 | 回到上两行 |

---

## 3. 本次排查的实测数据

环境：`MODEL_BACKEND=zhipu`，`glm-4-flash`。

### 3.1 模型本身不截断

| 场景 | finish_reason | output_tokens | 正文字数 | 结尾 |
|------|--------------|--------------|---------|------|
| 同步，「至少写 8 个方面」 | `stop` | 397 | 754 | 「…确保用户获得最佳体验。」（完整句） |
| 流式，同一 prompt | `stop` | — | 746 | 「…用户可以获取帮助和反馈。」（完整句） |
| 同步，「不少于 3000 字」 | `stop` | 1018 | 2050 | 「…为经济社会发展注入新动力。」（完整句） |

三次都是**自然收尾**，没有出现 `length`。说明：默认 `max_tokens` 足以支撑
一次 2000 字左右的回答；模型也确实愿意写长（不是「只会写一句就停」）。

### 3.2 通过应用复现

问题：「介绍下Clash Verge」

```
事件序列     = ['intent', 'sources', 'chunk', 'final']
chunk 数     = 86
流式拼接字数 = 173
final 字数   = 173
两者是否一致 = True          ← 关键：前端没丢内容
```

日志侧：

```
13:44:34 收到流式问题: 介绍下Clash Verge...
13:44:47 POST .../chat/completions 200 OK
13:44:54 POST .../chat/completions 200 OK     ← 第二次是记忆抽取，非重试
```

**没有「流式生成失败」警告**，说明这一条是模型自己写短了（模型不认得这个软件，
把它当成了人名）。这属于 ① 模型能力，不属于截断。

### 3.3 一个意外发现：智谱内容风控会中断生成

把 `max_tokens` 压到 120 复现长回答时，智谱直接返回：

```json
{"contentFilter":[{"level":1,"role":"assistant"}],
 "error":{"code":"1301","message":"系统检测到输入或生成内容可能包含不安全或敏感内容…"}}
```

这也是为什么 `Clash Verge` 这类**代理软件**话题值得警惕：
风控可能在**生成中途**触发，此时流被掐断且**不会给出 `finish_reason`**，
表象正是「句子断在半截」。

---

## 4. 已做的加固

排查过程中发现三处会导致「答案不全且难以定位」的真实缺陷，均已修复。

### 4.1 `generate` 节点此前不记录截断信息

旧代码只累加文本，不关心 `finish_reason`，因此**从日志无法区分**
「模型自己写完」与「被 `max_tokens` 掐断」。现已补充：

- 逐 chunk 提取 `finish_reason`（`app/utils/text.py: finish_reason_of`）
- 记录 token 用量
- 判定为截断时打 `[截断]` WARNING 并写入响应字段 `truncated: true`
- 正常完成打 `[生成完成]` INFO

响应新增字段（向后兼容，前端可选用）：

```json
{ "answer": "...", "model": "glm-4-flash", "truncated": false }
```

### 4.2 前端逐块 `trim()` 会吃掉词间空格

`cleanLLMChunk()` 对流式分片调用 `.trim()`，导致
`"Hello"` + `" world"` 粘成 `"Helloworld"`，纯空白分片还会被 `if (cleanChunk)` 丢弃。
现已改为：**分片不 trim，只在最终答案上 trim**。

### 4.3 带 `g` 的正则用 `.test()` 有状态（噪声过滤时灵时不灵）

```js
const _INTERNAL_NOISE_RE = /(...)/g
if (_INTERNAL_NOISE_RE.test(text)) { ... }   // lastIndex 跨调用残留 → 交替返回 true/false
```

现已在使用前后重置 `lastIndex`。

### 4.4 流式失败回退时可能**静默返回半截答案**

这是最值得注意的一条。旧代码：

```python
except Exception as exc:
    try:
        answer = llm.invoke(prompt)
        text = extract_text(answer)
        if text:            # ← 若 invoke 返回空，full 保持"已流出的半截内容"
            full = text
    except Exception:
        full = "（生成失败）"
return {"answer": full}     # ← 半截答案被当作完整答案返回，无任何标记
```

当「流中断 + 同步回退返回空」时，用户看到的就是**看起来正常、实际少一半**的回答 ——
这是最难排查的状态。现已：保留半截内容的同时标记 `truncated=true` 并打 WARNING 日志。

---

## 5. 复现与验证命令速查

```bash
# 1) 独立压测模型（不依赖应用）
cd backend
python scripts/diagnose_truncation.py
python scripts/diagnose_truncation.py --max-tokens 2048

# 2) 抓 SSE 原始流，看 final.answer 与页面是否一致
curl -N -X POST http://127.0.0.1:8000/ask/stream \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"question":"...","conversation_id":"diag"}' | tail -1

# 3) 看应用日志里的生成质量标记
grep -E "\[生成完成\]|\[截断\]" backend/logs/app.log | tail -20

# 4) 通过接口指定模型对比
curl -X POST http://127.0.0.1:8000/ask \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"question":"...","model":"glm-4-plus"}'
```

---

## 6. 若问题复现，按此顺序取证

1. 记下 `trace_id`（页面报错或响应体里都有）
2. `grep <trace_id> backend/logs/app.log` —— 看是否有 `[截断]` / 流式回退 / 4xx
3. 若是 `[截断]` 且 `finish_reason=length` → 调大 `MAX_TOKENS`
4. 若 `finish_reason` 缺失 → 大概率上游中断；检查是否命中内容风控（错误码 1301）
5. 若日志显示 `[生成完成] ... finish_reason=stop` 而内容仍短 → 模型能力问题，
   换 `glm-4-plus` 或在 prompt 中明确要求篇幅
6. 若 `final.answer` 完整但页面短 → 才进入前端排查（Markdown / CSS / 虚拟滚动）
