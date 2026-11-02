<script setup>
/**
 * 侧栏品牌标题：logo + 文字。
 *
 * 为什么单独抽组件：Chat.vue / WorkView.vue / CreateView.vue 的侧栏标题都要求
 * 「logo + 文案」，各写一遍切换逻辑迟早会漏改其中一处。
 *
 * logo 为什么不再区分深浅两版：
 *   图形本体是中蓝鲸鱼剪影，腹部与浪花是**白色镂空**——放在深色底上会透出底色，
 *   看起来「漏气」。所以统一用 `zhijing-logo-white.png`：纯白圆角底板 + 居中图形。
 *   浅色主题下白底与背景几乎同色（只剩图形），深色主题下是一枚白色小方块，
 *   两种情况下都还原了设计稿的观感，也就不再需要 html.dark 分支。
 */
import logoWhite from '../assets/zhijing-logo-white.png'

defineProps({
  /** 标题文字 */
  text: { type: String, required: true },
})
</script>

<template>
  <div class="brand-title">
    <span class="brand-logo-box">
      <img class="brand-logo" :src="logoWhite" alt="" aria-hidden="true" />
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
  /* 底板本身已是白底圆角，这里再补一圈极淡描边：
     浅色主题下白底与背景同色，没有这圈线就看不出边界 */
  border-radius: 8px;
  box-shadow: 0 0 0 1px rgba(11, 17, 24, 0.08);
}

:global(html.dark) .brand-logo {
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.14), 0 1px 3px rgba(0, 0, 0, 0.35);
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
