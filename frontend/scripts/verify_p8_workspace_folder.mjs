/**
 * 界面验证：工作区绑定本地文件夹 + 从文件夹引用文件 + 空状态已去掉路线图。
 *
 * 刻意**不点「打开」按钮** —— 那会在你桌面上真的弹出资源管理器窗口。
 * 该调用的正确性由后端测试（mock Popen）覆盖，这里只断言入口存在。
 *
 * 用法：node scripts/verify_p8_workspace_folder.mjs [baseUrl] [outDir]
 */
import { chromium } from 'playwright-core'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { homedir, tmpdir } from 'node:os'

const BASE = process.argv[2] || 'http://localhost:5173'
const OUT = process.argv[3] || '.verify-shots-p8'
const WS_NAME = `绑文件夹-${Date.now().toString().slice(-6)}`
const SECRET_TEXT = '这是我本地早就有的资料：定价 0.42 元'
mkdirSync(OUT, { recursive: true })

// 造一个「用户本机的文件夹」。
// ⚠️ 刻意放在**主目录**下而不是系统临时目录：选择器里要从「主目录」点进去，
// 路径太深就点不到，那样这段交互就等于没验证。
const FOLDER_NAME = `kb-ws-test-${Date.now().toString().slice(-6)}`
const localDir = join(homedir(), FOLDER_NAME)
mkdirSync(join(localDir, '子目录'), { recursive: true })
writeFileSync(join(localDir, '本地已有资料.txt'), SECRET_TEXT, 'utf-8')

