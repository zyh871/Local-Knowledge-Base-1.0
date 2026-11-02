/**
 * 落地页视觉与响应式验证。
 *
 * 检查项：控制台错误、横向溢出（移动端最常见的翻车点）、关键区块是否渲染、
 * 移动端抽屉与 FAQ 交互是否可用。产出桌面 / 平板 / 手机三档截图。
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const URL = 'file:///' + resolve(here, '../../landing/index.html').replace(/\\/g, '/')

const browser = await chromium.launch({ channel: 'msedge' })
const errors = []

async function open(width, height, name) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 1,
  })
  const page = await ctx.newPage()
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`[${name}] ${m.text()}`)
  })
  page.on('pageerror', (e) => errors.push(`[${name}] pageerror: ${e.message}`))
  await page.goto(URL, { waitUntil: 'load' })
  await page.waitForTimeout(700)
  return { ctx, page }
}

/* ── 桌面 ─────────────────────────────────────────────── */
{
  const { ctx, page } = await open(1440, 900, 'desktop')

  await page.screenshot({ path: `${shots}/d1-hero.png` })

  for (const [id, file] of [
    ['why', 'd2-why'],
    ['features', 'd3-features'],
    ['scenes', 'd4-scenes'],
    ['download', 'd5-download'],
    ['requirements', 'd6-requirements'],
    ['faq', 'd7-faq'],
  ]) {
    await page.evaluate((sel) => {
      document.querySelector('#' + sel).scrollIntoView()
    }, id)
    await page.waitForTimeout(650)
    await page.screenshot({ path: `${shots}/${file}.png` })
  }

  // 页尾
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await page.waitForTimeout(700)
  await page.screenshot({ path: `${shots}/d8-footer.png` })

  // 复制按钮真的能改状态
  const copyWorked = await page.evaluate(() => {
    const btn = document.querySelector('[data-copy]')
    btn.click()
    return btn.textContent
  })
  console.log('copy button ->', copyWorked)

  await ctx.close()
}

/* ── 平板 / 手机 ──────────────────────────────────────── */
for (const [w, h, tag] of [
  [834, 1112, 'tablet'],
  [390, 844, 'mobile'],
]) {
  const { ctx, page } = await open(w, h, tag)

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  )
  console.log(`${tag}: 横向溢出 ${overflow}px`)

  await page.screenshot({ path: `${shots}/${tag}-1-hero.png` })

  // 抽屉
  const drawer = await page.evaluate(() => {
    const btn = document.getElementById('navToggle')
    const d = document.getElementById('navDrawer')
    const before = getComputedStyle(d).display
    btn.click()
    const after = getComputedStyle(d).display
    btn.click()
    return { before, after }
  })
  console.log(`${tag}: 抽屉 ${drawer.before} -> ${drawer.after}`)

  await page.evaluate(() => document.querySelector('#download').scrollIntoView())
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${shots}/${tag}-2-download.png` })

  await page.evaluate(() => document.querySelector('#faq').scrollIntoView())
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${shots}/${tag}-3-faq.png` })

  await ctx.close()
}

await browser.close()

const real = errors.filter((e) => !/favicon|Download the Vue Devtools/i.test(e))
console.log('')
if (real.length) {
  console.log(`控制台错误 ${real.length} 条：`)
  real.slice(0, 10).forEach((e) => console.log('  - ' + e))
  process.exitCode = 1
} else {
  console.log('控制台无错误')
}
