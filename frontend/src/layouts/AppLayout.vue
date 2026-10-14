<template>
  <div class="app-shell flex h-screen overflow-hidden">
    <!-- 模式侧边栏：内容由各模式视图通过 #sidebar 插槽提供 -->
    <slot name="sidebar" />

    <!-- 主区 -->
    <section class="app-main flex-1 flex flex-col min-w-0 min-h-0">
      <!-- 模式切换：对话 ｜ 工作 -->
      <div class="mode-switch-bar shrink-0">
        <div class="mode-switch" role="tablist" aria-label="模式切换">
          <button
            v-for="m in MODES"
            :key="m.key"
            type="button"
            role="tab"
            :aria-selected="currentMode === m.key"
            :class="['mode-switch-btn', { active: currentMode === m.key }]"
            @click="go(m)"
          >
            {{ m.label }}
          </button>
        </div>
      </div>

      <slot />
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const MODES = [
  { key: 'chat', label: '对话', path: '/chat' },
  { key: 'work', label: '工作', path: '/work' },
]

const route = useRoute()
const router = useRouter()

// 用 path 前缀判断而非 route.name，避免后续新增工作流子路由时失效
const currentMode = computed(() => (route.path.startsWith('/work') ? 'work' : 'chat'))

function go(mode) {
  if (mode.key === currentMode.value) return
  router.push(mode.path)
}
</script>

<style scoped>
.app-shell {
  font-family: var(--font-body);
  background-color: var(--color-bg);
}

.mode-switch-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px 16px 4px;
}

.mode-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  border: 1px solid var(--color-border-light);
}

.mode-switch-btn {
  min-width: 78px;
  padding: 6px 20px;
  font-size: var(--text-sm);
  line-height: 1.4;
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  transition: color 0.15s, background-color 0.15s;
}

.mode-switch-btn:hover {
  color: var(--color-text-primary);
}

.mode-switch-btn.active {
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-static);
}
</style>
