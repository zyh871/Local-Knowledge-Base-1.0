/**
 * 对话页输入区截图：验证「深度思考 / 智能搜索」胶囊按钮的视觉与展开状态。
 *
 * 直接注入 token 跳过登录表单（token 从 /api/token 拿），这样截图稳定、不依赖
 * 表单选择器。深浅两版都截，确认配色在深色主题下也成立。
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const BASE = process.env.ZJ_BASE || 'http://127.0.0.1:8022'
const USER = process.env.ZJ_USER || 'admin'
const PASS = process.env.ZJ_PASS || ''

const res = await fetch(`${BASE}/api/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ username: USER, password: PASS }),
})
if (!res.ok) throw new Error(`登录失败 ${res.status}`)
const { access_token: token } = await res.json()

const browser = await chromium.launch({ channel: 'msedge' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await ctx.addInitScript(
  (t) => {
    localStorage.setItem('token', t)
    localStorage.setItem('username', 'admin')
    localStorage.setItem('tokenExpire', String(Date.now() + 86400000))
  },
  token,
)

const page = await ctx.newPage()
const errors = []
page.on('pageerror', (e) => errors.push(e.message))
await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle', timeout: 60000 })
await page.waitForTimeout(1500)

const pills = page.locator('.toggle-pill')
console.log('胶囊按钮数量:', await pills.count())
for (let i = 0; i < (await pills.count()); i++) {
  const el = pills.nth(i)
  console.log(
    `  ${(await el.innerText()).trim()}  开=${await el.evaluate((n) => n.classList.contains('on'))}`,
  )
}

const composer = page.locator('.quick-tools').first()
await composer.scrollIntoViewIfNeeded()
await page.waitForTimeout(300)
await page.screenshot({ path: `${shots}/chat-composer-light.png`, clip: await composer.boundingBox() })

// 点一下「智能搜索」，看开启态
await pills.nth(1).click()
await page.waitForTimeout(300)
const onClass = await pills.nth(1).evaluate((n) => n.classList.contains('on'))
console.log('点击后智能搜索 on =', onClass)

const box = await composer.boundingBox()
await page.screenshot({
  path: `${shots}/chat-composer-toggled.png`,
  clip: { x: box.x, y: box.y - 10, width: box.width, height: box.height + 30 },
})

console.log('页面错误:', errors.length ? errors.slice(0, 3) : '无')
await browser.close()
