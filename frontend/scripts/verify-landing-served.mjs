/**
 * 验证「安装后的本地服务」这条路径：从 http:// 打开页面，页面应完整渲染。
 * 与 file:// 相比，这里额外确认 Content-Type 正确（否则浏览器会把页面当文件下载）。
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const url = process.argv[2] || 'http://127.0.0.1:8099/'

const browser = await chromium.launch({ channel: 'msedge' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

const errors = []
page.on('pageerror', (e) => errors.push(e.message))

const resp = await page.goto(url, { waitUntil: 'load' })
await page.waitForTimeout(700)

const info = await page.evaluate(() => ({
  title: document.title,
  h1: document.querySelector('h1')?.innerText.replace(/\n/g, ' / '),
  logos: document.querySelectorAll('.logo').length,
  sections: ['why', 'features', 'scenes', 'download', 'requirements', 'faq'].filter((id) =>
    document.getElementById(id)
  ).length,
  overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
}))

console.log('HTTP 状态:', resp.status(), '| Content-Type:', resp.headers()['content-type'])
console.log('页面:', JSON.stringify(info))
console.log('脚本错误:', errors.length ? errors : '无')

await page.screenshot({ path: `${shots}/served-http.png` })
await browser.close()

const ok =
  resp.status() === 200 &&
  /text\/html/.test(resp.headers()['content-type'] || '') &&
  info.logos === 3 &&
  info.sections === 6 &&
  info.overflow === 0 &&
  errors.length === 0

console.log(ok ? '结果：本地服务正常 ✅' : '结果：异常 ❌')
if (!ok) process.exitCode = 1
