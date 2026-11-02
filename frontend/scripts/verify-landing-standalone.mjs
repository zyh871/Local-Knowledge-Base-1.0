/**
 * 独立部署验证：证明 index.html 脱离原项目后仍然可用。
 *
 * 做法：
 *   1. 把 index.html 复制到一个全新的空目录（模拟"只拿到这一个文件"）
 *   2. 从那个目录以 file:// 打开，并拦截一切非 file 请求
 *   3. 断言：外部请求数 = 0、logo 背景图已内联、三档视口无横向溢出
 */

import { chromium } from 'playwright'
import { mkdtempSync, copyFileSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = resolve(here, '../../landing/index.html')

// 1. 隔离目录：只有这一个文件
const sandbox = mkdtempSync(join(tmpdir(), 'zhijing-standalone-'))
copyFileSync(source, join(sandbox, 'index.html'))
const listing = readdirSync(sandbox)
console.log('隔离目录内容:', listing.join(', '))

const fileUrl = 'file:///' + join(sandbox, 'index.html').replace(/\\/g, '/')

const browser = await chromium.launch({ channel: 'msedge' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const external = []
const failed = []

await page.route('**/*', (route) => {
  const url = route.request().url()
  if (url.startsWith('file://') || url.startsWith('data:')) return route.continue()
  external.push(url)
  return route.abort()
})

page.on('requestfailed', (req) => {
  if (!req.url().startsWith('file://')) failed.push(req.url())
})

await page.goto(fileUrl, { waitUntil: 'load' })
await page.waitForTimeout(600)

const report = await page.evaluate(() => {
  const logos = [...document.querySelectorAll('.logo')]
  const bg = logos.map((el) => getComputedStyle(el).backgroundImage)
  const box = logos.map((el) => {
    const r = el.getBoundingClientRect()
    return { w: Math.round(r.width), h: Math.round(r.height) }
  })
  return {
    logoCount: logos.length,
    allInline: bg.every((v) => v.startsWith('url("data:image/png;base64,')),
    sizes: box,
    viewport: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }
})

console.log('外部请求:', external.length, external.slice(0, 5))
console.log('失败请求:', failed.length, failed.slice(0, 5))
console.log('logo:', JSON.stringify(report))

// 三档视口再测一遍溢出
for (const [w, h] of [[1440, 900], [834, 1112], [390, 844]]) {
  await page.setViewportSize({ width: w, height: h })
  await page.waitForTimeout(250)
  const o = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  )
  console.log(`  ${w}px 横向溢出: ${o}px`)
}

await page.setViewportSize({ width: 1440, height: 900 })
await page.waitForTimeout(300)
await page.screenshot({ path: join(sandbox, 'proof.png') })

await browser.close()

const ok =
  listing.length === 1 &&
  external.length === 0 &&
  report.allInline &&
  report.logoCount === 3 &&
  report.overflow === 0

console.log('')
console.log(ok ? '结果：单文件独立可运行 ✅' : '结果：仍有依赖，需要修复 ❌')
if (!ok) process.exitCode = 1