const log = (...a) => console.log(...a)
const fails = []
const check = (ok, label, extra = '') => {
  log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? '  → ' + extra : ''}`)
  if (!ok) fails.push(label)
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1400, height: 1100 } })
const consoleErrors = []
page.on('console', m => {
  if (m.type() === 'error') consoleErrors.push(m.text())
})
page.on('pageerror', e => consoleErrors.push(String(e)))

const openWsMenu = async () => {
  if ((await page.locator('.menu-wide').count()) === 0) {
    await page.locator('.composer-meta .composer-meta-btn').first().click()
    await page.waitForSelector('.menu-wide', { timeout: 5000 })
  }
}

try {
  log('1) 登录')
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  if (await page.locator('input[type="password"]').count()) {
    await page.fill('input[type="text"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.locator('form button, .login-card button, button').first().click()
    await page.waitForTimeout(3500)
  }
  await page.goto(`${BASE}/work`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('.composer-box', { timeout: 25000 })
  await page.waitForTimeout(1500)

  log('2) 空状态不应再有 P0–P7 路线图')
  const emptyText = await page.locator('.work-empty').innerText()
  check(!/\bP[0-7]\b/.test(emptyText), '已删除阶段路线图', emptyText.replace(/\s+/g, ' ').slice(0, 40))
  check(!emptyText.includes('后续阶段接入'), '已删除过期的说明文案')
  check(emptyText.includes('描述你要完成的事'), '空状态标题仍在')
  await page.screenshot({ path: `${OUT}/01-empty-state.png`, fullPage: true })

  log('3) 新建工作区')
  await openWsMenu()
  await page.locator('.menu-wide .menu-input').last().fill(WS_NAME)
  await page.locator('.menu-wide .mini-btn', { hasText: '新建' }).click()
  await page.waitForTimeout(1800)

  await openWsMenu()
  const folderHead = await page.locator('.menu-folder-head').innerText()
  check(folderHead.includes('未绑定'), '新工作区显示「未绑定」', folderHead.replace(/\s+/g, ' '))
  check(
    (await page.locator('.menu-folder .mini-btn', { hasText: '选择' }).count()) === 1,
    '提供「选择…」入口'
  )
  check(
    (await page.locator('.menu-folder .mini-btn', { hasText: '打开' }).count()) === 1,
    '提供「打开」入口'
  )
  await page.screenshot({ path: `${OUT}/02-menu-unbound.png`, fullPage: true })

  log('4) 文件夹选择器：从「主目录」点进目标文件夹并选择')
  await page.locator('.menu-folder .mini-btn', { hasText: '选择' }).click()
  await page.waitForSelector('.fp-panel', { timeout: 10000 })
  // 等首屏目录读完再断言（打开面板是瞬时的，请求是异步的）
  await page.waitForSelector('.fp-root', { timeout: 20000 })
  const pickerError = await page.locator('.fp-error').innerText().catch(() => '')
  check(!pickerError, '选择器没有报错', pickerError)
  check((await page.locator('.fp-root').count()) >= 1, '有起始位置（盘符/主目录）')
  check(
    (await page.locator('.fp-root', { hasText: '主目录' }).count()) === 1,
    '提供「主目录」快捷入口'
  )

  await page.locator('.fp-root', { hasText: '主目录' }).first().click()
  await page.waitForTimeout(1500)
  const homePath = await page.locator('.fp-path-text').innerText()
  check(!!homePath.trim(), '读取到主目录', homePath.slice(-30))
  await page.screenshot({ path: `${OUT}/03-folder-picker.png`, fullPage: true })

  const targetItem = page.locator('.fp-item', { hasText: FOLDER_NAME }).first()
  check((await targetItem.count()) === 1, '主目录里能找到测试文件夹', FOLDER_NAME)
  await targetItem.click()
  await page.waitForTimeout(1500)
  const insidePath = await page.locator('.fp-path-text').innerText()
  check(insidePath.includes(FOLDER_NAME), '进入到了目标文件夹', insidePath.slice(-30))

  // 文件夹模式下文件不可选（避免用户误点）
  const fileItem = page.locator('.fp-item.is-file').first()
  check(
    (await fileItem.count()) >= 1 && (await fileItem.isDisabled()),
    '文件夹模式下文件不可点选'
  )

  await page.locator('.mini-btn', { hasText: '选择此文件夹' }).click()
  await page.waitForTimeout(3000)

  await openWsMenu()
  const head2 = await page.locator('.menu-folder-head').innerText()
  check(head2.includes(FOLDER_NAME), '★ 绑定后菜单显示该路径', head2.replace(/\s+/g, ' ').slice(0, 60))
  check(
    (await page.locator('.menu-folder .mini-btn', { hasText: '更换' }).count()) === 1,
    '已绑定后入口变为「更换…」'
  )
  check(
    (await page.locator('.menu-folder .mini-btn', { hasText: '解绑' }).count()) === 1,
    '提供「解绑」入口'
  )
  await page.screenshot({ path: `${OUT}/04-menu-bound.png`, fullPage: true })
  await page.locator('.composer-meta .composer-meta-btn').first().click()
  await page.waitForTimeout(400)

  log('5) 新建任务 → 从工作区文件夹引用文件')
  await page.locator('.composer-textarea').fill('用我本地的资料总结一下')
  await page.locator('.composer-send').click()
  await page.waitForTimeout(4000)

  const addBtn = page.locator('.mini-btn', { hasText: '从工作区文件夹添加' })
  check((await addBtn.count()) === 1, '引用文件区出现「从工作区文件夹添加」')
  await addBtn.click()
  await page.waitForSelector('.fp-panel', { timeout: 10000 })
  // 文件模式下应从绑定目录起步
  const startPath = await page.locator('.fp-path-text').innerText()
  check(startPath.includes(FOLDER_NAME), '选择器从绑定目录起步（不用重新找）', startPath.slice(-30))
  const dirItems = await page.locator('.fp-item').allInnerTexts()
  check(
    dirItems.some(t => t.includes('本地已有资料')),
    '列出了文件夹里的文件',
    JSON.stringify(dirItems.slice(0, 3))
  )
  await page.screenshot({ path: `${OUT}/05-file-picker.png`, fullPage: true })

  // 选择器已改为多选：先勾选，再点「加入选中的 N 个文件」
  await page.locator('.fp-item', { hasText: '本地已有资料' }).first().click()
  await page.locator('.fp-actions .mini-btn', { hasText: '加入选中的' }).click()
  await page.waitForTimeout(4000)
  const fileRows = await page.locator('.file-row').allInnerTexts()
  check(
    fileRows.some(t => t.includes('本地已有资料')),
    '★ 文件已引用到任务里',
    JSON.stringify(fileRows.map(t => t.replace(/\s+/g, ' ').slice(0, 30)))
  )
  await page.screenshot({ path: `${OUT}/06-file-added.png`, fullPage: true })

  log('6) 解绑')
  await openWsMenu()
  await page.locator('.menu-folder .mini-btn', { hasText: '解绑' }).click()
  await page.waitForTimeout(2500)
  await openWsMenu()
  const head3 = await page.locator('.menu-folder-head').innerText()
  check(head3.includes('未绑定'), '解绑后回到「未绑定」', head3.replace(/\s+/g, ' ').slice(0, 40))

  log('7) 清理')
  const cleaned = await page.evaluate(async name => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('token')}` }
    const list = await (await fetch('/api/workflow/workspaces', { headers })).json()
    const targets = (list.workspaces || []).filter(w => w.name === name)
    if (!targets.length) return 'not-found'
    let ok = 0
    for (const t of targets) {
      const res = await fetch(`/api/workflow/workspaces/${t.id}`, { method: 'DELETE', headers })
      if (res.ok) ok += 1
    }
    return ok === targets.length ? `deleted:${ok}` : `partial:${ok}/${targets.length}`
  }, WS_NAME)
  check(cleaned.startsWith('deleted'), '验证用工作区已清理', cleaned)
} catch (e) {
  fails.push('脚本异常: ' + e.message)
  log('EXCEPTION', e.message)
  try {
    await page.screenshot({ path: `${OUT}/99-error.png`, fullPage: true })
  } catch {}
} finally {
  await browser.close()
  // 清理本机测试文件夹（只删自己刚建的那个，且名字带固定前缀）
  try {
    if (localDir.startsWith(homedir()) && FOLDER_NAME.startsWith('kb-ws-test-')) {
      rmSync(localDir, { recursive: true, force: true })
    }
  } catch (e) {
    log('清理测试文件夹失败：' + e.message)
  }
  log('')
  log('=== 控制台错误 ===')
  if (consoleErrors.length === 0) log('（无）')
  else consoleErrors.forEach(e => log('  -', e))
}

log('')
log(fails.length === 0 ? '全部通过' : `失败 ${fails.length} 项: ${fails.join(' | ')}`)
process.exit(fails.length === 0 ? 0 : 1)
