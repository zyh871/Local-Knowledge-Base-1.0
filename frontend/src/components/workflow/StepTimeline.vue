<template>
  <div class="timeline">
    <!-- 执行工具栏 -->
    <div class="tl-toolbar">
      <div class="tl-summary">
        <span class="tl-count">{{ doneCount }} / {{ steps.length }} 步完成</span>
        <span v-if="statusText" class="tl-status">{{ statusText }}</span>
      </div>
      <div class="tl-actions">
        <!-- 执行中可切换模式（设计 §7.3）：用户常在看到前两步质量后决定放权 -->
        <div class="mode-switch" title="切换后立即对尚未开始的步骤生效">
          <button
            :class="['mode-option', { on: mode === 'manual' }]"
            :disabled="busy || finished"
            @click="$emit('set-mode', 'manual')"
          >
            人工审查
          </button>
          <button
            :class="['mode-option', { on: mode === 'auto' }]"
            :disabled="busy || finished"
            @click="$emit('set-mode', 'auto')"
          >
            AI 自动
          </button>
        </div>

        <button
          class="mini-btn primary"
          :disabled="busy || nextRunnableIdx === null"
          @click="$emit('next-step')"
        >
          {{ busy ? '执行中…' : '执行下一步' }}
        </button>
        <button
          class="mini-btn"
          :disabled="busy || nextRunnableIdx === null"
          title="连续执行直到需要人工确认或全部完成"
          @click="$emit('run-all')"
        >
          自动执行剩余
        </button>
        <button v-if="busy" class="mini-btn" @click="$emit('stop')">停止</button>
        <button
          v-else-if="!finished"
          class="mini-btn danger"
          title="终止后不再推进，已完成的产出会保留"
          @click="$emit('cancel')"
        >
          终止任务
        </button>
      </div>
    </div>

    <p v-if="showHint" class="tl-hint">
      {{ blockedReason }}
    </p>

    <!-- 时间线 -->
    <div class="tl-list">
      <div
        v-for="step in steps"
        :key="step.idx"
        :class="['tl-step', `is-${step.status}`, { active: step.idx === activeIdx }]"
      >
        <div class="tl-rail">
          <span class="tl-idx">{{ step.idx }}</span>
          <span class="tl-line"></span>
        </div>

        <div class="tl-body">
          <div class="tl-head">
            <span :class="['tl-kind', `k-${step.kind}`]">{{ kindLabel(step.kind) }}</span>
            <span class="tl-title">{{ step.title }}</span>
            <!-- 已完成的步骤也显示耗时：用户看到「这步花了 1 分 58 秒」才会明白
                 慢是产出量大，而不是卡住了 -->
            <span v-if="doneDuration(step)" class="tl-elapsed">{{ doneDuration(step) }}</span>
            <!-- 单步 token：一眼看出钱花在哪一步，而不是只知道总量 -->
            <span v-if="tokenLabel(step)" class="tl-tokens" :title="tokenTitle(step)">
              {{ tokenLabel(step) }}
            </span>
            <span :class="['tl-chip', `s-${step.status}`]">
              {{ statusLabel(step.status) }}
              <template v-if="step.status === 'running' && runningElapsed(step)">
                {{ runningElapsed(step) }}
              </template>
            </span>
          </div>

          <p class="tl-action">{{ actionText(step) }}</p>

          <p v-if="step.needs_gate && isOpen(step)" class="tl-gate">
            这一步需要人工确认后才能执行
            <template v-if="forcedTool(step)">（{{
              forcedTool(step)
            }} 属于强制审批工具）</template>
          </p>

          <!-- 产出文件：这一步真的写了文件，要能一眼看到并下载。
               只说「已写入 xx 字节」等于没生成 —— 用户要的是文件本身。 -->
          <p v-if="writtenOf(step)" class="tl-written">
            <span class="tl-written-tag">产出文件</span>
            <button
              class="tl-written-link"
              :title="'下载 ' + writtenOf(step).filename"
              @click="$emit('download-artifact', writtenOf(step).id)"
            >
              {{ writtenOf(step).filename }}
            </button>
            <span class="tl-written-meta">{{ formatBytes(writtenOf(step).size_bytes) }}</span>
            <span v-if="writtenOf(step).target === 'bound_folder'" class="tl-written-meta">
              已写入工作区绑定文件夹
            </span>
            <span v-if="writtenOf(step).renamed" class="tl-warn">同名文件已存在，已自动改名</span>
          </p>

          <!-- 参数归位：模型写错了键名，系统替它改对了 —— 必须说出来 -->
          <ul v-if="(step.output?.arg_notes || []).length" class="tl-notes">
            <li v-for="(note, i) in step.output.arg_notes" :key="i">{{ note }}</li>
          </ul>

          <!-- 执行中的实时输出 -->
          <pre
            v-if="step.status === 'running' && step.idx === activeIdx"
            class="tl-live"
          >{{ liveText }}<span class="caret"></span></pre>

          <!-- 产出 -->
          <template v-if="step.status === 'done' && step.output">
            <div class="tl-output-head">
              <button class="mini-btn" @click="toggle(step.idx)">
                {{ expanded[step.idx] ? '收起产出' : '查看产出' }}
              </button>
              <span v-if="step.output.truncated" class="tl-warn">输出可能被截断</span>
              <span v-if="(step.output.stripped_links || []).length" class="tl-warn">
                已剔除 {{ step.output.stripped_links.length }} 个无效链接
              </span>
            </div>
            <div
              v-if="expanded[step.idx]"
              class="tl-output"
              v-html="renderOutput(step)"
            ></div>

            <div v-if="sourcesOf(step).length" class="tl-sources">
              <span class="tl-sources-label">来源</span>
              <a
                v-for="src in sourcesOf(step)"
                :key="src.href"
                :class="['tl-source', { dead: src.reachable === false }]"
                :href="src.href"
                target="_blank"
                rel="noopener noreferrer"
                :title="src.title || src.href"
              >
                <span class="tl-source-domain">{{ domainOf(src.href) }}</span>
                <span class="tl-source-title">{{ src.title || src.href }}</span>
                <span v-if="src.reachable === false" class="tl-source-flag">可能失效</span>
              </a>
            </div>
          </template>

          <p v-if="step.error" class="tl-error">{{ step.error }}</p>
          <p v-if="step.retry_count > 0" class="tl-retry">已重试 {{ step.retry_count }} 次</p>

          <!-- 驳回：需要一段意见，因此就地展开输入 -->
          <div v-if="rejectingIdx === step.idx" class="tl-reject">
            <p class="tl-reject-hint">
              驳回后，<strong>这一步及之后的步骤</strong>会重新规划；
              前面已完成步骤的产出会保留。
            </p>
            <textarea
              v-model="rejectComment"
              class="tl-reject-input"
              rows="2"
              placeholder="要改什么？（例如：切入角度不对，应该按成本分档对比）"
            ></textarea>
            <div class="tl-reject-actions">
              <button class="mini-btn" @click="cancelReject">取消</button>
              <button class="mini-btn primary" @click="confirmReject(step.idx)">
                确认驳回并重新规划
              </button>
            </div>
          </div>

          <!-- 步骤操作 -->
          <div v-else-if="actionsFor(step).length" class="tl-step-actions">
            <button
              v-for="action in actionsFor(step)"
              :key="action.key"
              :class="['mini-btn', { primary: action.primary, danger: action.danger }]"
              :disabled="busy"
              @click="onAction(action.key, step.idx)"
            >
              {{ action.label }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  status: { type: String, default: 'draft' },
  mode: { type: String, default: 'manual' },
  busy: { type: Boolean, default: false },
  activeIdx: { type: Number, default: 0 },
  liveText: { type: String, default: '' },
  nextRunnableIdx: { type: Number, default: null },
  //: 每步的 token 用量（服务端已归一化）：{ [idx]: {input,output,total} }
  usageByStep: { type: Object, default: () => ({}) },
})

