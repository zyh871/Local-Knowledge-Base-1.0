/**
 * P2 界面验证：任务书生成 → 思考流 → 编辑 → 驳回 → 批准。
 *
 * 用系统 Edge，无需下载浏览器。
 * 用法：node scripts/verify_p2_plan.mjs [baseUrl] [outDir]
 *
 * 会走真实 LLM，因此超时给得比较宽。
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5173'
const OUT = process.argv[3] || '.verify-shots-p2'
const WS_NAME = 'P2界面验证'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } })

const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

// 顶部状态徽标
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

  log('2) 准备一个干净的工作区')
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)
  check(
    (await page.locator('.composer-meta .composer-meta-btn').first().innerText()).includes(WS_NAME),
    '工作区已就绪'
  )

  log('3) 新建任务')
  await page.locator('.composer-textarea').fill('把这段产品需求整理成一份包含验收标准的开发任务清单')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)
  check((await badge().innerText()).includes('草稿'), '任务处于草稿态', await badge().innerText())

  log('4) 生成任务书（真实 LLM，请稍候）')
  await page.locator('button', { hasText: '生成任务书' }).first().click()

  // 思考流：卡片会立刻出现（planning 期间），但要等到真有文字才算渲染成功。
  // 首字延迟可能 1-3 秒，所以这里轮询等待内容而非固定 sleep。
  await page.waitForSelector('.thinking-card, .thinking-text', { timeout: 30000 })
  await page
    .waitForFunction(
      () => {
        const el = document.querySelector('.thinking-text')
        return el && el.innerText.trim().length > 0
      },
      null,
      { timeout: 60000 }
    )
    .catch(() => {})
  await page.waitForTimeout(500)
  const thinkingEarly = (await page.locator('.thinking-text').innerText().catch(() => '')) || ''
  check(thinkingEarly.trim().length > 0, '思考流已在渲染', `${thinkingEarly.trim().length} 字`)
  check(!thinkingEarly.includes('{'), '思考流里没有 JSON 花括号')
  await page.screenshot({ path: `${OUT}/01-thinking.png` })

  // 任务书渲染
  await page.waitForSelector('.plan-editor .step', { timeout: 120000 })
  const stepCount = await page.locator('.plan-editor .step').count()
  check(stepCount >= 1, '任务书步骤已渲染', `${stepCount} 步`)
  await page.waitForTimeout(800)
  check((await badge().innerText()).includes('待审批'), '状态变为待审批', await badge().innerText())

  const goalText = await page.locator('.plan-editor .field-value').first().innerText()
  check(goalText.trim().length > 1 && goalText !== '—', '任务书目标已展示', goalText.slice(0, 40))
  await page.screenshot({ path: `${OUT}/02-plan.png`, fullPage: true })

  log('5) 编辑并保存')
  await page.locator('.plan-editor .mini-btn', { hasText: '编辑' }).click()
  await page.waitForTimeout(500)
  const titleInput = page.locator('.plan-editor .step .form-input').nth(1)
  await titleInput.fill('【人工改过】第一步：抽取需求要点')
  await page.locator('.plan-editor .mini-btn', { hasText: '保存' }).click()
  await page.waitForTimeout(3000)
  const savedTitles = await page.locator('.plan-editor .step-title').allInnerTexts()
  check(
    savedTitles.some(t => t.includes('【人工改过】')),
    '人工编辑已保存并回显',
    savedTitles[0] || ''
  )
  await page.screenshot({ path: `${OUT}/03-edited.png`, fullPage: true })

  log('6) 驳回')
  await page.locator('.plan-editor .btn-secondary', { hasText: '驳回' }).click()
  await page.waitForTimeout(3000)
  const afterReject = await badge().innerText()
  check(afterReject.includes('规划中'), '驳回后回到待规划', afterReject)
  check(
    (await page.locator('.plan-editor .step').count()) === stepCount,
    '驳回保留了任务书'
  )

  log('7) 批准')
  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)
  const afterApprove = await badge().innerText()
  check(afterApprove.includes('已批准'), '批准后状态为已批准', afterApprove)
  check(
    (await page.locator('.plan-editor .mini-btn', { hasText: '编辑' }).count()) === 0,
    '已批准后不再显示编辑入口'
  )
  // 但必须留一条退路：否则用户被锁死在已批准状态
  check(
    (await page.locator('.plan-editor .btn-secondary', { hasText: '驳回以修改' }).count()) === 1,
    '已批准后仍可驳回以修改'
  )
  check(
    (await page.locator('.plan-editor .btn-primary').count()) === 0,
    '已批准后不再显示批准按钮'
  )
  await page.screenshot({ path: `${OUT}/04-approved.png`, fullPage: true })

  log('8) 刷新后状态保持')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3500)
  await page.locator('.chat-item').first().click()
  await page.waitForTimeout(2000)
  check(
    (await badge().innerText()).includes('已批准'),
    '刷新后批准状态保持',
    await badge().innerText()
  )

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
