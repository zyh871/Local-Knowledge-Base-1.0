<template>
  <div class="fp-mask" @click.self="$emit('close')">
    <div class="fp-panel">
      <header class="fp-head">
        <h4 class="fp-title">{{ mode === 'folder' ? '选择文件夹作为工作区' : '从工作区文件夹选择文件' }}</h4>
        <button class="fp-close" @click="$emit('close')">✕</button>
      </header>

      <!-- 起始位置：盘符 / 主目录。让用户不必知道路径怎么写 -->
      <div class="fp-roots">
        <button
          v-for="root in roots"
          :key="root.path"
          :class="['fp-root', { on: root.path === current.path }]"
          :disabled="loading"
          @click="go(root.path)"
        >
          {{ root.name }}
        </button>
      </div>

      <!-- 路径 + 上一级 -->
      <div class="fp-path">
        <button class="fp-up" :disabled="loading || !current.parent" @click="go(current.parent)">
          ↑ 上一级
        </button>
        <span class="fp-path-text" :title="current.path">{{ current.path || '…' }}</span>
      </div>

      <div class="fp-body">
        <p v-if="loading" class="fp-state">读取中…</p>
        <p v-else-if="error" class="fp-state fp-error">{{ error }}</p>
        <p v-else-if="!dirs.length && !files.length" class="fp-state">这个文件夹是空的</p>
        <template v-else>
          <button
            v-for="item in dirs"
            :key="item.path"
            class="fp-item"
            :disabled="busy"
            @click="go(item.path)"
          >
            <span class="fp-icon">📁</span>
            <span class="fp-name">{{ item.name }}</span>
          </button>
          <button
            v-for="item in files"
            :key="item.path"
            :class="[
              'fp-item',
              'is-file',
              { pickable: mode === 'file' && canPick(item) },
              { blocked: mode === 'file' && !canPick(item) },
              { picked: mode === 'file' && isPicked(item) },
            ]"
            :title="mode === 'file' && !canPick(item) ? blockedHint(item) : ''"
            :disabled="busy || mode !== 'file' || !canPick(item)"
            @click="togglePick(item)"
          >
            <span v-if="mode === 'file' && canPick(item)" class="fp-check">
              {{ isPicked(item) ? '☑' : '☐' }}
            </span>
            <span v-else class="fp-icon">📄</span>
            <span class="fp-name">{{ item.name }}</span>
            <span v-if="mode === 'file' && !canPick(item)" class="fp-blocked">不支持</span>
            <span v-else class="fp-size">{{ formatSize(item.size) }}</span>
          </button>
          <p v-if="current.truncated" class="fp-state">条目过多，仅显示前 500 项</p>
        </template>
      </div>

      <footer class="fp-foot">
        <p class="fp-hint">
          <template v-if="mode === 'folder'">
            选中即把这个文件夹绑定为工作区，之后可直接引用里面的文件。
          </template>
          <template v-else>
            只会显示文件，不显示内容；选中后会复制一份到当前任务。
            <span v-if="blockedCount" class="fp-hint-warn">
              本目录有 {{ blockedCount }} 个文件类型不支持引用（已置灰）。
            </span>
            <br />
            可引用：{{ allowedExtensions.join(' ') || '（由服务端配置）' }}
          </template>
        </p>

        <!-- 如实说明「选多了会怎样」：上下文预算只有这么多，文件越多每个分到的越少 -->
        <p v-if="mode === 'file' && pickedCount >= 2" class="fp-budget">
          已选 {{ pickedCount }} 个：每个文件大约只能进 <strong>1/{{ pickedCount }}</strong>
          的上下文预算（合计上限约 {{ budgetChars.toLocaleString() }} 字符）。
          只挑真正需要的几个，效果通常更好。
        </p>

        <div class="fp-actions">
          <template v-if="mode === 'file'">
            <button class="mini-btn" :disabled="busy || !pickableFiles.length" @click="toggleAll">
              {{ pickedCount === pickableFiles.length ? '清空' : '全选可引用' }}
            </button>
          </template>
          <button class="mini-btn" :disabled="busy" @click="$emit('close')">取消</button>
          <button
            v-if="mode === 'folder'"
            class="mini-btn primary"
            :disabled="busy || !current.path"
            @click="$emit('select', current.path)"
          >
            {{ busy ? '绑定中…' : '选择此文件夹' }}
          </button>
          <button
            v-else
            class="mini-btn primary"
            :disabled="busy || !pickedCount"
            @click="commitPick"
          >
            {{ busy ? '加入中…' : pickedCount ? `加入选中的 ${pickedCount} 个文件` : '请先选择文件' }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { workflowApi } from '../../api/workflow'

const props = defineProps({
  /** `folder` = 选文件夹设为工作区；`file` = 从工作区文件夹选文件（可多选） */
  mode: { type: String, default: 'folder' },
  /** 起始路径（有绑定文件夹时直接从这里开始） */
  startPath: { type: String, default: '' },
  /** 后端允许引用的扩展名（形如 `.pdf`）。空数组 = 不做限制。 */
  allowedExtensions: { type: Array, default: () => [] },
  /** 上下文预算（字符）。用于如实告知「选多了每个分到多少」 */
  budgetChars: { type: Number, default: 8000 },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'pick', 'close'])

/** 已选中的文件路径（文件模式下的多选状态）。 */
const picked = ref([])

/**
 * 这个文件能不能被引用。
 *
 * ⚠️ 必须在**点击之前**就判断出来：此前选择器把 `.html` 之类不可引用的文件
 * 也照常列出来，用户点了才被拒 —— 于是合理怀疑「程序访问不了我的本地文件」。
 * 能不能用，应该是看得见的。
 */
function canPick(item) {
  if (!props.allowedExtensions.length) return true
  const dot = item.name.lastIndexOf('.')
  if (dot < 0) return false
  return props.allowedExtensions.includes(item.name.slice(dot).toLowerCase())
}

function blockedHint(item) {
  const dot = item.name.lastIndexOf('.')
  const ext = dot < 0 ? '（无扩展名）' : item.name.slice(dot)
  return `${ext} 不在可引用类型里，无法添加到任务`
}

function isPicked(item) {
  return picked.value.includes(item.path)
}

function togglePick(item) {
  if (props.mode !== 'file' || props.busy || !canPick(item)) return
  const index = picked.value.indexOf(item.path)
  if (index >= 0) picked.value.splice(index, 1)
  else picked.value.push(item.path)
}

const pickableFiles = computed(() => files.value.filter((f) => canPick(f)))
const pickedCount = computed(() => picked.value.length)

function toggleAll() {
  picked.value =
    pickedCount.value === pickableFiles.value.length ? [] : pickableFiles.value.map((f) => f.path)
}

/** 交给上层去调批量引用接口；成功后由上层关闭面板。 */
function commitPick() {
  if (!pickedCount.value) return
  emit('pick', [...picked.value])
}

const blockedCount = computed(
  () => (current.value.entries || []).filter((e) => !e.is_dir && !canPick(e)).length
)

const current = ref({ path: '', parent: null, entries: [], truncated: false })
const roots = ref([])
const loading = ref(false)
const error = ref('')

const dirs = computed(() => (current.value.entries || []).filter(e => e.is_dir))
const files = computed(() => (current.value.entries || []).filter(e => !e.is_dir))

async function go(path) {
  // ⚠️ 只挡 null/undefined，**不能挡空串**：空串是「让后端给一个起点」的约定，
  // 而组件挂载时正是用它初始化。写成 `if (!path) return` 会让选择器永远空白。
  if (path === null || path === undefined || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await workflowApi.browseFs(path)
    current.value = data
    roots.value = data.roots || roots.value
  } catch (e) {
    error.value = e.message || '读取文件夹失败'
  } finally {
    loading.value = false
  }
}

function formatSize(bytes) {
  const value = Number(bytes) || 0
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

onMounted(() => go(props.startPath || ''))
</script>

<style scoped>
.fp-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.35);
  padding: 20px;
}