// ⚠️ 必须赋值给变量：脚本里要调用 `emit(...)`，
// 只写 `defineEmits([...])` 会让脚本侧报 `emit is not defined`，
// 于是**所有步骤操作按钮静默失效**（模板里的 `$emit` 仍然可用，所以更难发现）。
const emit = defineEmits([
  'execute',
  'approve',
  'retry',
  'skip',
  'reject',
  'next-step',
  'run-all',
  'stop',
  'cancel',
  'set-mode',
  'download-artifact',
])

const KIND_LABELS = { tool: '工具', llm: '生成', synthesis: '汇总', unplanned: '计划外' }
const STATUS_LABELS = {
  pending: '待执行',
  awaiting_approval: '待确认',
  approved: '已批准',
  running: '执行中',
  done: '已完成',
  failed: '失败',
  skipped: '已跳过',
  rejected: '已驳回',
}

const expanded = reactive({})
//: 就地展开的「驳回」输入框所对应的步骤序号（0 = 未展开）
const rejectingIdx = ref(0)
const rejectComment = ref('')

const finished = computed(() => ['done', 'cancelled'].includes(props.status))

const doneCount = computed(
  () => props.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length
)

const statusText = computed(() => {
  if (props.status === 'done') return '全部完成'
  if (props.status === 'paused') return '已暂停（有步骤失败）'
  if (props.status === 'running') return '执行中'
  return ''
})

