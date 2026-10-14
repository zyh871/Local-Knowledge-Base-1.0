/**
 * P12 验证：底部输入区在 100% 缩放下必须完整可见、不被遮挡、可正常输入。
 *
 * 三件事都测，而不是只量高度：
 *   1. **不越界**：input-area 的矩形底边不超过视口底部
 *   2. **不被遮挡**：在输入框中心做命中测试（elementFromPoint），
 *      拿到的最上层元素必须是输入区内部 —— 否则就是被别的元素盖住了
 *   3. **可输入**：真的点进去打字，文字要落在 textarea 里
 *
 * 覆盖多种视口高度（含很矮的窗口）与**长会话**（消息多到需要滚动时，
 * 正是遮挡最容易发生的场景）。
 *
 * 用法：node scripts/verify_p12_layout.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p12'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

/** 在任意页面上检查底部输入区：不越界 / 不被遮挡 / 可输入。 */
const probeComposer = (page, selector) =>
  page.evaluate(sel => {
    const el = document.querySelector(sel)
    if (!el) return { missing: true }
    const r = el.getBoundingClientRect()
    const vh = window.innerHeight
    const input = el.querySelector('textarea')
    const ir = input ? input.getBoundingClientRect() : null

    // 命中测试：取输入框中心点，看最上层元素是否在输入区内部
    let hitInside = null
    let hitDesc = ''
    if (ir) {
      const x = Math.round(ir.left + ir.width / 2)
      const y = Math.round(ir.top + ir.height / 2)
      if (y > 0 && y < vh) {
        const top = document.elementFromPoint(x, y)
        hitInside = !!(top && el.contains(top))
        hitDesc = top ? `${top.tagName.toLowerCase()}.${(top.className || '').toString().split(' ')[0]}` : '(null)'
      }
    }

    // 工具栏（最底下那一行）是否也在视口内
    const toolbar = el.querySelector('.input-toolbar, .toolbar, .composer-toolbar')
    const tr = toolbar ? toolbar.getBoundingClientRect() : null

    return {
      vh,
      inputTop: Math.round(r.top),
      inputBottom: Math.round(r.bottom),
      inputH: Math.round(r.height),
      overflow: Math.round(r.bottom - vh),
      textareaVisible: ir ? ir.height > 10 && ir.top >= 0 && ir.bottom <= vh : false,
      textareaBottom: ir ? Math.round(ir.bottom) : null,
      hitInside,
      hitDesc,
      toolbarBottom: tr ? Math.round(tr.bottom) : null,
      toolbarVisible: tr ? tr.bottom <= vh + 0.5 : null,
      docScrollH: document.documentElement.scrollHeight,
    }
  }, selector)

const browser = await chromium.launch({ channel: 'msedge', headless: true })

