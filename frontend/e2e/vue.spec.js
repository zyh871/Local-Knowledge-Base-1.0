import { test, expect } from '@playwright/test'

// 原用例是**脚手架残留**：断言页面 h1 文本为 'You did it!' —— 那是
// create-vue 模板自带的文案，本项目里根本不存在这个元素，CI 必然红。
// 这里改成验证「应用确实能起来」：SPA 挂载成功 + 标题正确。
test('应用可以加载', async ({ page }) => {
  await page.goto('/')

  // 标题来自 index.html（项目里没有运行时改 title 的逻辑）
  await expect(page).toHaveTitle(/本地知识库/)

  // #app 挂载成功说明 main.js 没有在初始化阶段抛错
  await expect(page.locator('#app')).toBeAttached()
})
