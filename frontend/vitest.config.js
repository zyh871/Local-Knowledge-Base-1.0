import { fileURLToPath } from 'node:url'
import { defineConfig, configDefaults } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'

/**
 * Vitest 配置。
 *
 * ⚠️ 不要试图 merge `vite.config.js`：
 * 它导出的是**异步函数**（`defineConfig(async ({ mode }) => ...)`，里面要
 * await 探活来决定 API 代理目标），`mergeConfig(Promise, ...)` 会把
 * `plugins` 整个丢掉 —— 于是 `.vue` 文件无法编译，组件测试一条都跑不了
 * （报错是 "Failed to resolve import ... .vue"，看起来像文件不存在，
 * 实际是插件没挂上）。
 *
 * 所以这里独立声明测试需要的插件与别名，不依赖 vite.config.js。
 */
export default defineConfig({
  plugins: [vue(), vueJsx()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    exclude: [...configDefaults.exclude, 'e2e/**'],
    root: fileURLToPath(new URL('./', import.meta.url)),
  },
})
