/**
 * 校验落地页改动：真实截图是否加载、三个按钮的跳转属性、以及两页在
 * 桌面 / 平板 / 手机三档下有没有横向溢出。
 *
 * 用法：node scripts/verify-landing-v2.mjs [baseUrl]
 */

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../../landing/_shots')
mkdirSync(shots, { recursive: true })

const BASE = process.argv[2] || 'http://127.0.0.1:8040'
// 线上站点需要 sharecode 才放行；本地静态服务没有这个参数
const QUERY = process.argv[3] || ''
const GH = 'https://github.com/zyh871/Local-Knowledge-Base-1.0.git'

const browser = await chromium.launch({ channel: 'msedge' })
const problems = []

async function openPage(width, height, path) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  const resp = await page.goto(`${BASE}${path}${QUERY}`, { waitUntil: 'load', timeout: 30000 })
  await page.waitForTimeout(600)
  return { ctx, page, resp, errors }
}

// ── 首页 ────────────────────────────────────────────────────
for (const [w, h, tag] of [
  [1440, 900, 'desktop'],
  [820, 1000, 'tablet'],
  [390, 844, 'mobile'],
]) {
  const { ctx, page, resp, errors } = await openPage(w, h, '/index.html')

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  if (overflow > 1) problems.push(`[${tag}] 首页横向溢出 ${overflow}px`)
  if (resp.status() !== 200) problems.push(`[${tag}] 首页状态 ${resp.status()}`)
  if (errors.length) problems.push(`[${tag}] 控制台错误: ${errors[0]}`)

  if (tag === 'desktop') {
    // 截图是否真的加载
    const img = await page.evaluate(() => {
      const el = document.querySelector('.shot img')
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { ok: el.complete && el.naturalWidth > 0, nw: el.naturalWidth, w: Math.round(r.width) }
    })
    if (!img) problems.push('首页找不到 .shot img')
    else if (!img.ok) problems.push('首页截图未加载')
    else console.log(`截图: ${img.nw}px 原图，显示宽 ${img.w}px`)

    // 三个按钮
    const btns = await page.evaluate(() =>
      [...document.querySelectorAll('.hero-actions .btn')].map((a) => ({
        text: a.textContent.trim(),
        href: a.getAttribute('href'),
        target: a.getAttribute('target'),
        rel: a.getAttribute('rel'),
      })),
    )
    console.log('主按钮:', btns.map((b) => b.text).join(' / '))
    const gh = btns.find((b) => b.text.includes('GitHub'))
    if (!gh) problems.push('找不到 查看 GitHub 按钮')
    else {
      if (gh.href !== GH) problems.push(`GitHub 链接不对: ${gh.href}`)
      if (gh.target !== '_blank') problems.push('GitHub 按钮缺少 target=_blank')
      if (!(gh.rel || '').includes('noopener')) problems.push('GitHub 按钮缺少 rel=noopener')
    }
    const dev = btns.find((b) => b.text.includes('开发者文档'))
    if (!dev) problems.push('找不到 开发者文档 按钮')
    else if (!dev.href.endsWith('developers.html')) problems.push(`开发者文档链接不对: ${dev.href}`)

    const hero = await page.locator('.hero').boundingBox()
    await page.screenshot({
      path: `${shots}/v2-hero-desktop.png`,
      clip: { x: 0, y: 0, width: 1440, height: Math.min(900, hero.height) },
    })
  } else {
    await page.screenshot({ path: `${shots}/v2-home-${tag}.png` })
  }

  await ctx.close()
}

// ── 开发者页 ────────────────────────────────────────────────
for (const [w, h, tag] of [
  [1440, 900, 'desktop'],
  [820, 1000, 'tablet'],
  [390, 844, 'mobile'],
]) {
  const { ctx, page, resp, errors } = await openPage(w, h, '/developers.html')

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  if (overflow > 1) problems.push(`[${tag}] 开发者页横向溢出 ${overflow}px`)
  if (resp.status() !== 200) problems.push(`[${tag}] 开发者页状态 ${resp.status()}`)
  if (errors.length) problems.push(`[${tag}] 开发者页错误: ${errors[0]}`)

  if (tag === 'desktop') {
    const info = await page.evaluate(() => ({
      h1: document.querySelector('h1')?.textContent.trim(),
      sections: [...document.querySelectorAll('.doc section')].map((s) => s.id),
      tocVisible: getComputedStyle(document.querySelector('.toc')).display !== 'none',
      ghLinks: [...document.querySelectorAll('a[href*="github.com/zyh871"]')].length,
    }))
    console.log('开发者页:', info.h1, '| 章节', info.sections.join(','), '| 目录', info.tocVisible, '| GitHub 链接', info.ghLinks)
    if (info.sections.length < 6) problems.push('开发者页章节过少')
    if (!info.tocVisible) problems.push('桌面端目录未显示')
    if (info.ghLinks < 3) problems.push('开发者页 GitHub 入口偏少')
    await page.screenshot({ path: `${shots}/v2-dev-desktop.png`, fullPage: false })
  } else if (tag === 'mobile') {
    const tocHidden = await page.evaluate(
      () => getComputedStyle(document.querySelector('.toc')).display === 'none',
    )
    if (!tocHidden) problems.push('移动端目录未隐藏')
    await page.screenshot({ path: `${shots}/v2-dev-mobile.png`, fullPage: false })
    // 抽屉
    await page.click('#navToggle')
    await page.waitForTimeout(300)
    const open = await page.evaluate(() => document.getElementById('drawer').dataset.open)
    if (open !== 'true') problems.push('移动端抽屉打不开')
  } else {
    await page.screenshot({ path: `${shots}/v2-dev-tablet.png` })
  }

  await ctx.close()
}

await browser.close()

console.log('')
if (problems.length) {
  console.log('发现问题:')
  problems.forEach((p) => console.log('  -', p))
  process.exitCode = 1
} else {
  console.log('全部通过 ✅')
}
