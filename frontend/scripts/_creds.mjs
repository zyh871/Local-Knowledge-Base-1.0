/**
 * 验收脚本统一的管理员凭据来源。
 *
 * **不要在脚本里写明文口令** —— 仓库里出现过的明文口令等于凭据泄露。
 *
 * 用法：
 *   import { ADMIN_USERNAME, ADMIN_PASSWORD } from './_creds.mjs'
 *
 * 凭据来源优先级：环境变量 > 仓库根 `.env` > `backend/.env`。
 * 都取不到时直接抛错，避免用错口令反复登录失败却看不出原因。
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

function readKeyFrom(file, key) {
  try {
    const text = readFileSync(file, 'utf8')
    const m = text.match(new RegExp(`^${key}\\s*=\\s*(.*)$`, 'm'))
    if (!m) return ''
    return m[1].trim().replace(/^["']|["']$/g, '')
  } catch {
    return ''
  }
}

function fromEnvFiles(key) {
  const candidates = [
    resolve(HERE, '../../.env'),
    resolve(HERE, '../../backend/.env'),
  ]
  for (const file of candidates) {
    const value = readKeyFrom(file, key)
    if (value) return value
  }
  return ''
}

export const ADMIN_USERNAME = process.env.ADMIN_USERNAME || fromEnvFiles('ADMIN_USERNAME') || 'admin'
export const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || fromEnvFiles('ADMIN_PASSWORD')

if (!ADMIN_PASSWORD) {
  throw new Error(
    '缺少 ADMIN_PASSWORD：请在 backend/.env 中设置，或用环境变量传入。' +
      '脚本里不允许硬编码口令。',
  )
}
