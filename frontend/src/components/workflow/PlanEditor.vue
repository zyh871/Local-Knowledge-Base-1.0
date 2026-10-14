<template>
  <div class="plan-editor">
    <!-- 校验修正：必须展示 —— 静默纠正会让用户批准的其实不是模型原本的计划 -->
    <div v-if="issues.length" class="issues">
      <div class="issues-head">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        生成时做了 {{ issues.length }} 处修正
      </div>
      <!-- ★ 分两类展示：会失败的必须先看到。
           此前"缺必填参数"和"键名已归位"混在一列里同色同权，
           用户很容易整体划过 —— 而那一条意味着"批准完必然会失败"。 -->
      <div v-if="blockingIssues.length" class="issues-blocking">
        <div class="issues-blocking-head">
          下面 {{ blockingIssues.length }} 项会导致执行失败，建议先改任务书
        </div>
        <ul class="issues-list">
          <li v-for="(item, i) in blockingIssues" :key="'b' + i" class="is-blocking">
            {{ item }}
          </li>
        </ul>
      </div>
      <ul v-if="otherIssues.length" class="issues-list">
        <li v-for="(item, i) in otherIssues" :key="'o' + i">{{ item }}</li>
      </ul>
    </div>

    <!-- 任务书头部 -->
    <section class="card">
      <div class="card-head">
        <h4 class="card-title">任务书</h4>
        <div class="card-head-actions">
          <button v-if="editable && !editMode" class="mini-btn" @click="startEdit">编辑</button>
          <template v-if="editable && editMode">
            <button class="mini-btn" @click="cancelEdit">取消</button>
            <button class="mini-btn primary" :disabled="busy || !!argsError" @click="submit">
              保存
            </button>
          </template>
        </div>
      </div>

      <div v-if="!editMode" class="field-grid">
        <div class="field">
          <span class="field-label">目标</span>
          <p class="field-value">{{ draft.goal || '—' }}</p>
        </div>
        <div class="field">
          <span class="field-label">受众 / 用途</span>
          <p class="field-value">{{ draft.audience || '—' }}</p>
        </div>
        <div class="field">
          <span class="field-label">约束</span>
          <p class="field-value">{{ listText(draft.constraints) }}</p>
        </div>
        <div class="field">
          <span class="field-label">假设</span>
          <p class="field-value">{{ listText(draft.assumptions) }}</p>
        </div>
      </div>

      <div v-else class="field-grid">
        <label class="field">
          <span class="field-label">目标</span>
          <input v-model="draft.goal" class="form-input" placeholder="最终要产出什么" />
        </label>
        <label class="field">
          <span class="field-label">受众 / 用途</span>
          <input v-model="draft.audience" class="form-input" placeholder="这份产物给谁看" />
        </label>
        <label class="field">
          <span class="field-label">约束（一行一条）</span>
          <textarea v-model="constraintsText" class="form-input" rows="2"></textarea>
        </label>
        <label class="field">
          <span class="field-label">假设（一行一条）</span>
          <textarea v-model="assumptionsText" class="form-input" rows="2"></textarea>
        </label>
      </div>

      <p v-if="rejectComment" class="reject-note">上次驳回意见：{{ rejectComment }}</p>
    </section>

    <p v-if="compact" class="card-hint compact-note">
      任务书已批准，步骤的执行情况见下方时间线。
    </p>

    <!-- 步骤 -->
    <section v-if="!compact" class="card">
      <div class="card-head">
        <h4 class="card-title">步骤（{{ draft.steps.length }}）</h4>
        <button v-if="editMode" class="mini-btn" @click="addStep">添加步骤</button>
      </div>

      <div class="steps">
        <div v-for="(step, index) in draft.steps" :key="index" class="step">
          <div class="step-rail">
            <span class="step-idx">{{ index + 1 }}</span>
            <span :class="['step-kind', `is-${step.kind}`]">{{ kindLabel(step.kind) }}</span>
          </div>

          <div class="step-body">
            <template v-if="!editMode">
              <p class="step-title">{{ step.title }}</p>
              <p class="step-detail">{{ actionText(step) }}</p>
              <span v-if="step.requires_approval" class="step-badge">需人工确认</span>
            </template>

            <template v-else>
              <div class="step-row">
                <select v-model="step.kind" class="form-input kind-select" @change="onKindChange(step)">
                  <option value="tool">工具调用</option>
                  <option value="llm">生成</option>
                  <option value="synthesis">汇总</option>
                </select>
                <input v-model="step.title" class="form-input flex-1" placeholder="这一步要做什么" />
              </div>

              <div class="step-row">
                <template v-if="step.kind === 'tool'">
                  <select v-model="step.action.tool" class="form-input tool-select">
                    <option v-for="t in availableTools" :key="t" :value="t">{{ t }}</option>
                  </select>
                  <input
                    v-model="step.argsText"
                    class="form-input flex-1 mono"
                    :class="{ invalid: !!argsErrorOf(index) }"
                    placeholder='参数 JSON，例如 {"query": "关键词"}'
                  />
                </template>
                <input
                  v-else
                  v-model="step.action.prompt_ref"
                  class="form-input flex-1"
                  placeholder="这一步的意图"
                />
              </div>

              <div class="step-row between">
                <label class="check">
                  <input
                    type="checkbox"
                    v-model="step.requires_approval"
                    :disabled="isForced(step)"
                  />
                  需要人工确认
                  <span v-if="isForced(step)" class="check-note">（该工具强制要求）</span>
                </label>
                <div class="step-actions">
                  <button class="mini-icon-btn" :disabled="index === 0" @click="move(index, -1)" title="上移">↑</button>
                  <button
                    class="mini-icon-btn"
                    :disabled="index === draft.steps.length - 1"
                    @click="move(index, 1)"
                    title="下移"
                  >↓</button>
                  <button class="mini-icon-btn danger" @click="removeStep(index)" title="删除">×</button>
                </div>
              </div>

              <p v-if="argsErrorOf(index)" class="field-error">{{ argsErrorOf(index) }}</p>
            </template>
          </div>
        </div>
      </div>
    </section>

    <!-- 审批 -->
    <section v-if="canDecide" class="card approval">
      <h4 class="card-title">{{ editMode ? '保存后即可审批' : '审批' }}</h4>
      <p class="card-hint">
        {{ approvalHint }}
      </p>
      <textarea
        v-if="!approved"
        v-model="rejectCommentDraft"
        class="form-input"
        rows="2"
        placeholder="驳回意见（可选）：哪一步需要改，怎么改"
      ></textarea>
      <div class="approval-actions">
        <button class="btn-secondary" :disabled="busy || editMode" @click="emitReject">
          {{ approved ? '驳回以修改' : '驳回并修改' }}
        </button>
        <button
          v-if="!approved"
          class="btn-primary"
          :disabled="busy || editMode"
          @click="$emit('approve')"
        >
          批准并锁定计划
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  plan: { type: Object, required: true },
  issues: { type: Array, default: () => [] },
  status: { type: String, default: 'draft' },
  busy: { type: Boolean, default: false },
  rejectComment: { type: String, default: '' },
  availableTools: { type: Array, default: () => [] },
  requireApprovalTools: { type: Array, default: () => [] },
  //: 紧凑模式：只显示目标/受众/约束/假设，不重复渲染步骤列表 ——
  //: 批准后步骤的实际执行情况由 StepTimeline 呈现，同一份步骤出现两次会让人困惑
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['save', 'approve', 'reject'])

