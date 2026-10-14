/**
 * P7 界面验证：生成交付物 → 下载 → 生成分享链接 → 无鉴权打开分享页。
 *
 * 分享页刻意用**独立的浏览器上下文**打开（等价于无痕窗口），
 * 这样才能真正验证「公开可读」而不是「带着登录态看着像公开」。
 *
 * 用法：node scripts/verify_p7_deliver.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p7'
const WS_NAME = 'P7界面验证'
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

  await page.locator('.composer-textarea').fill('总结小团队做代码评审的三条落地建议')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(3000)

  log('2) 生成任务书并批准，然后自动执行')
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 180000 })
  await page.locator('.plan-editor .btn-primary', { hasText: '批准' }).click()
  await page.waitForTimeout(3000)

  await page.locator('.tl-actions button', { hasText: '自动执行剩余' }).click()
  await page.waitForFunction(
    () => {
      const chips = [...document.querySelectorAll('.tl-chip')]
      return (
        chips.length > 0 &&
        chips.every(c => ['已完成', '已跳过', '失败'].includes(c.innerText.trim()))
      )
    },
    null,
    { timeout: 400000 }
  )
  await page.waitForTimeout(2500)
  check((await badge().innerText()).includes('已完成'), '任务已完成', await badge().innerText())

  log('3) 生成交付物')
  await page.waitForSelector('.artifact-list, .card:has-text("交付物")', { timeout: 20000 })
  const deliverBtn = page.locator('.mini-btn', { hasText: '生成交付物' })
  check((await deliverBtn.count()) === 1, '提供「生成交付物」入口')
  await deliverBtn.click()
  await page.waitForSelector('.artifact-list li', { timeout: 60000 })
  const artifactName = await page.locator('.artifact-link').first().innerText()
  check(artifactName.trim().endsWith('.md'), '交付物已出现在列表', artifactName.trim())
  const artifactSize = await page.locator('.artifact-meta').first().innerText()
  check(/\d/.test(artifactSize), '显示交付物大小', artifactSize.trim())

  log('4) 下载交付物')
  const downloadPromise = page.waitForEvent('download', { timeout: 30000 })
  await page.locator('.artifact-link').first().click()
  const download = await downloadPromise.catch(() => null)
  check(!!download, '交付物可下载', download ? download.suggestedFilename() : '未触发下载')
  await page.screenshot({ path: `${OUT}/01-artifact.png`, fullPage: true })

  log('5) 生成分享链接')
  await page.locator('.mini-btn', { hasText: '生成分享链接' }).click()
  await page.waitForSelector('.share-box', { timeout: 30000 })
  const shareUrl = await page.locator('.share-box input').inputValue()
  check(shareUrl.includes('/shared/'), '分享链接已生成', shareUrl)
  await page.screenshot({ path: `${OUT}/02-share.png`, fullPage: true })

  log('6) 无鉴权打开分享页（独立上下文 = 无痕）')
  const anonymous = await browser.newContext({ viewport: { width: 1100, height: 1000 } })
  const guest = await anonymous.newPage()
  const guestErrors = []
  guest.on('pageerror', e => guestErrors.push(String(e)))
  await guest.goto(shareUrl, { waitUntil: 'domcontentloaded' })
  await guest.waitForTimeout(3000)

  const title = await guest.locator('.shared-title').innerText().catch(() => '')
  check(!title.includes('登录'), '分享页无需登录即可打开', title.slice(0, 40))
  check((await guest.locator('.wf-step').count()) >= 1, '分享页渲染了步骤时间线',
    `${await guest.locator('.wf-step').count()} 步`)
  const sectionTitles = await guest.locator('.wf-h2').allInnerTexts()
  check(sectionTitles.includes('执行明细'), '含执行明细段', JSON.stringify(sectionTitles))
  const hasConclusion = sectionTitles.some(t => t.includes('结论'))
  check(hasConclusion || sectionTitles.includes('交付物'), '含结论或交付物段', JSON.stringify(sectionTitles))
  // 分享页不能有下载原文件的入口
  check(
    (await guest.locator('a[href*="/workflow/files/"]').count()) === 0,
    '★ 分享页没有原始文件下载入口'
  )
  await guest.screenshot({ path: `${OUT}/03-shared-view.png`, fullPage: true })
  await anonymous.close()
  if (guestErrors.length) log('    访客页错误：' + guestErrors.join(' | '))

  log('7) 清理验证数据')
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
