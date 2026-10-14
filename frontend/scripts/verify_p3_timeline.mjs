/**
 * P3 界面验证：批准任务书 → 步骤时间线 → 执行 → 产出与来源链接。
 *
 * 用系统 Edge，无需下载浏览器。
 * 用法：node scripts/verify_p3_timeline.mjs [baseUrl] [outDir]
 *
 * 刻意选一个「纯 LLM」的任务（不含联网搜索），让单步耗时稳定在几秒级，
 * 验证本身的运行时间才可控。
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = process.argv[3] || '.verify-shots-p3'
const WS_NAME = 'P3界面验证'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } })

const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

const badge = () => page.locator('.work-header .status-badge')

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

  log('2) 准备干净工作区并新建任务')
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)

  await page.locator('.composer-textarea').fill('把「支持多轮对话」这个需求整理成 3 条开发任务，每条附一句验收标准')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)

  log('3) 生成任务书并批准')
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 120000 })
  const stepCount = await page.locator('.plan-editor .step').count()
  check(stepCount >= 1, '任务书步骤已渲染', `${stepCount} 步`)

  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)
  check((await badge().innerText()).includes('已批准'), '任务书已批准', await badge().innerText())

  log('4) 步骤时间线出现')
  await page.waitForSelector('.timeline', { timeout: 15000 })
  const tlSteps = await page.locator('.tl-step').count()
  check(tlSteps === stepCount, '时间线步骤数与任务书一致', `${tlSteps}`)
  const firstChip = await page.locator('.tl-chip').first().innerText()
  check(firstChip.trim() === '待执行', '初始状态为待执行', firstChip)
  await page.screenshot({ path: `${OUT}/01-timeline-ready.png`, fullPage: true })

  log('5) 自动执行剩余步骤')
  const runAllBtn = page.locator('.tl-actions button', { hasText: '自动执行剩余' })
  check((await runAllBtn.count()) === 1, '「自动执行剩余」按钮可用')
  await runAllBtn.click()

  // 等到所有步骤都有终态
  await page
    .waitForFunction(
      () => {
        const chips = [...document.querySelectorAll('.tl-chip')]
        return (
          chips.length > 0 &&
          chips.every(c => ['已完成', '已跳过', '失败'].includes(c.innerText.trim()))
        )
      },
      null,
      { timeout: 300000 }
    )
    .catch(() => {})
  await page.waitForTimeout(2000)

  const chips = await page.locator('.tl-chip').allInnerTexts()
  const trimmed = chips.map(c => c.trim())
  check(trimmed.every(c => c === '已完成'), '所有步骤都已完成', JSON.stringify(trimmed))
  check((await badge().innerText()).includes('已完成'), '任务状态为已完成', await badge().innerText())

  const summaryText = await page.locator('.thinking-text').last().innerText().catch(() => '')
  check(summaryText.trim().length > 10, '给出了最终结论', `${summaryText.trim().length} 字`)
  await page.screenshot({ path: `${OUT}/02-completed.png`, fullPage: true })

  log('6) 查看步骤产出')
  const firstView = page.locator('.tl-step', { hasText: '1' }).locator('button', { hasText: '查看产出' })
  await firstView.first().click()
  await page.waitForTimeout(600)
  const outputVisible = await page.locator('.tl-output').count()
  check(outputVisible > 0, '步骤产出可展开查看')
  await page.screenshot({ path: `${OUT}/03-step-output.png`, fullPage: true })

  log('7) 已完成的步骤不能重复执行')
  const executeBtns = await page.locator('.tl-step-actions button', { hasText: '执行' }).count()
  check(executeBtns === 0, '已完成后不再显示执行按钮')

  log('8) 刷新后状态保持')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3500)
  await page.locator('.chat-item').first().click()
  await page.waitForTimeout(2500)
  check(
    (await badge().innerText()).includes('已完成'),
    '刷新后完成状态保持',
    await badge().innerText()
  )
  check((await page.locator('.tl-step').count()) >= 1, '刷新后时间线仍在')

  log('9) 清理验证数据')
  const cleaned = await page.evaluate(async name => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
    const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
    // ⚠️ 必须删除**全部**同名工作区：脚本中途崩过会留下残留，
    // 而每轮又用同一个名字新建 —— 用 find() 只删一个会越积越多。
    const targets = (list.workspaces || []).filter(w => w.name === name)
    if (!targets.length) return 'not-found'
    let ok = 0
    for (const target of targets) {
      const res = await fetch(`/api/workflow/workspaces/${target.id}`, {
        method: 'DELETE',
        headers,
      })
      if (res.ok) ok += 1
    }
    return ok === targets.length ? `deleted:${ok}` : `partial:${ok}/${targets.length}`
  }, WS_NAME)
  check(cleaned.startsWith('deleted'), '验证用工作区已清理', cleaned)

  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  await browser.close()
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