/** 为什么现在没有可执行的步骤 —— 必须说清楚，否则用户不知道在等什么。 */
const blockedReason = computed(() => {
  if (props.status === 'done') return ''
  if (props.status === 'planned' || props.status === 'planning') return ''
  const waiting = props.steps.find((s) => s.status === 'awaiting_approval')
  if (waiting) {
    return `第 ${waiting.idx} 步等待人工确认。确认后即可继续。`
  }
  const failed = props.steps.find((s) => s.status === 'failed')
  if (failed) return `第 ${failed.idx} 步执行失败：请重试或跳过。`
  const running = props.steps.find((s) => s.status === 'running')
  if (running) {
    // 带上已用时：没有它，一个正在正常生成长内容的步骤
    // 与一个已经卡死的步骤在界面上完全一样
    const elapsed = runningElapsed(running)
    return `第 ${running.idx} 步正在执行中…${elapsed ? `（已运行 ${elapsed}）` : ''}`
  }
  return ''
})

/** 有任何步骤正在执行。 */
const hasRunningStep = computed(() => props.steps.some((s) => s.status === 'running'))

/**
 * 何时展示提示条。
 *
 * 原本的条件是 `nextRunnableIdx === null`（「现在没有可执行的步骤」），
 * 但它会漏掉**执行中**这种情况：前端在执行期间不刷新，
 * 手上的 `nextRunnableIdx` 还是旧值（指向正在跑的那一步），于是最该
 * 看到「正在执行中…（已运行 1:23）」的时候反而什么都不显示。
 * 执行中的提示优先级高于「没有可执行步骤」。
 */
const showHint = computed(
  () => !!blockedReason.value && (props.nextRunnableIdx === null || hasRunningStep.value)
)

function kindLabel(kind) {
  return KIND_LABELS[kind] || kind
}

// ── 用时显示 ──────────────────────────────────────────────────────
//
// 目的：让「正常但缓慢」与「已经卡死」在界面上**可见地不同**。
// 实测一个 synthesis 步可以生成 3 万字符、耗时 2 分钟 —— 没有秒表时，
// 用户只能看到一个静止的「执行中…」，自然会认为程序死了。

/** 每秒推进一次的时间源。只在**有步骤正在执行**时更新，避免无谓重渲染。 */
const nowSec = ref(Date.now() / 1000)
let ticker = null