try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  const consoleErrors = []
  page.on('console', m => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', e => consoleErrors.push(String(e)))

  log('1) 登录')
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  if (await page.locator('input[type="password"]').count()) {
    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.locator('form button, .login-card button, button').first().click()
    await page.waitForTimeout(3500)
  }

  log('2) 多种视口高度下，输入区都必须完整可见')
  // ⚠️ 只加载一次、之后**只改视口尺寸**：布局是 CSS 驱动的，改尺寸会立即重排。
  // 反复整页导航会触发后端限流（实测打出 429，连会话列表都拉不到），
  // 那种失败是测试自己造成的，与被测的布局问题无关。
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.input-area', { timeout: 25000 })
  await page.waitForTimeout(2500)

  // 覆盖「很矮的窗口」到「很高的窗口」——矮窗口是最容易出问题的场景
  for (const h of [560, 600, 700, 800, 900, 1080]) {
    await page.setViewportSize({ width: 1280, height: h })
    await page.waitForTimeout(700)
    const p = await probeComposer(page, '.input-area')
    check(p.inputBottom <= p.vh, `对话页 ${h}px：输入区不越界`, `底边 ${p.inputBottom} / 视口 ${p.vh}`)
    check(p.textareaVisible, `对话页 ${h}px：输入框完整可见`, `textarea 底 ${p.textareaBottom}`)
    check(p.hitInside !== false, `对话页 ${h}px：输入框中心未被遮挡`, p.hitDesc)
    if (h === 800) await page.screenshot({ path: `${OUT}/01-chat-800.png` })
    if (h === 600) await page.screenshot({ path: `${OUT}/02-chat-600.png` })
  }

  log('3) 长会话（消息多到需要滚动）下同样不能遮挡')
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.waitForTimeout(700)
  // 侧栏会话列表来自 localStorage（`chatData`），不是从后端拉的 ——
  // 全新浏览器上下文里它是空的（后端那 26 条不会出现在侧栏）。
  // 所以这里直接种一份**真实数据结构**的长会话，用应用自己的消息组件渲染，
  // 比往 DOM 里塞占位 div 更接近真实场景。
  const seededCount = await page.evaluate(() => {
    const msgs = []
    for (let i = 0; i < 24; i++) {
      msgs.push({ role: 'user', content: `第 ${i + 1} 个问题：请说明 RAG 的第 ${i + 1} 个要点。` })
      msgs.push({
        role: 'assistant',
        content:
          `第 ${i + 1} 个回答：` +
          '这是用于布局自检的回答内容，需要足够长才能把消息区推满。'.repeat(6),
      })
    }
    const convId = 'p12-seed'
    localStorage.setItem(
      'chatData',
      JSON.stringify({
        conversationId: convId,
        chatHistory: [
          { id: convId, title: 'P12 布局自检长会话', createdAt: Date.now(), updatedAt: Date.now() },
        ],
        chatMessages: { [convId]: msgs },
        pinnedChats: [],
        groups: [],
      })
    )
    return msgs.length
  })
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(2500)
  const chatItems = page.locator('.chat-list .chat-item')
  await chatItems.first().waitFor({ timeout: 15000 })
  check((await chatItems.count()) >= 1, '种入的长会话出现在侧栏', `${await chatItems.count()} 条`)
  await chatItems.first().click()
  await page.waitForTimeout(2000)

  const bubbles = await page.locator('.message-item').count()
  check(bubbles >= 20, '长会话已渲染', `${bubbles} 条消息（种入 ${seededCount}）`)
  const pLong = await probeComposer(page, '.input-area')
  check(pLong.inputBottom <= pLong.vh, `长会话(${bubbles} 条)：输入区不越界`, `底边 ${pLong.inputBottom} / ${pLong.vh}`)
  check(pLong.textareaVisible, `长会话(${bubbles} 条)：输入框完整可见`, `textarea 底 ${pLong.textareaBottom}`)
  check(pLong.hitInside !== false, `长会话(${bubbles} 条)：输入框中心未被遮挡`, pLong.hitDesc)
  const longScroll = await page.evaluate(() => {
    const m = document.querySelector('.messages-area')
    return m ? { scrollH: m.scrollHeight, clientH: m.clientHeight } : null
  })
  check(
    !!longScroll && longScroll.scrollH > longScroll.clientH,
    '★ 消息区确实在滚动（而不是把输入区顶出视口）',
    longScroll ? `scrollH=${longScroll.scrollH} clientH=${longScroll.clientH}` : '(未找到)'
  )
  await page.screenshot({ path: `${OUT}/03-chat-long.png` })

  // 再压到很矮的窗口：内容长 + 视口矮，是最容易暴露遮挡的组合
  for (const h of [560, 700]) {
    await page.setViewportSize({ width: 1280, height: h })
    await page.waitForTimeout(700)
    const p = await probeComposer(page, '.input-area')
    check(p.inputBottom <= p.vh, `长会话 + ${h}px 矮窗口：输入区不越界`, `底边 ${p.inputBottom} / ${p.vh}`)
    check(p.hitInside !== false, `长会话 + ${h}px 矮窗口：输入框未被遮挡`, p.hitDesc)
  }
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.waitForTimeout(500)

  log('4) 真的能打字（点击 + 输入）')
  // 复用上一步那个「内容很长」的页面，正好顺便验证长内容下也能输入
  const ta = page.locator('.input-area textarea').first()
  await ta.scrollIntoViewIfNeeded()
  await ta.click()
  await ta.fill('布局自检：这段文字必须完整落在输入框里')
  await page.waitForTimeout(400)
  const typed = await ta.inputValue()
  check(typed.includes('布局自检'), '★ 输入框可正常聚焦并输入', typed.slice(0, 24))
  const p2 = await probeComposer(page, '.input-area')
  check(p2.textareaVisible, '输入过程中输入框仍完整可见')
  check(p2.inputBottom <= p2.vh, '输入过程中输入区仍不越界', `底边 ${p2.inputBottom} / ${p2.vh}`)
  await page.screenshot({ path: `${OUT}/04-chat-typed.png` })

  log('5) 工作台同样检查')
  for (const h of [600, 800]) {
    await page.setViewportSize({ width: 1280, height: h })
    await page.goto(`${BASE}/work`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2500)
    const p = await probeComposer(page, '.composer-box')
    check(p.inputBottom <= p.vh, `工作台 ${h}px：输入区不越界`, `底边 ${p.inputBottom} / ${p.vh}`)
    check(p.hitInside !== false, `工作台 ${h}px：输入框中心未被遮挡`, p.hitDesc)
  }

  log('6) 侧边栏页脚（模型设置入口）不能被挤出视口')
  for (const [label, url] of [['对话页', '/chat'], ['工作台', '/work'], ['模型设置', '/models']]) {
    await page.setViewportSize({ width: 1280, height: 600 })
    await page.goto(BASE + url, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2500)
    const r = await page.evaluate(() => {
      const side = document.querySelector('aside.sidebar')
      if (!side) return null
      const footer = side.querySelector('.sidebar-footer')
      const sr = side.getBoundingClientRect()
      const fr = footer ? footer.getBoundingClientRect() : null
      return {
        vh: window.innerHeight,
        sideBottom: Math.round(sr.bottom),
        footerBottom: fr ? Math.round(fr.bottom) : null,
        footerTop: fr ? Math.round(fr.top) : null,
      }
    })
    if (r) {
      check(r.sideBottom <= r.vh, `${label}：侧栏不超出视口`, `底 ${r.sideBottom} / ${r.vh}`)
      if (r.footerBottom !== null) {
        check(r.footerBottom <= r.vh, `${label}：侧栏页脚完整可见`, `底 ${r.footerBottom} / ${r.vh}`)
      }
    }
  }

  log('')
  log('=== 控制台错误 ===')
  // 429 是限流：本脚本会导航多次，被限流是测试自己造成的，与被测的布局无关
  const realErrors = consoleErrors.filter(e => !e.includes('429'))
  const limited = consoleErrors.length - realErrors.length
  if (limited) log(`  （忽略 ${limited} 条 429 限流，属测试自身反复导航所致）`)
  log(realErrors.length ? realErrors.map(e => '  - ' + e).join('\n') : '（无真实错误）')
  if (realErrors.length) fails.push('控制台有错误')
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
} finally {
  await browser.close()
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
