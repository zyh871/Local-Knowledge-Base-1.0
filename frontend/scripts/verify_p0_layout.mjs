/**
 * P0 外壳验证：共享 AppLayout + 对话｜工作 分段控件 + /work 路由骨架。
 *
 * 用系统 Edge，无需下载浏览器。
 * 用法：node scripts/verify_p0_layout.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = process.argv[3] || '.verify-shots-p0'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  ' + extra : ''}`)
  if (!ok) fails.push(label)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1360, height: 900 } })

const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

try {
  log('1) 打开', BASE)
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)

  if (await page.locator('input[type="password"]').count()) {
    log('2) 登录')
    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.locator('form button, .login-card button, button').first().click()
    await page.waitForTimeout(3500)
  }

  // ---------- 对话模式 ----------
  await page.waitForSelector('.input-area', { timeout: 25000 })
  check(page.url().includes('/chat'), '登录后进入 /chat', page.url())

  await page.waitForSelector('.app-shell', { timeout: 10000 })
  check(true, 'AppLayout .app-shell 已渲染')

  await page.waitForSelector('.mode-switch', { timeout: 10000 })
  const modeBtns = await page.locator('.mode-switch-btn').allInnerTexts()
  check(
    modeBtns.length === 2 && modeBtns[0].trim() === '对话' && modeBtns[1].trim() === '工作',
    '分段控件有两个选项',
    JSON.stringify(modeBtns)
  )

  const activeChat = await page.locator('.mode-switch-btn.active').innerText()
  check(activeChat.trim() === '对话', '对话模式处于选中态', JSON.stringify(activeChat))

  // 对话侧边栏（会话列表）仍在
  check(await page.locator('.sidebar .sidebar-title').count() > 0, '对话侧边栏标题仍在')
  const sidebarTitle = await page.locator('.sidebar .sidebar-title').first().innerText()
  check(sidebarTitle.includes('知识库问答'), '侧边栏标题内容正确', JSON.stringify(sidebarTitle))
  check(await page.locator('.sidebar .chat-list').count() > 0, '会话列表容器仍在')
  check(await page.locator('.messages-area').count() > 0, '消息区仍在')
  check(await page.locator('.input-area textarea').count() > 0, '输入框仍在')

  await page.screenshot({ path: `${OUT}/01-chat-mode.png` })
  log('   截图 01-chat-mode.png')

  // ---------- 切到工作模式 ----------
  log('3) 点击「工作」')
  await page.locator('.mode-switch-btn', { hasText: '工作' }).click()
  await page.waitForTimeout(1200)
  check(page.url().includes('/work'), '路由切到 /work', page.url())

  const activeWork = await page.locator('.mode-switch-btn.active').innerText()
  check(activeWork.trim() === '工作', '工作模式处于选中态', JSON.stringify(activeWork))

  const workSidebarTitle = await page.locator('.sidebar .sidebar-title').first().innerText()
  check(workSidebarTitle.includes('AI 工作流'), '工作侧边栏标题已切换', JSON.stringify(workSidebarTitle))
  check(await page.locator('.roadmap-item').count() >= 6, '路线图渲染')
  check(await page.locator('.composer-box').count() > 0, '输入区预览已渲染')
  const metaBtns = await page.locator('.composer-meta-btn').allInnerTexts()
  check(
    metaBtns.some(t => t.includes('选择工作空间')) && metaBtns.some(t => t.includes('执行权限')),
    '工作空间/执行权限选择器已就位',
    JSON.stringify(metaBtns.map(t => t.trim()))
  )
  await page.screenshot({ path: `${OUT}/02-work-mode.png` })
  log('   截图 02-work-mode.png')

  // ---------- 深色主题（跨模式共享）----------
  log('4) 切换深色主题')
  await page.locator('.sidebar-nav-btn', { hasText: '深色模式' }).click()
  await page.waitForTimeout(900)
  const isDark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  check(isDark, 'documentElement 已加 dark 类')
  await page.screenshot({ path: `${OUT}/03-work-dark.png` })
  log('   截图 03-work-dark.png')

  // ---------- 回对话模式，主题应保持 ----------
  log('5) 切回「对话」')
  await page.locator('.mode-switch-btn', { hasText: '对话' }).click()
  await page.waitForTimeout(1200)
  check(page.url().includes('/chat'), '路由切回 /chat', page.url())
  const stillDark = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  check(stillDark, '主题跨模式保持（共享单例生效）')
  const backTitle = await page.locator('.sidebar .sidebar-title').first().innerText()
  check(backTitle.includes('知识库问答'), '对话侧边栏已恢复', JSON.stringify(backTitle))
  await page.screenshot({ path: `${OUT}/04-chat-dark.png` })
  log('   截图 04-chat-dark.png')

  // 主题复位，避免影响用户后续使用
  await page.locator('.sidebar-nav-btn', { hasText: '浅色模式' }).click()
  await page.waitForTimeout(600)

  // ---------- 直接访问 /work（刷新场景）----------
  log('6) 直接访问 /work')
  await page.goto(`${BASE}/work`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.app-shell', { timeout: 15000 })
  await page.waitForTimeout(800)
  check(await page.locator('.roadmap').count() > 0, '/work 可直接访问（无登录跳转）')

  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png` })
  } catch {}
} finally {
  await browser.close()
}

log('')
log(fails.length === 0 ? `全部通过` : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