const hasRunning = computed(() => props.steps.some((s) => s.status === 'running'))

function syncTicker() {
  if (hasRunning.value) {
    nowSec.value = Date.now() / 1000
    if (ticker === null) {
      ticker = setInterval(() => {
        nowSec.value = Date.now() / 1000
      }, 1000)
    }
  } else if (ticker !== null) {
    clearInterval(ticker)
    ticker = null
  }
}

watch(hasRunning, syncTicker, { immediate: true })
onUnmounted(() => {
  if (ticker !== null) {
    clearInterval(ticker)
    ticker = null
  }
})

/** 给人看的时长：8 秒 / 1 分 23 秒 / 1 小时 2 分。 */
function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`
}

/** 运行中步骤的已用时（时钟形式，放在状态徽标里要够短）。 */
function runningElapsed(step) {
  if (!step?.started_at) return ''
  const seconds = Math.max(0, Math.floor(nowSec.value - step.started_at))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

/** 已完成/失败步骤的实际耗时（前后都有时间戳才算）。 */
function doneDuration(step) {
  if (!step?.started_at || !step?.ended_at) return ''
  return formatDuration(step.ended_at - step.started_at)
}

/** 该步的 token 用量（服务端归一化后下发，前端不重复实现一遍）。 */
function stepUsage(step) {
  return props.usageByStep?.[step?.idx] || null
}

/** 这一步产出的文件（服务端登记成产物后回填到 step.output.artifact）。 */
function writtenOf(step) {
  const artifact = step?.output?.artifact
  return artifact && artifact.id ? artifact : null
}

function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** 单步 token 标签；太小的数不显示（没有信息量）。 */
function tokenLabel(step) {
  const usage = stepUsage(step)
  const total = Number(usage?.total_tokens) || 0
  if (total < 100) return ''
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k tok` : `${total} tok`
}

function tokenTitle(step) {
  const usage = stepUsage(step)
  if (!usage) return ''
  return `输入 ${usage.input_tokens || 0} / 输出 ${usage.output_tokens || 0} tokens`
}
function statusLabel(status) {
  return STATUS_LABELS[status] || status
}
function isOpen(step) {
  return ['pending', 'awaiting_approval'].includes(step.status)
}
function forcedTool(step) {
  return step.kind === 'tool' && step.needs_gate && !step.requires_approval
    ? step.action?.tool || ''
    : ''
}
function toggle(idx) {
  expanded[idx] = !expanded[idx]
}
function sourcesOf(step) {
  const list = step.output?.sources
  return Array.isArray(list) ? list.filter((s) => s && s.href) : []
}

/**
 * 全流程的来源池（按步骤顺序去重编号）。
 *
 * 与后端 `executor.collect_sources()` 用**同一条规则**：按 idx 顺序遍历各步的
 * `output.sources`，按 href 首次出现去重，从 1 开始编号。模型的 `[1]` 引用
 * 就是按这个编号写的，所以前端必须能复现出同一套编号，否则锚点会指错。
 *
 * 规则一旦变化，两边都要改 —— 这是前后端共享的一处**约定**，不是实现细节。
 */
const pooledSources = computed(() => {
  const seen = new Set()
  const out = []
  for (const step of [...props.steps].sort((a, b) => a.idx - b.idx)) {
    for (const src of sourcesOf(step)) {
      if (seen.has(src.href)) continue
      seen.add(src.href)
      out.push(src)
    }
  }
  return out
})

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 把产出正文里的 `[n]` 渲染成可点击的来源锚点。
 *
 * ⚠️ 先整段转义、再只插入我们自己构造的 `<a>` —— 绝不能把模型输出直接塞进
 * `v-html`（那是一个 XSS 入口）。编号找不到对应来源时原样保留文字。
 */
