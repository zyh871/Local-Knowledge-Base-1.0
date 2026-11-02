/**
 * 验证「点击下载按钮」这条真实用户路径。
 *
 * 只看 curl 能拿到文件还不够：页面里 `<a href>` 是否指向正确、`download` 属性
 * 是否生效、按钮会不会因为 HEAD 探测失败而被降级，都得在浏览器里跑一遍。
 */

import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const URL_ = 'https://zhijing-ai-landing.app.workbuddy.host/?sharecode=32WW4WGSC5'

const browser = await chromium.launch({ channel: 'msedge' })
// 全新技术上下文 = 不带任何 cookie / 登录态，模拟「别人第一次打开」
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const page = await ctx.newPage()

const errors = []
page.on('pageerror', (e) => errors.push(e.message))

const resp = await page.goto(URL_, { waitUntil: 'load', timeout: 60000 })
await page.waitForTimeout(1200)

console.log('页面状态:', resp.status(), '| 大小:', (await resp.body()).length)

// 按钮应当已经由脚本改写为真实文件地址
const hrefs = await page.evaluate(() =>
  [...document.querySelectorAll('[data-download]')].map((el) => ({
    text: el.textContent.trim().slice(0, 12),
    key: el.getAttribute('data-download') || '(默认)',
    href: el.getAttribute('href'),
    download: el.hasAttribute('download'),
  }))
)
console.log('下载按钮:')
hrefs.forEach((h) => console.log(`  ${h.key.padEnd(6)} ${h.download ? 'download ' : '         '} ${h.href}  <- ${h.text}`))

// 真的点一次 Windows 那个按钮
await page.locator('#download').scrollIntoViewIfNeeded()
await page.waitForTimeout(400)

const [download] = await Promise.all([
  page.waitForEvent('download', { timeout: 30000 }),
  page.locator('a[data-download="win"]').first().click(),
])

console.log('')
console.log('触发下载:', download.suggestedFilename())
const path = await download.path()
const buf = readFileSync(path)
const { createHash } = await import('node:crypto')
console.log('下载字节:', buf.length)
console.log('sha256  :', createHash('sha256').update(buf).digest('hex'))

await page.screenshot({ path: `${shots}/online-download.png` })
await browser.close()

const ok =
  resp.status() === 200 &&
  hrefs.every((h) => h.href && h.href.startsWith('download/') && h.download) &&
  buf.length > 200000 &&
  errors.length === 0

console.log('')
console.log(ok ? '结果：下载可用 ✅' : '结果：仍有问题 ❌')
if (!ok) process.exitCode = 1
