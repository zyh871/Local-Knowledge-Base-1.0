/**
 * 模型切换控件的界面验证（用系统 Edge，无需下载浏览器）。
 *
 * 用法：node scripts/verify_model_picker.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5199'
const OUT = process.argv[3] || '.verify-shots'
mkdirSync(OUT, { recursive: true })

const log = (...a) => console.log(...a)

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })

const failed = []
page.on('console', m => {
  if (m.type() === 'error') failed.push(m.text())
})
page.on('pageerror', e => failed.push(String(e)))

try {
  log('1) 打开', BASE)
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)

  // ---- 登录 ----
  if (await page.locator('input[type="password"]').count()) {
    log('2) 登录')
    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    // 按钮文案是「登 录」（中间有空格），用 has-text 子串匹配会失败
    await page.locator('form button, .login-card button, button').first().click()
    await page.waitForTimeout(3500)
  }
  await page.waitForSelector('.input-area', { timeout: 25000 })
  log('   已进入对话页, URL =', page.url())

  // ---- 模型控件存在性 ----
  const pill = page.locator('.model-picker .model-pill')
  await pill.waitFor({ timeout: 10000 })
  const pillText = (await pill.innerText()).trim()
  log('3) 当前模型控件文本 =', JSON.stringify(pillText))
  await page.screenshot({ path: `${OUT}/01-input-area.png`, clip: await clipOf(page, '.input-area') })
  log('   截图 01-input-area.png')

  // ---- 展开下拉 ----
  await pill.click()
  await page.waitForSelector('.model-menu', { timeout: 5000 })
  const options = await page.locator('.model-option').allInnerTexts()
  log('4) 下拉候选数 =', options.length)
  options.forEach((o, i) => log(`   [${i}] ${o.replace(/\n/g, ' | ')}`))
  // 菜单向上展开，截图要往上多留空间
  await page.screenshot({
    path: `${OUT}/02-menu-open.png`,
    clip: await clipOf(page, '.input-area', { topExtra: 320 })
  })
  log('   截图 02-menu-open.png')

  // ---- 切换到第二个模型 ----
  if (options.length > 1) {
    const target = options[1].split('\n')[0].trim()
    await page.locator('.model-option').nth(1).click()
    await page.waitForTimeout(600)
    const after = (await pill.innerText()).trim()
    log(`5) 切换到「${target}」后控件文本 =`, JSON.stringify(after))
    log('   菜单是否已关闭 =', (await page.locator('.model-menu').count()) === 0)
    const stored = await page.evaluate(() => localStorage.getItem('selectedModel'))
    log('   localStorage.selectedModel =', JSON.stringify(stored))
    await page.screenshot({ path: `${OUT}/03-model-switched.png`, clip: await clipOf(page, '.input-area') })

    // ---- 关键一步：切换后立即发一条消息，验证请求真的带上了新模型 ----
    log('6) 验证切换对后续请求立即生效')
    let sentBody = null
    let finalEvent = null

    page.on('request', req => {
      if (req.url().includes('/ask/stream')) {
        try { sentBody = JSON.parse(req.postData() || '{}') } catch { /* ignore */ }
      }
    })
    page.on('response', async res => {
      if (!res.url().includes('/ask/stream')) return
      try {
        const text = await res.text()
        const finals = text
          .split('\n')
          .filter(l => l.startsWith('data: '))
          .map(l => JSON.parse(l.slice(6)))
          .filter(e => e.type === 'final')
        if (finals.length) finalEvent = finals[finals.length - 1].content
      } catch { /* ignore */ }
    })

    await page.fill('textarea', '你好')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(25000)

    log('   请求体 model        =', JSON.stringify(sentBody?.model ?? '(未捕获)'))
    log('   final 事件回显 model =', JSON.stringify(finalEvent?.model ?? '(未捕获)'))
    log('   final.answer 长度    =', finalEvent?.answer ? finalEvent.answer.length : '(未捕获)')
    log('   页面出现回答        =', (await page.locator('.markdown-body').count()) > 0)
    await page.screenshot({ path: `${OUT}/04-after-send.png`, clip: await clipOf(page, '.input-area', { topExtra: 420 }) })
    log('   截图 04-after-send.png')

    if (sentBody?.model !== 'glm-4-air') {
      log('   !! 断言失败：请求体未带上切换后的模型')
      process.exitCode = 1
    } else {
      log('   ✓ 请求体已带上切换后的模型 glm-4-air')
    }
  }

  log('')
  log('控制台错误 =', failed.length ? failed.slice(0, 5) : '无')
} catch (e) {
  log('!! 失败:', e.message)
  await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true }).catch(() => {})
  process.exitCode = 1
} finally {
  await browser.close()
}

async function clipOf(page, sel, { topExtra = 0 } = {}) {
  const box = await page.locator(sel).boundingBox()
  if (!box) return undefined
  const y = Math.max(0, box.y - 10 - topExtra)
  return {
    x: Math.max(0, box.x - 10),
    y,
    width: Math.min(1280, box.width + 20),
    height: Math.min(900 - y, box.height + 20 + topExtra)
  }
}