function renderOutput(step) {
  const text = escapeHtml(step.output?.summary || '')
  const pool = pooledSources.value
  return text.replace(/\[(\d{1,2})\]/g, (match, n) => {
    const src = pool[Number(n) - 1]
    if (!src || !src.href) return match
    const href = escapeHtml(src.href)
    const title = escapeHtml(src.title || src.href)
    const dead = src.reachable === false ? ' cite-dead' : ''
    return `<a class="cite${dead}" href="${href}" target="_blank" rel="noopener noreferrer" title="${title}">[${n}]</a>`
  })
}

function domainOf(href) {
  try {
    return new URL(href).hostname.replace(/^www\./, '')
  } catch (e) {
    return href
  }
}
function actionText(step) {
  const action = step.action || {}
  if (step.kind === 'tool') {
    return `${action.tool}(${JSON.stringify(action.args || {})})`
  }
  return action.prompt_ref || step.title
}

/**
 * 每个状态可做的操作。
 *
 * 「执行」只给服务端下发的 `nextRunnableIdx` —— 顺序与门禁策略都在服务端，
 * 前端不自己判断（否则会给出一个必然撞 409 的按钮）。
 *
 * 「驳回」在任何未终结状态下都提供：用户看到某一步产出不如意时，
 * 第一反应就是改这一步，而不是先去整体驳回。
 */
function actionsFor(step) {
  const list = []
  const canRun = step.idx === props.nextRunnableIdx && !props.busy
  const rejectable = ['pending', 'awaiting_approval', 'approved', 'failed'].includes(
    step.status
  )

  if (step.status === 'pending' || step.status === 'awaiting_approval') {
    if (step.needs_gate) {
      list.push({ key: 'approve', label: '批准此步', primary: true })
    }
    if (canRun) {
      list.push({ key: 'execute', label: '执行', primary: !step.needs_gate })
    }
    list.push({ key: 'skip', label: '跳过' })
  } else if (step.status === 'approved') {
    if (canRun) list.push({ key: 'execute', label: '执行', primary: true })
    list.push({ key: 'skip', label: '跳过' })
  } else if (step.status === 'failed') {
    list.push({ key: 'retry', label: '重试', primary: true })
    list.push({ key: 'skip', label: '跳过' })
  }
  if (rejectable) {
    list.push({ key: 'reject', label: '驳回重规划' })
  }
  return list
}

function onAction(key, idx) {
  if (key === 'reject') {
    rejectingIdx.value = idx
    rejectComment.value = ''
    return
  }
  emit(key, idx)
}

function cancelReject() {
  rejectingIdx.value = 0
  rejectComment.value = ''
}

function confirmReject(idx) {
  emit('reject', idx, rejectComment.value.trim())
  rejectingIdx.value = 0
  rejectComment.value = ''
}
</script>

<style scoped>
.timeline {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  padding: 14px 16px;
}

