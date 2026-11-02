/**
 * P3 端到端验证：团队空间 + 开源社区。
 *
 * 用真实浏览器（系统 Edge）跑一遍主链路：
 *   团队：创建 → 看成员/权限 → 生成邀请链接
 *   社区：创建项目（草稿）→ 发布 → Star → 评论 → 回列表
 *
 * 令牌由 `backend/scripts/mint_verify_token.py` 预置到 `_verify_auth.json`，
 * 这里注入 localStorage，避免脚本里出现密码。
 */

import { chromium } from 'playwright'
import { readFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const shots = resolve(here, '../_shots')
mkdirSync(shots, { recursive: true })

const auth = JSON.parse(
  readFileSync(resolve(here, '../../backend/_verify_auth.json'), 'utf-8')
)

const errors = []
const stamp = Date.now().toString().slice(-5)
const TEAM = `验证组-${stamp}`
const PROJECT = `智鲸验证项目-${stamp}`

const browser = await chromium.launch({ channel: 'msedge' })
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
})

await context.addInitScript(
  ([token, username]) => {
    localStorage.setItem('token', token)
    localStorage.setItem('username', username)
    localStorage.setItem('tokenExpire', String(Date.now() + 2 * 3600 * 1000))
  },
  [auth.token, auth.username]
)

const page = await context.newPage()
page.on('console', (msg) => {
  if (msg.type() === 'error') errors.push(msg.text())
})
page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`))

const step = async (name, fn) => {
  try {
    await fn()
    console.log(`  ok   ${name}`)
  } catch (e) {
    console.log(`  FAIL ${name}: ${e.message}`)
    throw e
  }
}

/* ── 1. 团队空间 ─────────────────────────────────────── */
console.log('团队空间')

await page.goto('http://localhost:5173/teams', { waitUntil: 'networkidle' })
await step('侧栏出现「团队空间 / 开源社区」入口', async () => {
  await page.locator('.side-link', { hasText: '团队空间' }).waitFor({ timeout: 8000 })
  await page.locator('.side-link', { hasText: '开源社区' }).waitFor()
})
await page.screenshot({ path: `${shots}/01-teams-list.png` })

await step('创建团队', async () => {
  await page.getByRole('button', { name: '创建团队' }).click()
  await page.locator('.modal input').first().fill(TEAM)
  await page.locator('.modal textarea').fill('P3 端到端验证用')
  await page.locator('.modal .btn-primary').click()
  await page.locator('.card-title', { hasText: '基本信息' }).waitFor({ timeout: 10000 })
  await page.locator('.page-title', { hasText: TEAM }).or(
    page.locator('.team-name', { hasText: TEAM })
  ).first().waitFor()
})

await step('成员与权限已加载', async () => {
  await page.locator('.cap', { hasText: '管理成员' }).waitFor({ timeout: 8000 })
})
await page.screenshot({ path: `${shots}/02-team-detail.png` })

await step('生成邀请链接', async () => {
  await page.getByRole('button', { name: '生成链接' }).click()
  await page.locator('.link-box code').waitFor({ timeout: 8000 })
  const link = await page.locator('.link-box code').innerText()
  if (!link.includes('/invite/')) throw new Error(`邀请链接格式不对: ${link}`)
})
await page.screenshot({ path: `${shots}/03-invite-link.png` })

/* ── 2. 开源社区 ─────────────────────────────────────── */
console.log('开源社区')

await page.goto('http://localhost:5173/community', { waitUntil: 'networkidle' })
await step('社区页加载', async () => {
  await page.locator('.page-title', { hasText: '开源社区' }).waitFor({ timeout: 8000 })
  await page.locator('.tab', { hasText: '我的项目' }).waitFor()
})
await page.screenshot({ path: `${shots}/04-community-list.png` })

await step('创建项目（草稿）', async () => {
  await page.getByRole('button', { name: '创建项目' }).click()
  // 弹窗里 input 顺序：0 项目名称 / 1 简介 / 2 标签 / 3 仓库地址（团队与语言是 select）
  await page.locator('.modal input').nth(0).fill(PROJECT)
  await page.locator('.modal input').nth(1).fill('P3 端到端验证项目')
  await page.locator('.modal .btn-primary').click()
  await page.locator('.detail-title .page-title').waitFor({ timeout: 10000 })
  const text = await page.locator('.detail-title .pill').innerText()
  if (!text.includes('草稿')) throw new Error(`新建项目应为草稿，实际: ${text}`)
})
await page.screenshot({ path: `${shots}/05-project-draft.png` })

await step('发布到社区', async () => {
  await page.getByRole('button', { name: '发布到社区' }).click()
  await page.waitForFunction(
    () => document.querySelector('.detail-title .pill')?.textContent?.includes('已发布'),
    null,
    { timeout: 10000 }
  )
})
await page.screenshot({ path: `${shots}/06-project-published.png` })

await step('Star 计数 +1', async () => {
  const btn = page.locator('.action-row .btn-ghost', { hasText: 'Star' }).first()
  const before = Number((await btn.innerText()).replace(/\D+/g, '')) || 0
  await btn.click()
  await page.waitForFunction(
    (n) => {
      const el = document.querySelector('.action-row .btn-ghost')
      return el && (Number(el.textContent.replace(/\D+/g, '')) || 0) === n + 1
    },
    before,
    { timeout: 10000 }
  )
})

await step('发表评论', async () => {
  await page.locator('.comment-form textarea').fill('端到端验证评论')
  await page.locator('.comment-form .btn-primary').click()
  await page.locator('.comment-text', { hasText: '端到端验证评论' }).waitFor({ timeout: 10000 })
})
await page.screenshot({ path: `${shots}/07-star-comment.png` })

await step('返回列表并看到已发布卡片', async () => {
  await page.getByRole('button', { name: '返回列表' }).click()
  await page.locator('.project-card', { hasText: PROJECT }).waitFor({ timeout: 10000 })
})
await page.screenshot({ path: `${shots}/08-community-grid.png` })

await step('「我的项目」含草稿/发布两个视图', async () => {
  await page.locator('.tab', { hasText: '我的项目' }).click()
  await page.locator('.project-card', { hasText: PROJECT }).waitFor({ timeout: 10000 })
})
await page.screenshot({ path: `${shots}/09-my-projects.png` })

/* ── 3. 深色主题下的可读性 ───────────────────────────── */
console.log('深色主题')

await step('深色主题侧栏入口可读', async () => {
  // 走应用自己的主题开关：直接加 class 会造出 `light dark` 并存的人造状态，
  // 那种状态下主题变量取值没有参考意义。
  await page.evaluate(() => localStorage.setItem('theme', 'dark'))
  await page.reload({ waitUntil: 'networkidle' })
  const cls = await page.evaluate(() => document.documentElement.className)
  if (cls !== 'dark') throw new Error(`主题未切到 dark，实际: "${cls}"`)
  await page.locator('.side-link', { hasText: '团队空间' }).hover()
  await page.screenshot({ path: `${shots}/10-dark-sidebar.png` })
})

await browser.close()

const real = errors.filter(
  (e) => !/favicon|ResizeObserver|Download the Vue Devtools/i.test(e)
)
console.log('')
if (real.length) {
  console.log(`控制台错误 ${real.length} 条：`)
  real.slice(0, 10).forEach((e) => console.log(`  - ${e}`))
  process.exitCode = 1
} else {
  console.log('控制台无错误')
}
