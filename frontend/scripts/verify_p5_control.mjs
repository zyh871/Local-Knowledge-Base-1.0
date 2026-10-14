/**
 * P5 界面验证：执行中切模式 / 驳回重规划 / 终止任务。
 *
 * 只覆盖 P5 新增的控件；任务书与时间线的其余部分已由 P2/P3 脚本覆盖。
 * 用法：node scripts/verify_p5_control.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p5'
const WS_NAME = 'P5界面验证'
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
const modeOn = () => page.locator('.mode-option.on').innerText()

try {
  log('1) 登录并准备任务')
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

  // 关掉澄清以缩短链路（本轮验的是执行控制，不是澄清）
  const chip = page.locator('.composer-chip', { hasText: '需求澄清' })
  if ((await chip.getAttribute('class')).includes('on')) {
    await chip.click()
    await page.waitForTimeout(400)
  }

  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)

  await page.locator('.composer-textarea').fill(
    '写一份小团队引入代码评审的说明，分别覆盖为什么要做、具体流程、常见阻力'
  )
  await page.locator('.composer-send').click()
  await page.waitForTimeout(3000)
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 180000 })
  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)
  await page.waitForSelector('.timeline', { timeout: 15000 })
  check((await badge().innerText()).includes('已批准'), '任务书已批准', await badge().innerText())

  log('2) 执行前两步')
  for (let i = 1; i <= 2; i += 1) {
    await page.locator('.tl-actions button', { hasText: '执行下一步' }).click()
    // ⚠️ 条件必须是「达到本次期望的步数」，不能只写 >= 1 ——
    // 第一步跑完后 >= 1 立刻成立，于是第二步还没跑完就往下走了
    await page.waitForFunction(
      n => document.querySelectorAll('.tl-chip.s-done').length >= n,
      i,
      { timeout: 240000 }
    )
    await page.waitForTimeout(1500)
  }
  const doneCount = await page.locator('.tl-chip.s-done').count()
  check(doneCount >= 2, '前两步已完成', `${doneCount} 步`)
  // 记下已完成步骤的产出字数，驳回后要对比
  await page.locator('.tl-step', { has: page.locator('.tl-chip.s-done') }).first()
    .locator('button', { hasText: '查看产出' })
    .first()
    .click()
  await page.waitForTimeout(600)
  const outputBefore = await page.locator('.tl-output').first().innerText()
  check(outputBefore.length > 20, '步骤产出可查看', `${outputBefore.length} 字`)
  await page.screenshot({ path: `${OUT}/01-executed.png`, fullPage: true })

  log('3) 执行中切换模式')
  check((await modeOn()).includes('人工'), '初始为人工审查', await modeOn())
  await page.locator('.mode-option', { hasText: 'AI 自动' }).click()
  await page.waitForTimeout(2000)
  check((await modeOn()).includes('AI 自动'), '切换到 AI 自动', await modeOn())
  const sidebarMode = await page.locator('.chat-item-meta, .run-meta').first().innerText().catch(() => '')
  log(`      侧边栏（${sidebarMode.replace(/\s+/g, ' ').slice(0, 40)}）`)
  await page.locator('.mode-option', { hasText: '人工审查' }).click()
  await page.waitForTimeout(2000)
  check((await modeOn()).includes('人工'), '切回人工审查', await modeOn())

  log('4) 驳回某一步并重新规划')
  const rejectBtn = page.locator('.tl-step-actions button', { hasText: '驳回重规划' }).first()
  check((await rejectBtn.count()) === 1, '时间线提供「驳回重规划」入口')
  await rejectBtn.click()
  await page.waitForSelector('.tl-reject', { timeout: 10000 })
  const hint = await page.locator('.tl-reject-hint').innerText()
  check(hint.includes('保留'), '驳回前明确告知前序产出会保留', hint.replace(/\s+/g, '').slice(0, 30))
  await page.locator('.tl-reject-input').fill('不要分小节写，改成一张对照表：场景 / 做法 / 常见坑')
  await page.screenshot({ path: `${OUT}/02-reject-form.png`, fullPage: true })
  await page.locator('.mini-btn', { hasText: '确认驳回并重新规划' }).click()

  // 驳回后会重新生成任务书 → 状态回到「待审批」。
  // ⚠️ 不能用 `.plan-editor .step` 作为等待条件：执行已开始时任务书是紧凑态，
  // 步骤列表被折叠（步骤改由时间线呈现），那个选择器永远等不到。
  await page.waitForFunction(
    () => (document.querySelector('.status-badge')?.innerText || '').includes('待审批'),
    null,
    { timeout: 240000 }
  )
  await page.waitForTimeout(2000)
  const notice = await page.locator('.plan-notice').innerText().catch(() => '')
  check(notice.includes('保留'), '「已保留前 N 步」提示可见', notice)
  const chips = await page.locator('.tl-chip').allInnerTexts()
  const trimmed = chips.map(c => c.trim())
  check(
    trimmed.filter(c => c === '已完成').length >= 2,
    '★ 驳回后前序步骤仍是已完成（产出被保留）',
    JSON.stringify(trimmed)
  )
  // 被保留的那一步产出必须还能展开查看。
  // ⚠️ 按钮是「查看产出 / 收起产出」的切换，前面已经展开过 ——
  // 无条件再点一次会把它折叠起来，于是读到 0 字（假失败）。
  if ((await page.locator('.tl-output').count()) === 0) {
    await page
      .locator('.tl-step', { has: page.locator('.tl-chip.s-done') })
      .first()
      .locator('button', { hasText: '查看产出' })
      .first()
      .click()
    await page.waitForTimeout(600)
  }
  const outputAfter = await page.locator('.tl-output').first().innerText().catch(() => '')
  check(outputAfter.length > 20, '★ 保留步骤的产出仍可查看', `${outputAfter.length} 字`)
  await page.screenshot({ path: `${OUT}/03-replanned.png`, fullPage: true })

  log('5) 终止任务')
  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)
  const cancelBtn = page.locator('.tl-actions button', { hasText: '终止任务' })
  check((await cancelBtn.count()) === 1, '时间线提供「终止任务」入口')
  await cancelBtn.click()
  await page.waitForTimeout(3000)
  check((await badge().innerText()).includes('已取消'), '任务已终止', await badge().innerText())

  const afterCancel = (await page.locator('.tl-chip').allInnerTexts()).map(c => c.trim())
  check(
    afterCancel.filter(c => c === '已完成').length >= 2,
    '★ 终止后已完成步骤的产出仍保留',
    JSON.stringify(afterCancel)
  )
  check(
    (await page.locator('.tl-actions button', { hasText: '终止任务' }).count()) === 0,
    '终止后不再显示终止入口'
  )
  await page.screenshot({ path: `${OUT}/04-cancelled.png`, fullPage: true })

  log('6) 清理验证数据')
  const cleaned = await page.evaluate(async name => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
    const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
    const targets = (list.workspaces || []).filter(w => w.name === name)
    if (!targets.length) return 'not-found'
    let ok = 0
    for (const t of targets) {
      const res = await fetch(`/api/workflow/workspaces/${t.id}`, { method: 'DELETE', headers })
      if (res.ok) ok += 1
    }
    return ok === targets.length ? `deleted:${ok}` : `partial:${ok}/${targets.length}`
  }, WS_NAME)
  check(cleaned.startsWith('deleted'), '验证用工作区已清理', cleaned)
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  await browser.close()
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
