/**
 * P1 验证：工作模式的工作区 / 任务 / 临时文件引用。
 *
 * 覆盖的是「接口测不出、只有真浏览器能验」的部分：
 *   * 输入区下拉选择器的真实交互
 *   * 「先挂文件再发送」→ 一次操作串起两次请求（建任务 + 传文件）
 *   * localStorage 记忆（工作空间 / 执行权限 / 模型）
 *   * 刷新后恢复
 *
 * 用系统 Edge，无需下载浏览器。
 * 用法：node scripts/verify_p1_workflow.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = process.argv[3] || '.verify-shots-p1'
const WS_NAME = 'P1验证工作区'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
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
  log('1) 登录并进入工作模式')
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  if (await page.locator('input[type="password"]').count()) {
    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.locator('form button, .login-card button, button').first().click()
    await page.waitForTimeout(3500)
  }
  await page.goto(`${BASE}/work`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.composer-box', { timeout: 25000 })
  await page.waitForTimeout(1500)
  check(true, '/work 可达')

  // 首次进入应自动建出默认工作区，否则用户第一步就卡在空下拉里
  const wsBtn = () => page.locator('.composer-meta .composer-meta-btn').first()
  const wsText = (await wsBtn().innerText()).trim()
  check(wsText.length > 0 && !wsText.includes('选择工作空间'), '工作空间已自动就绪', wsText)

  log('2) 新建工作区')
  await wsBtn().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  const input = page.locator('.menu-wide .menu-input').last()
  await input.fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)
  check((await wsBtn().innerText()).includes(WS_NAME), '工作区已切换为新建的', await wsBtn().innerText())

  log('3) 输入任务 + 挂文件')
  await page.locator('.composer-textarea').fill('对比三家大模型的推理成本并给出结论')
  await page.locator('.composer-toolbar input[type=file]').setInputFiles({
    name: 'p1-note.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('这是 P1 验证用的临时上下文文件，不应进入文档管理。', 'utf8'),
  })
  await page.waitForTimeout(600)
  const chips = await page.locator('.pending-chip').allInnerTexts()
  check(chips.length === 1, '待上传文件 chip 已出现', JSON.stringify(chips))
  await page.screenshot({ path: `${OUT}/01-composer-ready.png` })

  log('4) 切换到「AI 自动执行」')
  await page.locator('.composer-meta .composer-meta-btn').nth(1).click()
  await page.waitForSelector('.menu-up', { timeout: 5000 })
  await page.locator('.menu-up .menu-item', { hasText: 'AI 自动执行' }).click()
  await page.waitForTimeout(600)
  const lsMode = await page.evaluate(() => localStorage.getItem('workflow:mode'))
  check(lsMode === 'auto', '执行权限已持久化', String(lsMode))

  log('5) 发送 → 创建任务 + 上传文件')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)

  const runTitle = await page.locator('.chat-header-title').innerText()
  check(runTitle.includes('对比三家'), '任务已创建并选中', runTitle)

  const sidebarItems = await page.locator('.chat-item').count()
  check(sidebarItems >= 1, '任务出现在侧边栏列表', `${sidebarItems} 条`)

  const fileNames = await page.locator('.run-detail .file-name').allInnerTexts()
  check(fileNames.includes('p1-note.txt'), '文件已挂到任务上', JSON.stringify(fileNames))
  const parsed = await page.locator('.run-detail .file-warn').count()
  check(parsed === 0, '文件解析成功（无「未解析」标记）')

  const meta = await page.locator('.run-detail .card-meta').innerText()
  check(meta.includes('AI 自动执行'), '任务记录的执行模式正确', meta.replace(/\s+/g, ' '))
  check(meta.includes(WS_NAME), '任务归属工作区正确')

  await page.screenshot({ path: `${OUT}/02-run-created.png` })

  log('6) 刷新后状态保持')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.composer-box', { timeout: 20000 })
  await page.waitForTimeout(2500)
  const afterReload = (await wsBtn().innerText()).trim()
  check(afterReload.includes(WS_NAME), '工作空间选择被记住', afterReload)
  const modeAfter = (await page.locator('.composer-meta .composer-meta-btn').nth(1).innerText()).trim()
  check(modeAfter.includes('AI 自动执行'), '执行权限被记住', modeAfter)
  const itemsAfter = await page.locator('.chat-item').count()
  check(itemsAfter >= 1, '任务列表持久化', `${itemsAfter} 条`)
  await page.screenshot({ path: `${OUT}/03-after-reload.png` })

  log('7) 清理验证数据')
  const cleaned = await page.evaluate(async name => {
    const token = localStorage.getItem('token')
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
    const target = (list.workspaces || []).find(w => w.name === name)
    if (!target) return 'not-found'
    const res = await fetch(`/api/workflow/workspaces/${target.id}`, {
      method: 'DELETE',
      headers,
    })
    return res.ok ? 'deleted' : `failed:${res.status}`
  }, WS_NAME)
  check(cleaned === 'deleted', '验证用工作区已清理', cleaned)

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
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
