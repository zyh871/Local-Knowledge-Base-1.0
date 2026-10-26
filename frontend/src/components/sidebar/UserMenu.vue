<template>
  <div ref="root" class="user-menu">
    <!-- 收起态只露头像和名字：侧栏底部不再挂一排功能按钮 -->
    <button
      type="button"
      class="user-bar"
      :class="{ open }"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="open = !open"
    >
      <span class="avatar">{{ initial }}</span>
      <span class="user-name">{{ displayName }}</span>
      <svg class="chevron" :class="{ up: open }" width="14" height="14" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 15 12 9 18 15" />
      </svg>
    </button>

    <!-- 上拉菜单：向上展开，而不是盖住下面的列表 -->
    <transition name="pop">
      <div v-if="open" class="pop" role="menu">
        <div class="pop-head">
          <span class="avatar lg">{{ initial }}</span>
          <span class="pop-ident">
            <span class="pop-name">{{ displayName }}</span>
            <span class="pop-sub">本地知识库</span>
          </span>
        </div>

        <nav class="pop-list">
          <button
            v-for="item in visibleItems"
            :key="item.key"
            type="button"
            role="menuitem"
            class="pop-item"
            @click="pick(item.key)"
          >
            <span class="pop-icon" v-html="item.icon" />
            <span class="pop-label">{{ item.key === 'theme' ? themeLabel : item.label }}</span>
            <span v-if="item.key === 'theme'" class="pop-hint">{{ theme === 'light' ? '浅色' : '深色' }}</span>
          </button>
        </nav>

        <div class="pop-split"></div>
        <button type="button" role="menuitem" class="pop-item danger" @click="pick('logout')">
          <span class="pop-icon" v-html="ICONS.logout" />
          <span class="pop-label">退出登录</span>
        </button>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  /** 登录名，来自登录时写入的 localStorage.username */
  name: { type: String, default: '' },
  theme: { type: String, default: 'light' },
  /**
   * 要显示的菜单项。工作/创作页没有「文档管理 / 用户画像 / 长文本」这些面板，
   * 列出来只会点了没反应 —— 所以按页面裁剪，而不是靠用户自己试。
   */
  items: {
    type: Array,
    default: () => ['documents', 'profile', 'long-text', 'models', 'theme'],
  },
})

const emit = defineEmits(['documents', 'profile', 'long-text', 'models', 'theme', 'logout'])

const root = ref(null)
const open = ref(false)

const displayName = computed(() => props.name || '未登录')
const initial = computed(() => (displayName.value || '?').trim().charAt(0).toUpperCase())
const themeLabel = computed(() => (props.theme === 'light' ? '深色模式' : '浅色模式'))

const ICONS = {
  documents: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
  profile: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  'long-text': `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  models: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/><circle cx="12" cy="12" r="3"/></svg>`,
  theme: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
  logout: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>`,
}

const ITEMS = [
  { key: 'documents', label: '文档管理', icon: ICONS.documents },
  { key: 'profile', label: '用户画像', icon: ICONS.profile },
  { key: 'long-text', label: '长文本工具', icon: ICONS['long-text'] },
  { key: 'models', label: '模型设置', icon: ICONS.models },
  { key: 'theme', label: '深色模式', icon: ICONS.theme },
]

const visibleItems = computed(() => ITEMS.filter((i) => props.items.includes(i.key)))

function pick(key) {
  open.value = false
  emit(key)
}

function onDocClick(event) {
  if (!open.value) return
  if (root.value && !root.value.contains(event.target)) open.value = false
}

function onKey(event) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<style scoped>
.user-menu {
  position: relative;
  flex-shrink: 0;
  padding: 8px 10px 12px;
  border-top: 1px solid var(--color-border-light);
}

.user-bar {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.user-bar:hover,
.user-bar.open {
  background-color: var(--color-hover);
}

.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--color-primary), #7c5cff);
}

.avatar.lg {
  width: 34px;
  height: 34px;
  font-size: 14px;
}

.user-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-align: left;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.chevron {
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  transition: transform 0.18s;
}

.chevron.up {
  transform: rotate(180deg);
}

/* ── 上拉菜单 ───────────────────────────────────────────── */
.pop {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: calc(100% - 2px);
  z-index: 40;
  padding: 6px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.14);
}

.pop-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px 10px;
  margin-bottom: 2px;
  border-bottom: 1px solid var(--color-border-light);
}

.pop-ident {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.35;
}

.pop-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.pop-sub {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.pop-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 0;
}

.pop-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s, color 0.15s;
}

.pop-item:hover {
  background-color: var(--color-hover);
  color: var(--color-text-primary);
}

.pop-icon {
  display: inline-flex;
  flex-shrink: 0;
  color: var(--color-text-tertiary);
}

.pop-item:hover .pop-icon {
  color: var(--color-text-secondary);
}

.pop-label {
  flex: 1;
  text-align: left;
}

.pop-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.pop-split {
  height: 1px;
  margin: 4px 6px;
  background-color: var(--color-border-light);
}

.pop-item.danger {
  color: var(--color-error);
}

.pop-item.danger .pop-icon {
  color: var(--color-error);
}

.pop-enter-active,
.pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
