<template>
  <div class="app-shell flex h-screen overflow-hidden">
    <!-- 侧边栏：品牌 / 对话·工作 / 新对话 / 智鲸创作 都在侧栏里，顶栏不再占高度 -->
    <slot name="sidebar" />

    <section class="app-main flex-1 flex flex-col min-w-0 min-h-0">
      <slot />
    </section>
  </div>
</template>

<script setup>
// 模式切换已移入侧栏（SidebarNav），这里只负责骨架与主区
</script>

<style scoped>
.app-shell {
  font-family: var(--font-body);
  background-color: var(--color-bg);
}
</style>
