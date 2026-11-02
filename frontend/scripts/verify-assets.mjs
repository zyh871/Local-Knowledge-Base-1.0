/**
 * 验证创作页「资产库删除」与侧栏白底 logo。
 *
 * 检查项：
 *   1. 资产卡片不再是 <button>（里面要能放删除按钮）
 *   2. 悬停时删除按钮显形，点击后弹确认框
 *   3. 确认后卡片消失、接口条数减 1
 *   4. 取消删除时什么都不会发生
 *   5. 侧栏 logo 用的是白底版（zhijing-logo-white）
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const BASE = process.env.ZJ_BASE || 'http://127.0.0.1:8060'
const PASS = process.env.ZJ_PASS || ''

const res = await fetch(`${BASE}/api/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: new URLSearchParams({ username: 'admin', password: PASS }),
})
if (!res.ok) throw new Error(`登录失败 ${res.status}`)
const { access_token: token } = await res.json()
const auth = { Authorization: `Bearer ${token}` }

const countAssets = async () => {
  const r = await fetch(`${BASE}/api/creations?limit=30`, { headers: auth })
  const d = await r.json()
  return (d.creations || []).length
}

const before = await countAssets()
console.log('删除前资产条数:', before)

const browser = await chromium.launch({ channel: 'msedge' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
await ctx.addInitScript(
  (t) => {
    localStorage.setItem('token', t)
    localStorage.setItem('username', 'admin')
    localStorage.setItem('tokenExpire', String(Date.now() + 30 * 86400000))
  },
  token,
)
const page = await ctx.newPage()
const problems = []
page.on('pageerror', (e) => problems.push(e.message))

await page.goto(`${BASE}/create`, { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(2500)

// 1. 卡片结构
const isButton = await page.evaluate(() => {
  const el = document.querySelector('.asset-card')
  return el ? el.tagName : null
})
console.log('资产卡片标签:', isButton)
if (isButton === 'BUTTON') problems.push('资产卡仍是 <button>，删除按钮会嵌套非法')

const cards = await page.locator('.asset-card').count()
console.log('当前页可见卡片:', cards)
if (!cards) problems.push('资产库没有卡片，无法验证删除')

// 2. 悬停显形
const delBtn = page.locator('.asset-card .asset-del').first()
await page.locator('.asset-card').first().hover()
await page.waitForTimeout(300)
const opacity = await delBtn.evaluate((el) => getComputedStyle(el).opacity)
console.log('悬停后删除按钮 opacity:', opacity)
if (Number(opacity) < 0.9) problems.push('删除按钮悬停后没有显形')

await page.screenshot({ path: `${shots}/v3-asset-hover.png`, fullPage: false })

// 3. 取消一次
page.once('dialog', async (d) => {
  console.log('确认框文案:', d.message().replace(/\s+/g, ' ').slice(0, 60))
  await d.dismiss()
})
await delBtn.click()
await page.waitForTimeout(500)
const afterCancel = await page.locator('.asset-card').count()
console.log('取消后卡片数:', afterCancel)
if (afterCancel !== cards) problems.push('取消删除却改变了卡片数')

// 4. 真删
page.once('dialog', async (d) => d.accept())
await delBtn.click()
await page.waitForTimeout(1200)
const afterDelete = await page.locator('.asset-card').count()
const serverCount = await countAssets()
console.log('删除后卡片数:', afterDelete, '| 接口条数:', serverCount)
if (afterDelete !== cards - 1) problems.push(`卡片数没有减少（${cards} → ${afterDelete}）`)
if (serverCount !== before - 1) problems.push(`接口条数没有减少（${before} → ${serverCount}）`)

// 5. 侧栏 logo
const logo = await page.evaluate(() => {
  const img = document.querySelector('.brand-logo')
  if (!img) return null
  const cs = getComputedStyle(img)
  return { src: img.getAttribute('src'), radius: cs.borderRadius, shadow: cs.boxShadow }
})
if (!logo) problems.push('找不到侧栏 logo')
else {
  console.log('侧栏 logo:', logo.src?.split('/').pop(), '| 圆角', logo.radius)
  if (!/white/.test(logo.src || '')) problems.push('侧栏 logo 不是白底版')
}

await page.screenshot({ path: `${shots}/v3-create-light.png` })

// 深色主题下确认白底仍然可读
await page.evaluate(() => {
  document.documentElement.classList.add('dark')
  localStorage.setItem('theme', 'dark')
})
await page.waitForTimeout(400)
const darkBox = await page.locator('.brand-title').boundingBox()
await page.screenshot({
  path: `${shots}/v3-brand-dark.png`,
  clip: { x: darkBox.x - 8, y: darkBox.y - 8, width: 240, height: darkBox.height + 16 },
})

console.log('页面错误:', problems.length ? problems : '无')
await browser.close()

if (problems.length) {
  console.log('\n发现问题:')
  problems.forEach((p) => console.log('  -', p))
  process.exitCode = 1
} else {
  console.log('\n全部通过 ✅')
}
