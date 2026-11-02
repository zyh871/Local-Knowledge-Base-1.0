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

    <div class="side-links">
      <button
        type="button"
        :class="['side-link', { on: route.path.startsWith('/teams') }]"
        @click="router.push('/teams')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
        团队空间
      </button>
      <button
        type="button"
        :class="['side-link', { on: route.path.startsWith('/community') }]"
        @click="router.push('/community')"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
          <circle cx="12" cy="12" r="3" />
          <circle cx="5" cy="6" r="2.2" />
          <circle cx="19" cy="6" r="2.2" />
          <circle cx="5" cy="18" r="2.2" />
          <circle cx="19" cy="18" r="2.2" />
          <path d="M7 7.5l3 2.5M17 7.5l-3 2.5M7 16.5l3-2.5M17 16.5l-3-2.5" />
        </svg>
        开源社区
      </button>
    </div>
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
 * 创作页、团队空间、开源社区都不属于这两个"场地"，它们是独立入口 ——
 * 硬把它们算进某个分段会让用户以为这些页面属于对话。
 *
 * 判据必须是「命中 /chat」而不是「兜底 chat」：后者会让新加的
 * /teams、/community 也高亮"对话"。
 */
const activeTab = computed(() => {
  if (route.path.startsWith('/work')) return 'work'
  if (route.path.startsWith('/chat')) return 'chat'
  return ''
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

/* 入口组：智鲸创作 / 团队空间 / 开源社区
   三者都是「去另一个地方」，与上面的会话列表性质不同，
   所以共用一套样式，不各自发明一套按钮。 */
.side-links {
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin-top: 2px;
}

.side-link {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s, color 0.15s;
}

.side-link:hover {
  background-color: var(--color-hover);
  color: var(--color-text-primary);
}

.side-link.on {
  color: var(--color-primary);
  font-weight: 600;
  background-color: var(--color-primary-lighter);
}

.side-link svg {
  flex-shrink: 0;
}
</style>
