/**
 * P9 界面验证：模型设置页 —— 新增 / 测试连接 / 列表 / 启用停用 / 编辑 / 删除，
 * 以及自定义模型出现在模型选择器里。
 *
 * 刻意**不真的连一个外部服务**：全部用「测试必然失败」的地址来验证流程与错误提示，
 * 只有一次用真实地址验证「连接成功」的绿色状态。
 *
 * 用法：node scripts/verify_p9_models.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p9'
const NAME = `界面验证-${Date.now().toString().slice(-6)}`
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

/**
 * 清理验证用的端点。
 *
 * 用 Node 直接登录+删除，而不是绕浏览器：浏览器里那次清理依赖页面已登录，
 * 一旦页面还在 /login 就静默什么都不删 —— 残留会在下一次运行里让断言错位。
 */
async function apiCleanup(prefix) {
  const base = new URL(BASE).origin
  try {
    const tokenRes = await fetch(`${base}/api/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username: 'admin', password: 'admin123' }),
    })
    if (!tokenRes.ok) return `login-${tokenRes.status}`
    const { access_token: token } = await tokenRes.json()
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`${base}/api/model-endpoints`, { headers })).json()
    const targets = (list.endpoints || []).filter(e => e.name.startsWith(prefix))
    let ok = 0
    for (const t of targets) {
      const res = await fetch(`${base}/api/model-endpoints/${t.id}`, {
        method: 'DELETE',
        headers,
      })
      if (res.ok) ok += 1
    }
    return `${ok}/${targets.length}`
  } catch (e) {
    return 'error:' + e.message
  }
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } })
const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

try {
  log('1) 登录并进入模型设置')
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

  // 侧边栏入口
  const navBtn = page.locator('.sidebar-nav-btn', { hasText: '模型设置' })
  check((await navBtn.count()) >= 1, '侧边栏有「模型设置」入口')
  await navBtn.first().click()
  await page.waitForURL('**/models', { timeout: 15000 })
  await page.waitForSelector('.ms-page', { timeout: 15000 })
  await page.waitForTimeout(1500)
  check(true, '进入 /models')
  await page.screenshot({ path: `${OUT}/01-settings-empty.png`, fullPage: true })

  log('2) 表单校验与按钮可用性')
  await page.locator('.btn-primary', { hasText: '添加模型' }).click()
  await page.waitForSelector('.ms-form', { timeout: 8000 })
  check(true, '打开新增表单')

  const field = n => page.locator('.ms-field').nth(n).locator('input')
  const testBtn = page.locator('.mini-btn', { hasText: '测试连接' })
  const saveBtn = page.locator('.ms-form-actions .btn-primary', { hasText: '保存' })

  // 什么都还没填时，两个按钮都该是禁用的 —— 不可行的操作不该能点
  check(await testBtn.isDisabled(), '空表单时「测试连接」不可点')
  check(await saveBtn.isDisabled(), '空表单时「保存」不可点')

  await field(0).fill(NAME)
  await field(3).fill('deepseek-chat')
  await field(2).fill('sk-deliberately-wrong-key')
  check(await testBtn.isDisabled(), '缺接口地址时仍不可点')

  // 地址缺协议头 → 当场拦下，而不是等一次网络往返
  await field(1).fill('api.deepseek.com/v1')
  check(!(await testBtn.isDisabled()), '填齐后可点「测试连接」')
  await testBtn.click()
  await page.waitForTimeout(800)
  const invalidMsg = await page.locator('.ms-test.bad').innerText().catch(() => '')
  check(invalidMsg.includes('http'), '弱校验：地址缺协议头被拦下', invalidMsg)
  await page.screenshot({ path: `${OUT}/02-validation.png`, fullPage: true })

  log('3) 测试连接（真实地址 + 故意错的密钥 → 必须失败且提示可操作）')
  await field(1).fill('https://open.bigmodel.cn/api/paas/v4')
  await testBtn.click()
  await page.waitForFunction(
    () => {
      const el = document.querySelector('.ms-test')
      return el && el.innerText.trim().length > 0
    },
    null,
    { timeout: 120000 }
  )
  await page.waitForTimeout(500)
  const badMsg = await page.locator('.ms-test.bad').innerText().catch(() => '')
  check(badMsg.length > 0, '错误密钥的测试被标为失败', badMsg.replace(/\s+/g, ' ').slice(0, 70))
  check(
    /401|密钥|认证/.test(badMsg),
    '提示是可操作的中文（而不是把上游原文甩出来）',
    badMsg.replace(/\s+/g, ' ').slice(0, 70)
  )

  log('4) 保存端点（错误密钥也允许保存，可用与否交给用户判断）')
  await saveBtn.click()
  // ⚠️ 要等**自己那一行**出现，不能等 `.ms-row`：
  // 列表里已经有别人的端点时，后者会立刻满足，断言会在刷新完成前跑掉。
  // 也不要按名字找第 1 行定位 —— 直接以名字作为定位条件。
  const myRow = page.locator('.ms-row', { hasText: NAME }).first()
  await myRow.waitFor({ timeout: 30000 })
  check((await myRow.count()) === 1, '端点已加入列表', NAME)
  const rowText = await myRow.innerText()
  check(rowText.includes('密钥'), '列表显示掩码密钥')
  check(!rowText.includes('deliberately'), '★ 页面里看不到完整密钥')
  await page.screenshot({ path: `${OUT}/03-saved.png`, fullPage: true })

  log('5) 自定义模型出现在模型选择器里')
  await page.goto(`${BASE}/work`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.composer-box', { timeout: 20000 })
  await page.waitForTimeout(2000)
  const chip = page.locator('[data-role="model-chip"]').first()
  await chip.click()
  await page.waitForTimeout(1200)
  const menuText = await page.locator('.menu').first().innerText()
  check(menuText.includes(NAME), '模型菜单里出现自定义模型', menuText.replace(/\s+/g, ' ').slice(0, 80))
  check(menuText.includes('自定义'), '带「自定义」标记')
  check(menuText.includes('管理模型'), '带「管理模型…」入口')
  await page.screenshot({ path: `${OUT}/04-model-menu.png`, fullPage: true })
  const manage = page.locator('.menu-manage')
  await manage.click()
  await page.waitForURL('**/models', { timeout: 15000 })
  check(true, '从模型菜单可直达设置页')

  log('6) 启用 / 停用')
  await page.waitForSelector('.ms-row', { timeout: 15000 })
  const toggle = page.locator('.ms-row', { hasText: NAME }).locator('.mini-btn', { hasText: '停用' })
  check((await toggle.count()) === 1, '提供停用入口')
  await toggle.click()
  await page.waitForTimeout(2500)
  check(
    (await page.locator('.ms-row', { hasText: NAME }).first().innerText()).includes('已停用'),
    '停用后状态更新'
  )
  await page.locator('.ms-row', { hasText: NAME }).locator('.mini-btn', { hasText: '启用' }).click()
  await page.waitForTimeout(2500)
  check(
    (await page.locator('.ms-row', { hasText: NAME }).first().innerText()).includes('已启用'),
    '可重新启用'
  )

  log('7) 编辑（密钥留空 = 不修改）')
  await page.locator('.ms-row', { hasText: NAME }).locator('.mini-btn', { hasText: '编辑' }).click()
  await page.waitForSelector('.ms-form', { timeout: 8000 })
  const keyPlaceholder = await page.locator('.ms-field').nth(2).locator('input').getAttribute('placeholder')
  check(keyPlaceholder.includes('留空'), '密钥框提示「留空则不修改」', keyPlaceholder.slice(0, 30))
  await page.locator('.ms-field').nth(0).locator('input').fill(NAME + '改名')
  await page.locator('.ms-form-actions .btn-primary', { hasText: '保存' }).click()
  await page.waitForTimeout(2500)
  check(
    (await page.locator('.ms-row', { hasText: NAME }).first().innerText()).includes('改名'),
    '改名生效'
  )

  log('8) 两步删除')
  const del = page.locator('.ms-row', { hasText: NAME }).locator('.mini-btn', { hasText: '删除' })
  await del.click()
  await page.waitForTimeout(600)
  const confirmBtn = page.locator('.ms-row', { hasText: NAME }).locator('.mini-btn', { hasText: '确认删除' })
  check((await confirmBtn.count()) === 1, '第一次点击变成「确认删除？」')
  await confirmBtn.click()
  await page.waitForTimeout(2500)
  check(
    !(await page.locator('.ms-page').innerText()).includes(NAME),
    '★ 删除后列表里不再有它'
  )
  await page.screenshot({ path: `${OUT}/05-deleted.png`, fullPage: true })

  log('9) 收尾检查：没有残留')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
  const finalText = await page.locator('.ms-page').innerText()
  check(!finalText.includes(NAME), '刷新后确认无残留')
  const helper = await page.locator('.ms-notes').innerText()
  check(helper.includes('对话生成'), '页面说明了「只用于对话生成」')
  check(helper.includes('末 4 位'), '页面说明了密钥只显示末 4 位')
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  // 兜底清理：脚本中途失败时也要把验证用的端点删掉。
  // 走 Node 直连接口，不依赖浏览器页面已登录 —— 否则页面停在 /login 时
  // 会静默什么都不删，残留让下一次运行的断言错位。
  const cleaned = await apiCleanup('界面验证-')
  if (cleaned !== '0/0') log(`兜底清理：删除 ${cleaned} 个验证端点`)
  await browser.close()
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