.fp-panel {
  width: 100%;
  max-width: 620px;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
  overflow: hidden;
}

.fp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-border-light);
}

.fp-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.fp-close {
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
  padding: 2px 6px;
}

.fp-roots {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 16px 0;
}

.fp-root {
  padding: 3px 11px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
}

.fp-root.on {
  border-color: var(--color-primary);
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.fp-path {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
}

.fp-up {
  flex-shrink: 0;
  padding: 3px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.fp-up[disabled] {
  opacity: 0.45;
}

.fp-path-text {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  text-align: left;
}

.fp-body {
  flex: 1;
  min-height: 200px;
  overflow-y: auto;
  padding: 0 10px 10px;
}

.fp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  text-align: left;
  color: var(--color-text-primary);
  font-size: var(--text-sm);
}

.fp-item:hover:not([disabled]) {
  background-color: var(--color-surface-secondary);
}

.fp-item[disabled] {
  cursor: default;
}

.fp-item.is-file:not(.pickable) {
  opacity: 0.6;
}

/* 类型不支持：置灰 + 明确标注，而不是让用户点了才知道 */
.fp-item.blocked {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 多选：勾选框 + 选中态 */
.fp-check {
  flex-shrink: 0;
  width: 14px;
  font-size: 13px;
  color: var(--color-text-tertiary);
  text-align: center;
}

.fp-item.picked {
  background-color: var(--color-primary-lighter);
}

.fp-item.picked .fp-check {
  color: var(--color-primary);
}

.fp-item.picked .fp-name {
  color: var(--color-primary);
}

.fp-budget {
  margin: 0 0 8px;
  padding: 6px 9px;
  font-size: var(--text-xs);
  line-height: 1.7;
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
}

.fp-blocked {
  flex-shrink: 0;
  font-size: var(--text-xs);
  padding: 0 6px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.fp-hint-warn {
  color: var(--state-warning);
}

.fp-icon {
  flex-shrink: 0;
  font-size: 13px;
}

.fp-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fp-size {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.fp-state {
  padding: 16px 8px;
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
}

.fp-error {
  color: var(--state-error);
}

.fp-foot {
  padding: 10px 16px 14px;
  border-top: 1px solid var(--color-border-light);
}

.fp-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.6;
  margin-bottom: 8px;
}

.fp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