.tl-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.tl-summary {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.tl-count {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.tl-status {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.tl-actions {
  display: flex;
  gap: 6px;
}

.tl-hint {
  margin-bottom: 10px;
  padding: 6px 10px;
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

/* ===== 步骤 ===== */
.tl-list {
  display: flex;
  flex-direction: column;
}

.tl-step {
  display: flex;
  gap: 12px;
}

.tl-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 24px;
  flex-shrink: 0;
  padding-top: 4px;
}

.tl-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: var(--text-xs);
  border-radius: 50%;
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.tl-line {
  flex: 1;
  width: 1px;
  min-height: 12px;
  background-color: var(--color-border-light);
}

.tl-step:last-child .tl-line {
  display: none;
}

.tl-step.is-done .tl-idx {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}
.tl-step.is-running .tl-idx {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}
.tl-step.is-failed .tl-idx {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}
.tl-step.is-awaiting_approval .tl-idx {
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.tl-body {
  flex: 1;
  min-width: 0;
  padding: 4px 0 16px;
}

.tl-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tl-kind {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  flex-shrink: 0;
}
.tl-kind.k-tool {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}
.tl-kind.k-synthesis {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.tl-title {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  word-break: break-word;
}

/* 已完成步骤的耗时：让人一眼看出「慢」是因为产出量大 */
.tl-elapsed {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

/* 单步 token 用量：一眼看出钱花在哪一步 */
.tl-tokens {
  flex-shrink: 0;
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

.tl-written {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0 0;
  padding: 6px 10px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  font-size: var(--text-sm);
}

.tl-written-tag {
  font-size: var(--text-xs);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background-color: var(--color-primary-lighter);
  color: var(--color-primary-dark);
}

.tl-written-link {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  color: var(--color-primary);
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.tl-written-meta {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.tl-notes {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.tl-chip {
  font-size: var(--text-xs);
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  flex-shrink: 0;
  white-space: nowrap;
}
.tl-chip.s-done {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}
.tl-chip.s-running {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}
.tl-chip.s-failed {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}
.tl-chip.s-awaiting_approval {
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}
.tl-chip.s-approved {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}

.tl-action {
  margin-top: 4px;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  word-break: break-all;
}

.tl-gate {
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--state-warning);
}

.tl-live {
  margin-top: 8px;
  padding: 8px 10px;
  max-height: 220px;
  overflow-y: auto;
  font-family: var(--font-body);
  font-size: var(--text-sm);
  line-height: 1.75;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.caret {
  display: inline-block;
  width: 7px;
  height: 13px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background-color: var(--color-primary);
}

.tl-output-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.tl-warn {
  font-size: var(--text-xs);
  color: var(--state-warning);
}

.tl-output {
  margin-top: 6px;
  padding: 8px 10px;
  max-height: 300px;
  overflow-y: auto;
  font-family: var(--font-body);
  font-size: var(--text-sm);
  line-height: 1.75;
  color: var(--color-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

/* 正文里的 [n] 引用锚点 */
.tl-output :deep(.cite) {
  display: inline-block;
  padding: 0 4px;
  margin: 0 1px;
  font-size: 11px;
  border-radius: var(--radius-sm);
  background-color: var(--state-info-bg);
  color: var(--state-info);
  text-decoration: none;
  vertical-align: baseline;
}

.tl-output :deep(.cite:hover) {
  text-decoration: underline;
}

.tl-output :deep(.cite-dead) {
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.tl-sources {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tl-sources-label {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.tl-source {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--color-primary);
  text-decoration: none;
  min-width: 0;
}

.tl-source:hover .tl-source-title {
  text-decoration: underline;
}

.tl-source-domain {
  flex-shrink: 0;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.tl-source-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-source.dead .tl-source-title {
  text-decoration: line-through;
  opacity: 0.7;
}

.tl-source-flag {
  flex-shrink: 0;
  font-size: 11px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.tl-error {
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--state-error);
  line-height: 1.7;
}

.tl-retry {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.tl-step-actions {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}

/* ===== 就地驳回 ===== */
.tl-reject {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--state-warning);
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
}

.tl-reject-hint {
  font-size: var(--text-xs);
  color: var(--state-warning);
  line-height: 1.7;
  margin-bottom: 6px;
}

.tl-reject-input {
  width: 100%;
  padding: 6px 9px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  resize: vertical;
}

.tl-reject-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 8px;
}

/* ===== 模式切换 ===== */
.mode-switch {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.mode-option {
  padding: 2px 9px;
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
}

.mode-option.on {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mode-option[disabled] {
  opacity: 0.5;
  cursor: default;
}

/* ===== 按钮 ===== */
.mini-btn {
  padding: 3px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.mini-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mini-btn.primary {
  border-color: var(--color-primary);
  color: var(--color-primary-contrast);
  background-color: var(--color-primary);
}

.mini-btn.danger {
  color: var(--state-error);
}

.mini-btn[disabled] {
  opacity: 0.5;
  cursor: default;
}
</style>
