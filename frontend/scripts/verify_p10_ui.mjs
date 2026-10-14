/**
 * P10 界面验证：已用时秒表 + 选择器置灰 + 模型 chip 显示友好名。
 *
 * 对应诊断里最让人困惑的三件事：
 *   1. 等待 2 分钟画面毫无变化 → 现在运行中的步骤显示「已运行 m:ss」，完成的步骤显示总耗时
 *   2. 选择器列出不可引用的文件，点了才被拒 → 现在置灰并标注「不支持」
 *   3. 模型 chip 显示成 custom:me_xxx → 现在显示用户起的名字
 *
 * 用法：node scripts/verify_p10_ui.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p10'
const WS_NAME = `P10验证-${Date.now().toString().slice(-6)}`
mkdirSync(OUT, { recursive: true })

// 造一个「有可引用 + 不可引用文件」的目录
const FOLDER_NAME = `kb-p10-${Date.now().toString().slice(-6)}`
const localDir = join(homedir(), FOLDER_NAME)
mkdirSync(localDir, { recursive: true })
writeFileSync(join(localDir, 'page.html'), '<html><body><h1>hi</h1></body></html>', 'utf-8')
writeFileSync(join(localDir, 'notes.md'), '# 笔记', 'utf-8')
writeFileSync(join(localDir, 'archive.zip'), 'PK\x03\x04fake', 'utf-8') // .zip 不在白名单

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
  log('1) 登录')
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

  log('2) 模型 chip 应显示友好名（而不是 custom:me_xxx）')
  // 只有模型 chip 带 title（提示当前模型的完整名称），用它定位最稳
  const chip = page.locator('[data-role="model-chip"]').first()
  const defaultLabel = (await chip.innerText()).trim()
  check(defaultLabel.length > 2, '默认模型显示的是可读名称', defaultLabel)
  check(!defaultLabel.startsWith('custom:'), '默认项不是机器码 id', defaultLabel)

  await chip.click()
  await page.waitForSelector('.menu', { timeout: 8000 })
  const customOption = page.locator('.menu-item', { hasText: '自定义' }).first()
  const hasCustom = (await customOption.count()) > 0
  if (hasCustom) {
    await customOption.click()
    await page.waitForTimeout(1500)
    const label = (await chip.innerText()).trim()
    check(!label.startsWith('custom:'), '★ 选中自定义模型后 chip 不再是机器码', label)
    check(label.includes('DeepSeek'), '显示用户起的端点名', label)
  } else {
    check(true, '（当前没有自定义模型，跳过该项）')
  }
  await page.screenshot({ path: `${OUT}/01-model-chip.png`, fullPage: true })

  // 切回内置模型，避免后面用自定义模型跑步骤（省时间也更可控）
  await chip.click()
  await page.waitForTimeout(800)
  const builtin = page.locator('.menu-item').filter({ hasNotText: '自定义' }).first()
  if (await builtin.count()) {
    await builtin.click()
    await page.waitForTimeout(800)
  }

  log('3) 新建工作区并绑定测试目录')
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

  log('4) 新建任务 → 选择器应把不支持的文件置灰')
  await page.locator('.composer-textarea').fill('把 page.html 改得更好看')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)

  const addBtn = page.locator('.mini-btn', { hasText: '从工作区文件夹添加' })
  check((await addBtn.count()) === 1, '有「从工作区文件夹添加」入口')
  await addBtn.click()
  await page.waitForSelector('.fp-panel', { timeout: 10000 })
  await page.waitForTimeout(1500)

  const htmlItem = page.locator('.fp-item', { hasText: 'page.html' }).first()
  const zipItem = page.locator('.fp-item', { hasText: 'archive.zip' }).first()
  check((await htmlItem.count()) === 1, '.html 出现在列表里')

  // 断言「可否引用」看的是**类名**（pickable / blocked）而不是 disabled：
  // disabled 里还混着选择器自身的忙状态，会把两类原因搞混。
  const htmlClass = (await htmlItem.getAttribute('class')) || ''
  const zipClass = (await zipItem.getAttribute('class')) || ''
  check(htmlClass.includes('pickable') && !htmlClass.includes('blocked'), '★ .html 判定为可引用', htmlClass)
  check(zipClass.includes('blocked'), '★ .zip 判定为不可引用', zipClass)
  // 静态状态下真的点得动（忙状态只在绑定/导入的那一瞬间）
  check(!(await htmlItem.isDisabled()), '.html 可点选')
  check(
    (await zipItem.innerText()).includes('不支持'),
    '置灰项标了「不支持」',
    (await zipItem.innerText()).replace(/\s+/g, ' ')
  )
  const hint = await page.locator('.fp-hint').innerText()
  check(hint.includes('.html'), '底部说明了可引用类型', hint.replace(/\s+/g, ' ').slice(0, 60))
  check(hint.includes('不支持引用'), '提示了有几个文件类型不支持', hint.replace(/\s+/g, ' ').slice(0, 70))
  await page.screenshot({ path: `${OUT}/02-picker-blocked.png`, fullPage: true })

  log('5) 点选 .html 应成功加入')
  await htmlItem.click()
  await page.waitForTimeout(300)
  await page.locator('.fp-actions .mini-btn', { hasText: '加入选中的' }).click()
  await page.waitForTimeout(4000)
  const rows = await page.locator('.file-row').allInnerTexts()
  check(
    rows.some(t => t.includes('page.html')),
    '★ .html 已引用到任务里',
    JSON.stringify(rows.map(t => t.replace(/\s+/g, ' ').slice(0, 26)))
  )
  await page.screenshot({ path: `${OUT}/03-html-added.png`, fullPage: true })

  log('6) 运行中的步骤应显示已用时')
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 240000 })
  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)

  await page.locator('.tl-actions button', { hasText: '执行下一步' }).click()

  // 密集轮询捕捉「运行中」这一刻：用 glm-4-flash 跑小步骤可能几秒就结束，
  // 用 waitForFunction 容易整个错过。
  let runningChip = ''
  let banner = ''
  let secs1 = -1
  for (let i = 0; i < 300; i++) {
    const chips = await page.locator('.tl-chip.s-running').allInnerTexts()
    if (chips.length) {
      runningChip = chips[0].replace(/\s+/g, ' ')
      banner = (await page.locator('.tl-hint').allInnerTexts()).join(' ').replace(/\s+/g, ' ')
      secs1 = Number((runningChip.match(/(\d+):(\d+)/) || [])[2] ?? -1)
      break
    }
    await page.waitForTimeout(200)
  }

  if (!runningChip) {
    log('    -- 没捕捉到运行中状态，看看是否已经跑完 --')
    log('      chips: ' + JSON.stringify(await page.locator('.tl-chip').allInnerTexts()))
    log('      耗时: ' + JSON.stringify(await page.locator('.tl-elapsed').allInnerTexts()))
  }
  check(!!runningChip, '捕捉到「执行中」的步骤', runningChip || '(未捕捉到)')
  check(/\d+:\d\d/.test(runningChip), '★ 运行中的状态徽标带计时 m:ss', runningChip)

  // 若还在跑，再等几秒看秒数是否增长
  if (runningChip && secs1 >= 0) {
    await page.waitForTimeout(4000)
    const stillRunning = await page.locator('.tl-chip.s-running').allInnerTexts()
    if (stillRunning.length) {
      const secs2 = Number(
        ((stillRunning[0].replace(/\s+/g, ' ')).match(/(\d+):(\d+)/) || [])[2] ?? -1
      )
      check(secs2 > secs1, '★ 秒表在走动（4 秒后变大）', `${secs1}s → ${secs2}s`)
      const banner2 = (await page.locator('.tl-hint').allInnerTexts()).join(' ').replace(/\s+/g, ' ')
      check(
        banner2.includes('已运行'),
        '★ 提示条也显示已用时',
        banner2.slice(0, 70) || '(提示条为空)'
      )
    } else {
      log('    （步骤已在这 4 秒内跑完，跳过秒表增长断言）')
    }
  }

  // 完成的步骤必须显示总耗时 —— 这是静态的、每次都能断言
  await page.waitForSelector('.tl-elapsed', { timeout: 120000 })
  const durations = await page.locator('.tl-elapsed').allInnerTexts()
  check(
    durations.some(t => /\d+\s*(秒|分)/.test(t)),
    '★ 已完成的步骤显示总耗时',
    JSON.stringify(durations)
  )
  await page.screenshot({ path: `${OUT}/04-elapsed.png`, fullPage: true })

  log('7) 终止任务并清理')
  await page.locator('.tl-actions button', { hasText: '终止任务' }).click()
  await page.waitForTimeout(2500)

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
  try {
    if (localDir.startsWith(homedir()) && FOLDER_NAME.startsWith('kb-p10-')) {
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
