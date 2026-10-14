/**
 * P11 界面验证：改动 1（缺失步骤提示）+ 改动 2（批量加入 + 如实告知注入量）。
 *
 * 用的目录刻意造得**超出上下文预算**，这样「实际只注入约 N 字」那段才会出现 ——
 * 那正是用户最需要看到、也最容易误解的一件事。
 *
 * 用法：node scripts/verify_p11_ui.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p11'
const WS_NAME = `P11验证-${Date.now().toString().slice(-6)}`
mkdirSync(OUT, { recursive: true })

// 造一个「有内容 + 超出预算 + 含不支持类型」的目录
const FOLDER_NAME = `kb-p11-${Date.now().toString().slice(-6)}`
const localDir = join(homedir(), FOLDER_NAME)
mkdirSync(localDir, { recursive: true })
// 每个约 2 万字 → 两个文件就超过 8000 字预算
writeFileSync(join(localDir, '讲义一.md'), '# 讲义一\n' + '内容内容内容。'.repeat(3000), 'utf-8')
writeFileSync(join(localDir, '讲义二.md'), '# 讲义二\n' + '要点要点要点。'.repeat(3000), 'utf-8')
writeFileSync(join(localDir, '课件.html'), '<html><body>' + 'x'.repeat(500) + '</body></html>', 'utf-8')
writeFileSync(join(localDir, '打包.zip'), 'PK', 'utf-8') // 不支持

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

const openWsMenu = async () => {
  if ((await page.locator('.menu-wide').count()) === 0) {
    await page.locator('.composer-meta .composer-meta-btn').first().click()
    await page.waitForSelector('.menu-wide', { timeout: 5000 })
  }
}

try {
  log('1) 登录并绑定测试目录')
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
  await page.waitForTimeout(2000)

  await openWsMenu()
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1800)
  await openWsMenu()
  await page.locator('.menu-folder .mini-btn', { hasText: '选择' }).click()
  await page.waitForSelector('.fp-panel', { timeout: 10000 })
  await page.waitForSelector('.fp-root', { timeout: 20000 })
  await page.locator('.fp-root', { hasText: '主目录' }).first().click()
  await page.waitForTimeout(1500)
  await page.locator('.fp-item', { hasText: FOLDER_NAME }).first().click()
  await page.waitForTimeout(1500)
  await page.locator('.mini-btn', { hasText: '选择此文件夹' }).click()
  await page.waitForTimeout(2500)
  await openWsMenu()
  check(
    (await page.locator('.menu-folder-head').innerText()).includes(FOLDER_NAME),
    '工作区已绑定测试目录'
  )
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForTimeout(400)

  log('2) 改动 1：新建任务后应出现「文件还没加入」的提示')
  await page.locator('.composer-textarea').fill('整理这个文件夹里的文件')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(5000)

  const callout = page.locator('.folder-callout')
  await callout.waitFor({ timeout: 20000 })
  const calloutText = (await callout.innerText()).replace(/\s+/g, ' ')
  check(calloutText.includes('4'), '提示里给出了文件数', calloutText.slice(0, 60))
  check(
    calloutText.includes('还没加入') || calloutText.includes('一个都还没加入'),
    '★ 明确说明「一个都还没加入本任务」'
  )
  check(
    calloutText.includes('AI 现在看不到'),
    '★ 说明 AI 看不到内容（消除「访问不了本地文件」的误判）'
  )
  const addSel = page.locator('.folder-callout-actions .mini-btn', { hasText: '选择要加入的文件' })
  const addAll = page.locator('.folder-callout-actions .mini-btn', { hasText: '全部加入' })
  check((await addSel.count()) === 1, '提供「选择要加入的文件」入口')
  check((await addAll.count()) === 1, '提供「全部加入」入口', await addAll.innerText())
  // .zip 不可引用 → 「全部加入」只应覆盖 3 个
  const allText = await addAll.innerText()
  check(allText.includes('3'), '「全部加入」只算可引用的 3 个（.zip 排除）', allText)
  await page.screenshot({ path: `${OUT}/01-callout.png`, fullPage: true })

  log('3) 改动 2：一键全部加入 → 应如实告知实际注入量')
  await addAll.click()
  await page.waitForTimeout(6000)
  const rows = await page.locator('.file-row').allInnerTexts()
  check(rows.length === 3, '3 个文件都已加入', JSON.stringify(rows.map(t => t.replace(/\s+/g, ' ').slice(0, 24))))
  check(
    !(await page.locator('.folder-callout').count()),
    '加入后「还没加入」提示消失'
  )

  const note = page.locator('.file-context-note')
  await note.waitFor({ timeout: 20000 })
  const noteText = (await note.innerText()).replace(/\s+/g, ' ')
  check(noteText.includes('预算'), '★ 说明了上下文预算', noteText.slice(0, 80))
  check(
    noteText.includes('实际只会注入'),
    '★ 如实告知「实际只会注入」多少（全部加入 ≠ 全部进上下文）',
    noteText.slice(0, 110)
  )
  check(noteText.includes('减少文件数量'), '给出了可操作的建议')

  const injectLabels = await page.locator('.file-inject').allInnerTexts()
  check(injectLabels.length >= 1, '每个文件标注了注入量', JSON.stringify(injectLabels.slice(0, 3)))
  check(
    injectLabels.every(t => /\d+%/.test(t)),
    '注入量带百分比',
    JSON.stringify(injectLabels.slice(0, 3))
  )
  await page.screenshot({ path: `${OUT}/02-injected.png`, fullPage: true })

  log('4) 选择器多选：选 2 个应提示「每个只占 1/2 预算」')
  await page.locator('.mini-btn', { hasText: '从工作区文件夹添加' }).click()
  await page.waitForSelector('.fp-panel', { timeout: 10000 })
  await page.waitForTimeout(1500)
  const first = page.locator('.fp-item.pickable').first()
  await first.click()
  await page.waitForTimeout(300)
  const budgetWarn = page.locator('.fp-budget')
  check(!(await budgetWarn.count()), '只选 1 个时不显示预算提醒（没信息量）')
  await page.locator('.fp-item.pickable').nth(1).click()
  await page.waitForTimeout(400)
  await budgetWarn.waitFor({ timeout: 5000 })
  const warnText = (await budgetWarn.innerText()).replace(/\s+/g, ' ')
  check(warnText.includes('1/2'), '★ 选 2 个时提示每个只占 1/2 预算', warnText.slice(0, 70))
  check(
    (await page.locator('.fp-actions .mini-btn', { hasText: '加入选中的 2 个文件' }).count()) === 1,
    '底部按钮显示选中数量'
  )
  await page.screenshot({ path: `${OUT}/03-multi-select.png`, fullPage: true })

  // 全选/清空
  await page.locator('.fp-actions .mini-btn', { hasText: '全选可引用' }).click()
  await page.waitForTimeout(400)
  const allPicked = await page.locator('.fp-item.picked').count()
  check(allPicked === 3, '「全选可引用」只选可引用的 3 个（.zip 不选）', `${allPicked} 个`)
  await page.locator('.fp-actions .mini-btn', { hasText: '清空' }).click()
  await page.waitForTimeout(400)
  check((await page.locator('.fp-item.picked').count()) === 0, '「清空」可用')
  await page.locator('.fp-close').click()
  await page.waitForTimeout(500)

  log('5) 清理')
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
  // 兜底清理工作区 + 测试目录
  try {
    const p2 = await browser.newPage()
    await p2.goto(BASE, { waitUntil: 'domcontentloaded' })
    await p2.waitForTimeout(2000)
    const r = await p2.evaluate(async name => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
      const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
      const t = (list.workspaces || []).filter(w => w.name.startsWith(name))
      let ok = 0
      for (const w of t) {
        const res = await fetch(`/api/workflow/workspaces/${w.id}`, { method: 'DELETE', headers })
        if (res.ok) ok += 1
      }
      return `${ok}/${t.length}`
    }, 'P11验证-')
    if (r !== '0/0') log(`兜底清理工作区：${r}`)
    await p2.close()
  } catch (e) {
    log('兜底清理失败：' + e.message)
  }
  await browser.close()
  try {
    if (localDir.startsWith(homedir()) && FOLDER_NAME.startsWith('kb-p11-')) {
      rmSync(localDir, { recursive: true, force: true })
    }
  } catch (e) {
    log('清理测试目录失败：' + e.message)
  }
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
