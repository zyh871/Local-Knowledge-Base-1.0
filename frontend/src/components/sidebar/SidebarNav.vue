<template>
  <div class="sidebar-nav">
    <div class="side-head">
      <BrandTitle text="智鲸" />
      <button
        v-if="closable"
        class="side-close"
        type="button"
        aria-label="关闭侧边栏"
        @click="$emit('close')"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 6L6 18M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- 对话 / 工作：从顶栏移进侧栏（参考千问：模式是"场地"，创作是一个入口） -->
    <div class="side-seg" role="tablist" aria-label="切换模式">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        role="tab"
        :aria-selected="activeTab === tab.key"
        :class="['side-seg-btn', { on: activeTab === tab.key }]"
        @click="switchTab(tab)"
      >
        {{ tab.label }}
      </button>
    </div>

    <button type="button" class="side-primary" @click="$emit('new')">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 5v14M5 12h14" />
      </svg>
      {{ primaryLabel }}
    </button>

    <button
      type="button"
      :class="['side-create', { on: mode === 'create' }]"
      @click="goCreate"
    >
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
        <rect x="3" y="4" width="13" height="16" rx="2.5" />
        <path d="M6.5 15.5l3-3 2.5 2.5 2-2" />
        <path d="M19 3.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" />
      </svg>
      智鲸创作
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandTitle from '../BrandTitle.vue'

const props = defineProps({
  /** chat / work / create —— 决定主按钮文案与高亮 */
  mode: { type: String, default: 'chat' },
  /** 移动端抽屉里需要一个关闭按钮 */
  closable: { type: Boolean, default: false },
})

defineEmits(['new', 'close'])

const route = useRoute()
const router = useRouter()

const TABS = [
  { key: 'chat', label: '对话', path: '/chat' },
  { key: 'work', label: '工作', path: '/work' },
]

/**
 * 只在 /chat 与 /work 时高亮分段。
 * 创作页不属于这两个"场地"，它是 新对话 下方的一个独立入口 ——
 * 硬把它算进某个分段会让用户以为创作属于对话。
 */
const activeTab = computed(() => {
  if (route.path.startsWith('/work')) return 'work'
  if (route.path.startsWith('/create')) return ''
  return 'chat'
})

const primaryLabel = computed(
  () => ({ chat: '新对话', work: '新建任务', create: '新建创作' })[props.mode] || '新对话'
)

function switchTab(tab) {
  if (activeTab.value === tab.key) return
  router.push(tab.path)
}

function goCreate() {
  if (props.mode === 'create') return
  router.push('/create')
}
</script>

<style scoped>
.sidebar-nav {
  flex-shrink: 0;
  padding: 18px 12px 12px;
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 14px;
}

.side-close {
  color: var(--color-text-secondary);
  padding: 4px;
  border-radius: var(--radius-md);
}

.side-close:hover {
  background-color: var(--color-hover);
}

/* 分段控件：与顶栏原来的胶囊一致，但更紧凑、更贴近列表 */
.side-seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  margin: 0 2px 10px;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.side-seg-btn {
  flex: 1;
  padding: 6px 0;
  font-size: var(--text-sm);
  line-height: 1.4;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  transition: background-color 0.15s, color 0.15s;
}

.side-seg-btn:hover {
  color: var(--color-text-primary);
}

.side-seg-btn.on {
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  font-weight: 600;
  box-shadow: var(--shadow-static);
}

.side-primary {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.side-primary:hover {
  background-color: var(--color-hover);
}

/* 智鲸创作：与上面的对话列表之间留一条视觉分界，避免被当成"又一个会话" */
.side-create {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 6px;
  padding: 9px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-top: 1px solid var(--color-border-light);
  border-radius: 0 0 var(--radius-md) var(--radius-md);
  padding-top: 14px;
  transition: background-color 0.15s, color 0.15s;
}

.side-create:hover {
  background-color: var(--color-hover);
  color: var(--color-text-primary);
}

.side-create.on {
  color: var(--color-primary);
  font-weight: 600;
}
</style>
