/**
 * P14 界面验证：产出文件看得见、下得下来，会失败的问题先看到。
 *
 * 走完整真实流程（提需求 → 生成任务书 → 批准 → 逐步执行 → 下载产物），
 * 因为要验证的正是「用户到底能不能拿到那个文件」——
 * 单元测试只能证明工具写对了盘，证明不了界面把它呈现出来了。
 *
 * 用法：node scripts/verify_p14_file_write.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p14'
const WS_NAME = `P14验证-${Date.now().toString().slice(-6)}`
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1500, height: 1100 },
  acceptDownloads: true,
})
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

/** 找到含指定工具的步骤节点。 */
async function toolStep(toolName) {
  const steps = page.locator('.plan-editor .step')
  const count = await steps.count()
  for (let i = 0; i < count; i += 1) {
    const node = steps.nth(i)
    const select = node.locator('select.tool-select')
    if ((await select.count()) && (await select.inputValue()) === toolName) return node
  }
  return null
}

try {
  log('1) 登录并新建工作区')
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

  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForSelector('.menu-wide', { timeout: 5000 })
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1500)
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForTimeout(400)

  log('2) 提需求 → 生成任务书')
  await page
    .locator('.composer-textarea')
    .fill('写一个 Python 脚本计算斐波那契数列前 20 项，保存为 fib.py')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 180000 })
  await page.waitForTimeout(1500)

  log('3) 任务书里应出现 file_write 步骤')
  await page.locator('.plan-editor .mini-btn', { hasText: '编辑' }).click()
  await page.waitForTimeout(600)
  const fwStep = await toolStep('file_write')
  check(!!fwStep, '★ 模型规划出了 file_write 步骤')
  // 文件名只在参数输入框里，`innerText` 读不到 —— 必须连同输入值一起看
  const argValues = await page
    .locator('.plan-editor input.mono')
    .evaluateAll(els => els.map(e => e.value))
  const planBlob = (await page.locator('.plan-editor').innerText()) + ' ' + argValues.join(' ')
  check(
    planBlob.includes('fib.py'),
    '任务书里带上了目标文件名 fib.py',
    argValues.join(' ').slice(0, 90)
  )

  log('4) 「会失败」的校验问题要单独高亮')
  let savedArgs = ''
  if (fwStep) {
    const argsInput = fwStep.locator('input.mono')
    savedArgs = await argsInput.inputValue()
    await argsInput.fill('{}')
    await page.waitForTimeout(300)
    await page.locator('.plan-editor .mini-btn.primary', { hasText: '保存' }).click()
    await page.waitForTimeout(3000)

    const blocking = page.locator('.issues-blocking')
    await blocking.waitFor({ timeout: 15000 })
    const text = (await blocking.innerText()).replace(/\s+/g, ' ')
    check(text.includes('会导致执行失败'), '★ 单独列出「会失败」的问题', text.slice(0, 70))
    check(text.includes('file_write'), '问题点名了是哪个工具')
    await page.screenshot({ path: `${OUT}/01-blocking-issues.png`, fullPage: true })

    // 恢复原参数，避免把验证数据留在失败态
    await page.locator('.plan-editor .mini-btn', { hasText: '编辑' }).click()
    await page.waitForTimeout(600)
    const restore = await toolStep('file_write')
    await restore.locator('input.mono').fill(savedArgs)
    await page.locator('.plan-editor .mini-btn.primary', { hasText: '保存' }).click()
    await page.waitForTimeout(3000)
    check(
      (await page.locator('.issues-blocking').count()) === 0,
      '恢复参数后不再有「会失败」提示'
    )
  }

  log('5) 批准并逐步执行（强制审批的工具需要单独批准）')
  const approveBtn = page.locator('.plan-editor .btn-primary', { hasText: '批准' })
  await approveBtn.click({ timeout: 180000 })
  await page.waitForTimeout(3000)

  // 逐步推进：先批所有「批准此步」，再点唯一可用的「执行」。
  // 不用「自动执行剩余」—— 它遇到强制审批的步骤会**置灰**，
  // 而置灰的原因不在按钮上，光看按钮会误判成"界面坏了"。
  for (let guard = 0; guard < 20; guard += 1) {
    const gateBtn = page.locator('.tl-step-actions .mini-btn', { hasText: '批准此步' })
    if (await gateBtn.count()) {
      await gateBtn.first().click({ timeout: 60000 })
      await page.waitForTimeout(1800)
      continue
    }
    const execBtn = page.locator('.tl-step-actions .mini-btn', { hasText: '执行' })
    if (await execBtn.count()) {
      await execBtn.first().click({ timeout: 120000 })
      await page.waitForTimeout(3000)
      continue
    }
    const chips = (await page.locator('.tl-chip').allInnerTexts()).map(t => t.trim())
    if (chips.length && chips.every(c => ['已完成', '已跳过', '失败'].includes(c))) break
    await page.waitForTimeout(1500)
  }

  const chips = (await page.locator('.tl-chip').allInnerTexts()).map(t => t.trim())
  log(`     步骤状态：${JSON.stringify(chips)}`)
  check(
    chips.every(c => c.startsWith('已完成')),
    '所有步骤都已完成',
    JSON.stringify(chips)
  )

  log('6) 步骤卡片上应能看到并下载产出文件')
  const written = page.locator('.tl-written')
  await written.first().waitFor({ timeout: 20000 })
  const writtenText = (await written.first().innerText()).replace(/\s+/g, ' ')
  check(writtenText.includes('fib.py'), '★ 步骤卡片标出了产出文件', writtenText)
  check(writtenText.includes('产出文件'), '有「产出文件」标签')
  const link = page.locator('.tl-written-link').first()
  check((await link.innerText()).includes('fib.py'), '文件是可点的下载入口')

  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 30000 }),
    link.click(),
  ])
  const name = download.suggestedFilename()
  check(name.includes('fib.py'), '★ 点一下真的下载到 fib.py', name)
  const path = await download.path()
  const { readFileSync } = await import('node:fs')
  const body = readFileSync(path, 'utf-8')
  check(!body.trimStart().startsWith('```'), '★ 下载到的文件没有 markdown 围栏', JSON.stringify(body.slice(0, 28)))
  check(/def |print\(/.test(body), '内容是真代码', body.slice(0, 40).replace(/\n/g, ' | '))
  await page.screenshot({ path: `${OUT}/02-written-file.png`, fullPage: true })

  log('7) 产物列表里也应出现')
  const artifactText = (await page.locator('.artifact-list').innerText()).replace(/\s+/g, ' ')
  check(artifactText.includes('fib.py'), '★ 产物列表里能看到 fib.py', artifactText.slice(0, 80))

  log('8) 清理')
  const cleaned = await page.evaluate(async name => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
    const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
    const targets = (list.workspaces || []).filter(w => w.name === name)
    let ok = 0
    for (const t of targets) {
      const res = await fetch(`/api/workflow/workspaces/${t.id}`, { method: 'DELETE', headers })
      if (res.ok) ok += 1
    }
    return `deleted:${ok}/${targets.length}`
  }, WS_NAME)
  check(cleaned.includes('deleted:1/1'), '验证用工作区已清理', cleaned)
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  try {
    const p3 = await ctx.newPage()
    await p3.goto(BASE, { waitUntil: 'domcontentloaded' })
    await p3.waitForTimeout(2000)
    const r = await p3.evaluate(async () => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
      const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
      const t = (list.workspaces || []).filter(w => w.name.startsWith('P14验证-'))
      let ok = 0
      for (const w of t) {
        const res = await fetch(`/api/workflow/workspaces/${w.id}`, { method: 'DELETE', headers })
        if (res.ok) ok += 1
      }
      return `${ok}/${t.length}`
    })
    if (r !== '0/0') log(`兜底清理工作区：${r}`)
    await p3.close()
  } catch (e) {
    log('兜底清理失败：' + e.message)
  }
  await browser.close()
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