const KIND_LABELS = { tool: '工具', llm: '生成', synthesis: '汇总', unplanned: '计划外' }

const editMode = ref(false)
const constraintsText = ref('')
const assumptionsText = ref('')
const rejectCommentDraft = ref('')

const clone = (value) =>
  JSON.parse(JSON.stringify(value === undefined ? null : value))

const draft = reactive({
  goal: '',
  audience: '',
  constraints: [],
  assumptions: [],
  file_refs: [],
  steps: [],
})

function argsErrorOf(index) {
  const step = draft.steps[index]
  if (!step || step.kind !== 'tool') return ''
  const text = (step.argsText || '').trim()
  if (!text) return ''
  try {
    const parsed = JSON.parse(text)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return '参数必须是 JSON 对象'
    }
    return ''
  } catch (e) {
    return '参数不是合法 JSON'
  }
}

const argsError = computed(() =>
  draft.steps.some((_, index) => !!argsErrorOf(index)) ? '有步骤的参数 JSON 不合法' : ''
)

/**
 * 把校验问题分成「会失败」与「已修正」两类。
 *
 * 后端在缺失必填参数时会写「…，执行到这里会失败」。这条和
 * 「参数 expression 已按 code 处理」混在一起同色展示时，用户会整体划过；
 * 而前者意味着"批准完跑到那一步必然失败"——它必须单独、显眼。
 */
