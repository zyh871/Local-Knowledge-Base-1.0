<script setup>
/**
 * 侧栏品牌标题：logo + 文字。
 *
 * 为什么单独抽组件：Chat.vue 与 WorkView.vue 的侧栏标题都要求「logo + 文案」，
 * 而 logo 需要深/浅两版 —— 两处各写一遍切换逻辑迟早会漏改其中一处。
 *
 * 为什么用 CSS 切图而不是 `<picture>` + `prefers-color-scheme`：
 * 主题开关 `useTheme` 是给 `<html>` 加 `.dark` class（**可手动覆盖系统设置**），
 * 而 `prefers-color-scheme` 只跟随操作系统。手动切到与系统相反的主题时两者
 * 不一致，logo 会选错版本。所以这里以 `html.dark` 为唯一判据。
 */
import logoDark from '../assets/zhijing-logo.png'
import logoLight from '../assets/zhijing-logo-light.png'

defineProps({
  /** 标题文字 */
  text: { type: String, required: true },
})
</script>

<template>
  <div class="brand-title">
    <span class="brand-logo-box">
      <img class="brand-logo brand-logo--dark" :src="logoDark" alt="" aria-hidden="true" />
      <img class="brand-logo brand-logo--light" :src="logoLight" alt="" aria-hidden="true" />
    </span>
    <h1 class="brand-name">{{ text }}</h1>
  </div>
</template>

<style scoped>
.brand-title {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 16px;
  min-width: 0;
}

.brand-logo-box {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  position: relative;
  display: block;
}

.brand-logo {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  /* 原图四周留白已在生成脚本里裁掉，这里只做等比缩放 */
  transform: scale(1.18);
}

/* 默认（浅色主题）显示深色 logo */
.brand-logo--light {
  display: none;
}

:global(html.dark) .brand-logo--dark {
  display: none;
}

:global(html.dark) .brand-logo--light {
  display: block;
}

.brand-name {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
  line-height: 1.25;
  /* 「智鲸 AI工作流」较长，窄侧栏下允许省略而不是换行顶乱布局 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
</style>