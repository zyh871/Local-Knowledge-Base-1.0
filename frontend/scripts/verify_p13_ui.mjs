/**
 * P13 界面验证：成本可见性（用量汇总行 + 单步 token）+ 分享页结论不重复。
 *
 * ★ 刻意走**完整真实流程**（澄清 → 任务书 → 批准 → 执行），而不是给前端塞假数据：
 *   这三项改动的价值全在于「真实跑完一次任务后，用户能看到什么」，
 *   假数据只能证明渲染函数没写错，证明不了链路通。
 *
 * 用法：node scripts/verify_p13_ui.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p13'
const WS_NAME = `P13验证-${Date.now().toString().slice(-6)}`
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

let wsId = ''

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

  log('2) 提需求 → 生成任务书 → 批准')
  await page.locator('.composer-textarea').fill('把「支持多轮对话」整理成 3 条开发任务，每条附一句验收标准')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)
  await page.locator('button', { hasText: '生成任务书' }).first().click()
  await page.waitForSelector('.plan-editor .step', { timeout: 180000 })
  // ⚠️ 不能等「第一个 .step 出现」就点批准：任务书是流式生成的，那时 busy 仍为 true，
  //    批准按钮是禁用的（这是对的）。用 click 自带的等待等它真正可用。
  const approveBtn = page.locator('.plan-editor .btn-primary', { hasText: '批准' })
  await approveBtn.click({ timeout: 180000 })
  await page.waitForTimeout(3000)

  log('3) 自动执行剩余步骤')
  await page.waitForSelector('.timeline', { timeout: 15000 })
  // 执行前的基线：澄清 + 规划本身也是真实调用，所以这时**可以有**用量行。
  // 要验证的不是「有没有」，而是「执行后必须变多」—— 少算了才是有害的 bug。
  const callsOf = async () => {
    const n = await page.locator('.usage-line').count()
    if (!n) return 0
    const t = await page.locator('.usage-line').innerText()
    return Number((t.match(/(\d+)\s*次模型调用/) || [0, 0])[1]) || 0
  }
  const callsBefore = await callsOf()
  log(`     执行前：${callsBefore} 次调用（澄清 + 规划）`)
  await page.locator('.tl-actions button', { hasText: '自动执行剩余' }).click()
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
      { timeout: 420000 }
    )
    .catch(() => {})
  await page.waitForTimeout(2500)

  log('4) 成本可见性：用量汇总行')
  const usageLine = page.locator('.usage-line')
  await usageLine.waitFor({ timeout: 15000 })
  const usageText = (await usageLine.innerText()).replace(/\s+/g, ' ')
  check(usageText.includes('次模型调用'), '★ 写明「多少次模型调用」', usageText)
  check(usageText.includes('tokens'), '★ 写明合计 tokens', usageText)
  check(/输入\s*\S+\s*\/\s*输出/.test(usageText), '★ 拆分输入/输出', usageText)
  const callsAfter = Number((usageText.match(/(\d+)\s*次模型调用/) || [0, 0])[1]) || 0
  check(
    callsAfter > callsBefore,
    '★ 执行后调用次数增长（澄清/规划/步骤都没漏算）',
    `${callsBefore} → ${callsAfter}`
  )
  await page.screenshot({ path: `${OUT}/01-usage-line.png`, fullPage: true })

  log('5) 单步 token 标注')
  const tokenLabels = await page.locator('.tl-tokens').allInnerTexts()
  check(tokenLabels.length >= 1, '★ 步骤上标出了 token', JSON.stringify(tokenLabels))
  check(
    tokenLabels.every(t => /tok/.test(t)),
    '单步标签带 tok 单位',
    JSON.stringify(tokenLabels)
  )
  const title = await page.locator('.tl-tokens').first().getAttribute('title')
  check(/输入\s*\d+/.test(title || ''), '悬停能看到输入/输出拆分', String(title))

  log('6) 分享页：结论不重复')
  wsId = await page.evaluate(async name => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
    const ws = await (await fetch('/api/workflow/workspaces', { headers })).json()
    const hit = (ws.workspaces || []).find(w => w.name === name)
    if (!hit) return ''
    // 按工作区过滤，否则会拿到别的工作区的旧任务
    const r = await (
      await fetch(`/api/workflow/runs?workspace_id=${encodeURIComponent(hit.id)}`, { headers })
    ).json()
    return (r.runs || [])[0]?.id || ''
  }, WS_NAME)
  check(!!wsId, '取到本次任务的 id', wsId)
  const share = await page.evaluate(async runId => {
    const headers = {
      Authorization: `Bearer ${localStorage.getItem('token')}`,
      'Content-Type': 'application/json',
    }
    const r = await fetch(`/api/workflow/runs/${runId}/share`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ ttl_hours: 24 }),
    })
    return r.ok ? await r.json() : { error: r.status }
  }, wsId)
  check(!!share.url_path, '分享链接已生成', JSON.stringify(share).slice(0, 120))

  if (share.url_path) {
    const p2 = await browser.newPage({ viewport: { width: 1200, height: 1000 } })
    await p2.goto(BASE + share.url_path, { waitUntil: 'domcontentloaded' })
    await p2.waitForSelector('.wf-step', { timeout: 30000 })
    await p2.waitForTimeout(1200)
    const pointers = await p2.locator('.wf-step-pointer').allInnerTexts()
    check(pointers.length >= 1, '★ 结论步骤在明细里只剩一句指引（不再重复正文）', JSON.stringify(pointers))
    const bodies = await p2.locator('.wf-step-body').allInnerTexts()
    const long = bodies.map(t => t.trim()).filter(t => t.length > 30)
    const dup = long.filter((t, i) => long.findIndex(x => x === t) !== i)
    check(dup.length === 0, '★ 长正文没有重复出现', `${dup.length} 处重复`)
    await p2.screenshot({ path: `${OUT}/02-shared.png`, fullPage: true })
    await p2.close()
  }

  log('7) 清理')
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
  if (WS_NAME.startsWith('P13验证-')) {
    try {
      const p3 = await browser.newPage()
      await p3.goto(BASE, { waitUntil: 'domcontentloaded' })
      await p3.waitForTimeout(2000)
      const r = await p3.evaluate(async () => {
        const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
        const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
        const t = (list.workspaces || []).filter(w => w.name.startsWith('P13验证-'))
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
