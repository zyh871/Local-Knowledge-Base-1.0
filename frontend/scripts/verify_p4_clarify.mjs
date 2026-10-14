/**
 * P4 界面验证：澄清问题卡 → 回答 → 生成任务书（含「原话 → 澄清补充 → 任务书」三段）。
 *
 * 用系统 Edge，无需下载浏览器。
 * 用法：node scripts/verify_p4_clarify.mjs [baseUrl] [outDir]
 *
 * 走真实 LLM，超时给得比较宽。
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = process.argv[3] || '.verify-shots-p4'
const WS_NAME = 'P4界面验证'
const MAX_ROUNDS = 3
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

/** 等到「有问题卡」或「任务书出现」之一。
 *
 * ⚠️ `waitForFunction(fn, arg, options)` —— 选项是**第三个**参数。
 * 把 `{timeout}` 放在第二位会被当成 `arg`，于是静默退回默认 30s 超时。
 */
const waitForStep = () =>
  page.waitForFunction(
    () =>
      !!document.querySelector('.clarify .q') ||
      !!document.querySelector('.plan-editor .step'),
    null,
    { timeout: 240000 }
  )

/**
 * 等到「任务书出现」或「问题换了一轮」。
 *
 * 不能只等 `.clarify .q` 存在 —— 点了提交之后，旧问题还挂在 DOM 上，
 * 会立刻匹配成功，于是循环误判为「没有新问题了」而提前退出。
 * 因此用问题文本做签名，只有**内容变了**才算新的一轮。
 */
const waitForNextRound = previousSignature =>
  page.waitForFunction(
    prev => {
      if (document.querySelector('.plan-editor .step')) return true
      const texts = [...document.querySelectorAll('.clarify .q-text')].map(e => e.innerText)
      return texts.length > 0 && texts.join('|') !== prev
    },
    previousSignature,
    { timeout: 240000 }
  )

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

  log('2) 输入区应有「需求澄清」开关')
  const clarifyChip = page.locator('.composer-chip', { hasText: '需求澄清' })
  check((await clarifyChip.count()) === 1, '输入区有「需求澄清」开关')
  check(
    (await clarifyChip.getAttribute('class')).includes('on'),
    '默认开启澄清'
  )

  log('3) 准备干净工作区并新建任务（目标刻意模糊）')
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)

  await page.locator('.composer-textarea').fill('帮我做一份调研')
  await page.locator('.composer-send').click()

  log('4) 澄清问题卡')
  await waitForStep()
  await page.waitForTimeout(1200)

  let rounds = 0
  for (let i = 0; i < MAX_ROUNDS; i += 1) {
    const questionCount = await page.locator('.clarify .q').count()
    if (!questionCount) break

    rounds += 1
    const hasRecommended = await page.locator('.clarify .q-option-tag', { hasText: '推荐' }).count()
    const selected = await page.locator('.clarify .q-option.selected').count()
    check(hasRecommended >= 1, `第 ${rounds} 轮每题带「推荐」标记`, `${hasRecommended} 个`)
    check(
      selected === questionCount,
      `第 ${rounds} 轮推荐答案已默认选中`,
      `${selected} / ${questionCount}`
    )

    const texts = await page.locator('.clarify .q-text').allInnerTexts()
    texts.forEach((t, idx) => log(`      Q${idx + 1}: ${t}`))

    if (rounds === 1) {
      await page.screenshot({ path: `${OUT}/01-clarify-round1.png`, fullPage: true })
    }

    // 直接提交默认（推荐答案）—— 这正是「点一下就行」的交互契约
    const signature = texts.join('|')
    await page.locator('.clarify .btn-primary').click()
    await waitForNextRound(signature)
    await page.waitForTimeout(1200)
  }

  check(rounds >= 1, '至少完成了一轮澄清', `${rounds} 轮`)
  check(rounds <= MAX_ROUNDS, '未超过轮次上限', `${rounds} 轮`)

  log('5) 澄清结束后自动生成任务书')
  await page.waitForSelector('.plan-editor .step', { timeout: 240000 })
  const stepCount = await page.locator('.plan-editor .step').count()
  check(stepCount >= 1, '任务书已生成', `${stepCount} 步`)
  await page.waitForTimeout(1000)

  log('6) 三段对比：原话 → 澄清补充 → 任务书')
  const rawCard = await page.locator('.card', { hasText: '原始输入' }).innerText()
  check(rawCard.includes('帮我做一份调研'), '① 原始输入已展示')

  const summaryItems = await page.locator('.clarify-summary li').allInnerTexts()
  check(summaryItems.length >= 1, '② 澄清补充已展示', `${summaryItems.length} 条`)
  summaryItems.forEach(t => log(`      ${t}`))
  // 每条都应体现「问题 → 用户选择」的对应关系
  check(
    summaryItems.every(t => t.includes('→')),
    '澄清补充呈现「问题 → 选择」'
  )

  const planGoal = await page.locator('.plan-editor .field-value').first().innerText()
  check(planGoal.trim().length > 1, '③ 任务书目标已展示', planGoal.slice(0, 40))
  await page.screenshot({ path: `${OUT}/02-plan-with-clarify.png`, fullPage: true })

  log('7) 刷新后澄清记录仍在')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3500)
  await page.locator('.chat-item').first().click()
  await page.waitForTimeout(2500)
  const afterReload = await page.locator('.clarify-summary li').allInnerTexts()
  check(afterReload.length === summaryItems.length, '刷新后澄清补充仍在', `${afterReload.length} 条`)

  log('8) 关闭「需求澄清」后不再提问')
  await page.locator('.composer-chip', { hasText: '需求澄清' }).click()
  await page.waitForTimeout(400)
  const lsValue = await page.evaluate(() => localStorage.getItem('workflow:clarify'))
  check(lsValue === '0', '开关状态已持久化', String(lsValue))

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

} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  await browser.close()
  // 失败时也要把控制台错误打出来 —— 否则现场只有一张截图，
  // 而前端渲染崩溃的真正原因（TypeError）恰恰只在控制台里
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
