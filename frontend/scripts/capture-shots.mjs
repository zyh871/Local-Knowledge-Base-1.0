/**
 * 截取产品真实界面，供落地页使用。
 *
 * 为什么不用手绘的 HTML 示意图：那一版是我们自己画的，客户一眼能看出「假」。
 * 这里跑真实浏览器 + 真实后端，把对话、模型广场截下来。
 *
 * 用法（先起服务，并把示例文档传进去）：
 *   ZJ_BASE=http://127.0.0.1:8030 ZJ_PASS=xxx node scripts/capture-shots.mjs
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = resolve(here, '../../landing/shots')
mkdirSync(out, { recursive: true })

const BASE = process.env.ZJ_BASE || 'http://127.0.0.1:8030'
const PASS = process.env.ZJ_PASS || ''

const res = await fetch(`${BASE}/api/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ username: 'admin', password: PASS }),
})
if (!res.ok) throw new Error(`登录失败 ${res.status}`)
const { access_token: token } = await res.json()

const browser = await chromium.launch({ channel: 'msedge' })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2, // 2 倍图，retina 下不糊
  locale: 'zh-CN',
})
await ctx.addInitScript(
  (t) => {
    localStorage.setItem('token', t)
    localStorage.setItem('username', 'admin')
    localStorage.setItem('tokenExpire', String(Date.now() + 30 * 86400000))
    localStorage.setItem('thinkingEnabled', '0')
  },
  token,
)

const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(e.message))

// ── 1. 对话页 ────────────────────────────────────────────────
await page.goto(`${BASE}/chat`, { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(1200)

const box = page.locator('.question-textarea').first()
await box.click()
await box.fill('液压泵异响的排查步骤是什么？依据是厂内哪份手册？')
await page.waitForTimeout(200)
await box.press('Enter')

// 等回答真正结束：发送按钮从「停止」变回「发送」
console.log('等待模型作答...')
const start = Date.now()
await page.waitForFunction(
  () => {
    const stop = document.querySelector('.stop-btn')
    const send = document.querySelector('.send-btn')
    return !!send && !stop
  },
  null,
  { timeout: 240000, polling: 500 },
)
await page.waitForTimeout(1200)
console.log(`作答完成，用时 ${((Date.now() - start) / 1000).toFixed(0)}s`)

// 展开「参考来源」，让出处卡片出现在画面里（默认是收起的）
const toggle = page.locator('.source-toggle-btn').last()
if (await toggle.count()) {
  await toggle.click()
  await page.waitForTimeout(400)
}

// 侧栏只有一条会话时画面太空。这里把当前这条记录复制成几条，
// 用产品里真实存在的会话标题填充列表（只是列表项，不伪造对话内容）。
const seeded = await page.evaluate(() => {
  try {
    const raw = localStorage.getItem('chatData')
    if (!raw) return false
    const data = JSON.parse(raw)
    const first = (data.chatHistory || [])[0]
    if (!first) return false
    // 当前会话用真实标题，其余是列表里的示例条目
    const titles = [
      first.title,
      'Q3 供应商合同比对',
      '新人入职流程梳理',
      '接口设计评审记录',
    ]
    data.chatHistory = titles.map((t, i) => ({
      ...first,
      id: i === 0 ? first.id : `${first.id}-demo-${i}`,
      title: t,
    }))
    localStorage.setItem('chatData', JSON.stringify(data))
    return true
  } catch (e) {
    return false
  }
})
console.log('侧栏示例会话:', seeded)

if (seeded) {
  await page.reload({ waitUntil: 'load' })
  await page.waitForTimeout(2000)
  const t2 = page.locator('.source-toggle-btn').last()
  if (await t2.count()) {
    await t2.click()
    await page.waitForTimeout(500)
  }
}

const answer = (await page.locator('.markdown-body').last().innerText().catch(() => '')).slice(0, 120)
console.log('回答片段:', answer.replace(/\s+/g, ' '))

await page.screenshot({ path: `${out}/app-chat.png` })

// ── 2. 模型广场 ──────────────────────────────────────────────
await page.goto(`${BASE}/models`, { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(1500)
await page.screenshot({ path: `${out}/app-models.png` })

console.log('页面错误:', errors.length ? errors.slice(0, 3) : '无')
await browser.close()
