/**
 * 通用 HTTP 客户端。
 *
 * 鉴权头、错误体解包、查询串拼接三件事**只写一次** ——
 * 后端的错误体是 `{detail, error:{code,message,detail,trace_id}}`，
 * 这里统一抛 `Error` 并把 `status` / `code` 挂在异常上，调用方按需分支。
 *
 * （`api/workflow.js` 原先自带一份同样的实现，现已改为引用这里。）
 */

export const API_BASE = '/api'

export function authHeaders() {
  const token = localStorage.getItem('token') || ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function request(path, { method = 'GET', body } = {}) {
  const init = { method, headers: { ...authHeaders() } }

  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body // 让浏览器自己带 multipart boundary
    } else {
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
  }

  const res = await fetch(`${API_BASE}${path}`, init)
  const raw = await res.text()
  let data = null
  try {
    data = raw ? JSON.parse(raw) : null
  } catch {
    data = null
  }

  if (!res.ok) {
    const message =
      data?.error?.message ||
      (typeof data?.detail === 'string' ? data.detail : '') ||
      `请求失败（HTTP ${res.status}）`
    const err = new Error(message)
    err.status = res.status
    err.code = data?.error?.code || ''
    err.payload = data
    throw err
  }
  return data
}

/** 拼查询串，自动跳过空值。 */
export function query(params) {
  const usable = Object.entries(params || {}).filter(
    ([, v]) => v !== undefined && v !== null && v !== ''
  )
  return usable.length ? `?${new URLSearchParams(usable)}` : ''
}
