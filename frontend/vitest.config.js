import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import viteConfig from './vite.config'

// ⚠️ vite.config.js 导出的是**函数**（`defineConfig(({ mode }) => ({...}))`），
// 而 `mergeConfig` 的第一个参数期望**对象** —— 直接传函数会让 vitest
// 在启动阶段就抛错（单测一条都跑不了）。
// 这里先把函数求值成配置对象再合并；将来若 vite.config 改回对象形式，
// 下面的三元判断也能兼容。
export default defineConfig((configEnv) =>
  mergeConfig(
    typeof viteConfig === 'function' ? viteConfig(configEnv) : viteConfig,
    defineConfig({
      test: {
        environment: 'jsdom',
        exclude: [...configDefaults.exclude, 'e2e/**'],
        root: fileURLToPath(new URL('./', import.meta.url)),
      },
    }),
  ),
)