const blockingIssues = computed(() =>
  props.issues.filter((item) => /会失败|无法执行|不可用/.test(String(item)))
)
const otherIssues = computed(() =>
  props.issues.filter((item) => !blockingIssues.value.includes(item))
)

// 已批准之后计划就锁定了 —— 想改必须先驳回。
// 否则会出现「界面上的任务书」与「将要执行的任务书」悄悄不一致，
// 而用户以为自己批准的就是眼前这份。
const editable = computed(
  () =>
    !['approved', 'running', 'done', 'cancelled'].includes(props.status) &&
    !props.busy
)
const canDecide = computed(
  () =>
    ['awaiting_approval', 'planning', 'failed', 'paused', 'approved'].includes(
      props.status
    )
)
const approved = computed(() => props.status === 'approved')

const approvalHint = computed(() => {
  if (approved.value) {
    return '任务书已批准并锁定。若要修改，请先驳回回到待规划。逐步执行将在 P3 接入。'
  }
  return '批准后计划会被锁定；能执行代码或写文件的步骤，无论人工还是自动模式都会单独再确认一次。'
})

function loadPlan(plan) {
  const source = plan || {}
  draft.goal = source.goal || ''
  draft.audience = source.audience || ''
  draft.constraints = clone(source.constraints || [])
  draft.assumptions = clone(source.assumptions || [])
  draft.file_refs = clone(source.file_refs || [])
  draft.steps = (source.steps || []).map((step) => {
    const action = clone(step.action || {})
    return {
      kind: step.kind || 'llm',
      title: step.title || '',
      action,
      requires_approval: !!step.requires_approval,
      argsText: step.kind === 'tool' ? JSON.stringify(action.args || {}, null, 0) : '',
    }
  })
  constraintsText.value = draft.constraints.join('\n')
  assumptionsText.value = draft.assumptions.join('\n')
}

// 任务书变化 → 重载草稿并退出编辑态。
// 保存成功后父组件会把新任务书传回来，靠这里收敛，不需要额外的成功回调。
watch(
  () => props.plan,
  (plan) => {
    loadPlan(plan)
    editMode.value = false
  },
  { immediate: true, deep: true }
)

function listText(items) {
  return items && items.length ? items.join('；') : '—'
}
function kindLabel(kind) {
  return KIND_LABELS[kind] || kind
}
function isForced(step) {
  return step.kind === 'tool' && props.requireApprovalTools.includes(step.action.tool)
}
function actionText(step) {
  if (step.kind === 'tool') {
    return `${step.action.tool}(${JSON.stringify(step.action.args || {})})`
  }
  return step.action.prompt_ref || step.title
}

function startEdit() {
  loadPlan(props.plan)
  editMode.value = true
}
function cancelEdit() {
  loadPlan(props.plan)
  editMode.value = false
}

function onKindChange(step) {
  if (step.kind === 'tool') {
    step.action = {
      tool: step.action.tool || props.availableTools[0] || '',
      args: {},
    }
    step.argsText = '{}'
  } else {
    step.action = { prompt_ref: step.action.prompt_ref || step.title }
    step.argsText = ''
  }
}

