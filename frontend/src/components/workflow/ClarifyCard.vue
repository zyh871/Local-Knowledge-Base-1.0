<template>
  <div class="clarify">
    <div class="clarify-head">
      <h4 class="clarify-title">
        需要确认 {{ questions.length }} 件事
        <span v-if="round" class="clarify-round">第 {{ round }} / {{ maxRounds }} 轮</span>
      </h4>
      <button class="mini-btn" :disabled="busy" @click="$emit('skip')">跳过澄清，直接规划</button>
    </div>

    <p class="clarify-hint">
      每题都给了推荐答案，直接点选即可；也可以自己填。
    </p>

    <div v-for="question in questions" :key="question.id" class="q">
      <p class="q-text">{{ question.question }}</p>
      <p v-if="question.reason" class="q-reason">推荐理由：{{ question.reason }}</p>

      <div v-if="question.options.length" class="q-options">
        <button
          v-for="option in question.options"
          :key="option.value"
          :class="['q-option', { selected: picked(question.id).value === option.value }]"
          :disabled="busy"
          @click="pick(question, option.value)"
        >
          <span class="q-option-label">{{ option.label }}</span>
          <span v-if="option.value === question.recommended" class="q-option-tag">推荐</span>
        </button>
      </div>

      <div class="q-freeform">
        <!-- 注意用 `picked()`（返回答案对象）而不是 `pick()`（点击处理器，无返回值）——
             v-model 需要一个可赋值的成员表达式 -->
        <input
          v-model="picked(question.id).text"
          class="form-input"
          :disabled="busy"
          :placeholder="question.options.length ? '或者直接说你的想法（会覆盖上面的选择）' : '说说你的想法'"
        />
      </div>
    </div>

    <div class="clarify-actions">
      <span class="clarify-progress">{{ answeredCount }} / {{ questions.length }} 已作答</span>
      <button class="btn-primary" :disabled="busy" @click="submit">
        {{ busy ? '提交中…' : '提交并继续' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  round: { type: Number, default: 0 },
  maxRounds: { type: Number, default: 3 },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['submit', 'skip'])

/** 题目 id → {value, text} */
const answers = reactive({})

function ensure(id) {
  if (!answers[id]) answers[id] = { value: '', text: '' }
  return answers[id]
}

/** 读答案对象。`v-model` 与选中态判断都用它 —— 必须返回对象，不能返回标量。 */
function picked(id) {
  return ensure(id)
}

/** 题目变化时重置答案，并**默认选中推荐答案** ——
 *  这是把澄清从「审讯」变成「确认」的关键：多数情况用户直接提交就行。
 */
watch(
  () => props.questions,
  (list) => {
    for (const key of Object.keys(answers)) delete answers[key]
    for (const question of list || []) {
      answers[question.id] = { value: question.recommended || '', text: '' }
    }
  },
  { immediate: true }
)

function pick(question, value) {
  const item = ensure(question.id)
  item.value = item.value === value ? '' : value
  // 选了选项就清掉自由输入，避免两个来源打架
  if (item.value) item.text = ''
}

const answeredCount = computed(
  () => (props.questions || []).filter((q) => ensure(q.id).value || ensure(q.id).text.trim()).length
)

function submit() {
  if (props.busy) return
  const payload = (props.questions || [])
    .map((q) => {
      const item = ensure(q.id)
      return { id: q.id, value: item.value || '', text: (item.text || '').trim() }
    })
    .filter((item) => item.value || item.text)
  // 一道都没答就提交 → 等价于跳过，但没有明确意图，先挡住
  if (!payload.length) return
  emit('submit', payload)
}
</script>

<style scoped>
.clarify {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  padding: 14px 16px;
}

.clarify-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.clarify-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.clarify-round {
  margin-left: 6px;
  font-size: var(--text-xs);
  font-weight: 400;
  color: var(--color-text-tertiary);
}

.clarify-hint {
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.q {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-light);
}

.q:first-of-type {
  border-top: none;
  padding-top: 8px;
}

.q-text {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  line-height: 1.7;
}

.q-reason {
  margin-top: 3px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.q-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.q-option {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  background-color: transparent;
}

.q-option:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.q-option.selected {
  border-color: var(--color-primary);
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.q-option-tag {
  font-size: 11px;
  padding: 0 5px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.q-option.selected .q-option-tag {
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.q-freeform {
  margin-top: 8px;
}

.form-input {
  width: 100%;
  padding: 6px 9px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
}

.clarify-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 16px;
}

.clarify-progress {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
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

.btn-primary {
  padding: 6px 16px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.btn-primary[disabled],
.mini-btn[disabled] {
  opacity: 0.5;
  cursor: default;
}
</style>