function addStep() {
  draft.steps.push({
    kind: 'llm',
    title: '',
    action: { prompt_ref: '' },
    requires_approval: false,
    argsText: '',
  })
}
function removeStep(index) {
  draft.steps.splice(index, 1)
}
function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= draft.steps.length) return
  const [item] = draft.steps.splice(index, 1)
  draft.steps.splice(target, 0, item)
}

function submit() {
  if (argsError.value) return
  const steps = draft.steps.map((step, index) => {
    const action = clone(step.action || {})
    if (step.kind === 'tool') {
      const text = (step.argsText || '').trim()
      action.args = text ? JSON.parse(text) : {}
    }
    return {
      idx: index + 1,
      kind: step.kind,
      title: step.title,
      action,
      requires_approval: !!step.requires_approval,
    }
  })

  emit('save', {
    goal: draft.goal,
    audience: draft.audience,
    constraints: constraintsText.value
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean),
    assumptions: assumptionsText.value
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean),
    file_refs: draft.file_refs,
    steps,
  })
}

function emitReject() {
  emit('reject', rejectCommentDraft.value.trim())
}
</script>

<style scoped>
.plan-editor {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.issues {
  padding: 10px 14px;
  border: 1px solid var(--state-warning);
  border-radius: var(--radius-lg);
  background-color: var(--state-warning-bg);
}

.issues-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--state-warning);
}

.issues-list {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  line-height: 1.8;
}

.issues-blocking {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--state-error);
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
}

.issues-blocking-head {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--state-error);
}

.issues-list li.is-blocking {
  color: var(--state-error);
}

.card {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  padding: 14px 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.card-head-actions {
  display: flex;
  gap: 6px;
}

.card-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.card-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.7;
  margin-bottom: 8px;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px 18px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.field-label {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.field-value {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  line-height: 1.6;
  word-break: break-word;
}

.field-error {
  font-size: var(--text-xs);
  color: var(--state-error);
  margin-top: 4px;
}

.reject-note {
  margin-top: 10px;
  padding: 6px 10px;
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

/* ===== 步骤 ===== */
.steps {
  display: flex;
  flex-direction: column;
}

.step {
  display: flex;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid var(--color-border-light);
}

.step:first-child {
  border-top: none;
}

.step-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  width: 52px;
  flex-shrink: 0;
}

.step-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: var(--text-xs);
  border-radius: 50%;
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
}

.step-kind {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  white-space: nowrap;
}

.step-kind.is-tool {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}
.step-kind.is-synthesis {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.step-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.step-title {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  line-height: 1.6;
}

.step-detail {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  word-break: break-all;
}

.step-badge {
  align-self: flex-start;
  font-size: var(--text-xs);
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-row.between {
  justify-content: space-between;
}

.step-actions {
  display: flex;
  gap: 2px;
}

.kind-select {
  width: 96px;
  flex-shrink: 0;
}

.tool-select {
  width: 150px;
  flex-shrink: 0;
}

.flex-1 {
  flex: 1;
  min-width: 0;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.check-note {
  color: var(--state-warning);
}

/* ===== 表单与按钮 ===== */
.form-input {
  width: 100%;
  padding: 6px 9px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  resize: vertical;
}

.form-input.invalid {
  border-color: var(--state-error);
}

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

.mini-btn[disabled] {
  opacity: 0.5;
  cursor: default;
}

.mini-icon-btn {
  padding: 3px;
  border-radius: var(--radius-sm);
  color: var(--color-text-tertiary);
}

.mini-icon-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mini-icon-btn.danger:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.mini-icon-btn[disabled] {
  opacity: 0.35;
  cursor: default;
}

.approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.btn-primary,
.btn-secondary {
  padding: 6px 14px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
}

.btn-primary {
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.btn-secondary {
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
}

.btn-secondary:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.btn-primary[disabled],
.btn-secondary[disabled] {
  opacity: 0.5;
  cursor: default;
}

.approval .card-title {
  margin-bottom: 6px;
}
</style>
