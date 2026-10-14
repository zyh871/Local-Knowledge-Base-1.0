/**
 * AI 工作流接口客户端。
 *
 * 鉴权头、错误体解包、查询串拼接已抽到 `./http`（与模型端点客户端共用）。
 */

import { API_BASE, authHeaders, query, request } from './http'

export { API_BASE }

export const workflowApi = {
  capabilities: () => request('/workflow/capabilities'),

  listWorkspaces: () => request('/workflow/workspaces'),
  createWorkspace: (name) =>
    request('/workflow/workspaces', { method: 'POST', body: { name } }),
  renameWorkspace: (id, name) =>
    request(`/workflow/workspaces/${id}`, { method: 'PATCH', body: { name } }),
  deleteWorkspace: (id) =>
    request(`/workflow/workspaces/${id}`, { method: 'DELETE' }),

  listRuns: (params) => request(`/workflow/runs${query(params)}`),
  createRun: (payload) => request('/workflow/runs', { method: 'POST', body: payload }),
  getRun: (id) => request(`/workflow/runs/${id}`),
  updateRun: (id, payload) =>
    request(`/workflow/runs/${id}`, { method: 'PATCH', body: payload }),
  deleteRun: (id) => request(`/workflow/runs/${id}`, { method: 'DELETE' }),

  listFiles: (runId) => request(`/workflow/runs/${runId}/files`),
  uploadFiles: (runId, files) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return request(`/workflow/runs/${runId}/files`, { method: 'POST', body: form })
  },
  deleteFile: (runId, fileId) =>
    request(`/workflow/runs/${runId}/files/${fileId}`, { method: 'DELETE' }),

  savePlan: (runId, plan) =>
    request(`/workflow/runs/${runId}/plan`, { method: 'PUT', body: plan }),
  approvePlan: (runId) =>
    request(`/workflow/runs/${runId}/approve`, { method: 'POST' }),
  rejectPlan: (runId, comment) =>
    request(`/workflow/runs/${runId}/reject`, {
      method: 'POST',
      body: { comment: comment || '' },
    }),

  answerClarify: (runId, answers) =>
    request(`/workflow/runs/${runId}/clarify/answer`, {
      method: 'POST',
      body: { answers },
    }),
  skipClarify: (runId, reason = '') =>
    request(`/workflow/runs/${runId}/clarify/skip`, {
      method: 'POST',
      body: { reason },
    }),

  executeStep: (runId, idx) =>
    request(`/workflow/runs/${runId}/steps/${idx}/execute`, { method: 'POST' }),
  approveStep: (runId, idx) =>
    request(`/workflow/runs/${runId}/steps/${idx}/approve`, { method: 'POST' }),
  retryStep: (runId, idx) =>
    request(`/workflow/runs/${runId}/steps/${idx}/retry`, { method: 'POST' }),
  skipStep: (runId, idx) =>
    request(`/workflow/runs/${runId}/steps/${idx}/skip`, { method: 'POST' }),
  rejectStep: (runId, idx, comment) =>
    request(`/workflow/runs/${runId}/steps/${idx}/reject`, {
      method: 'POST',
      body: { comment: comment || '' },
    }),
  cancelRun: (runId, reason = '') =>
    request(`/workflow/runs/${runId}/cancel`, {
      method: 'POST',
      body: { comment: reason },
    }),

  deliverRun: (runId) =>
    request(`/workflow/runs/${runId}/deliver`, { method: 'POST' }),
  listArtifacts: (runId) => request(`/workflow/runs/${runId}/artifacts`),
  shareRun: (runId, ttlHours = null) =>
    request(`/workflow/runs/${runId}/share`, {
      method: 'POST',
      body: { ttl_hours: ttlHours },
    }),

  // ── 本地文件夹 ──────────────────────────────────────────────
  /** 浏览服务器本机目录（供文件夹选择器）。path 留空返回起始位置。 */
  browseFs: (path = '') =>
    request(`/workflow/fs/list?path=${encodeURIComponent(path)}`),
  bindWorkspaceFolder: (workspaceId, localDir) =>
    request(`/workflow/workspaces/${workspaceId}/folder`, {
      method: 'PUT',
      body: { local_dir: localDir },
    }),
  openWorkspaceFolder: (workspaceId) =>
    request(`/workflow/workspaces/${workspaceId}/open`, { method: 'POST' }),
  /** 把工作区文件夹里的一个文件引用到任务（后端会复制一份）。 */
  importLocalFile: (runId, path) =>
    request(`/workflow/runs/${runId}/files/import`, {
      method: 'POST',
      body: { path },
    }),
  /** 批量引用：逐个单独校验，部分失败不影响其余（返回 failed 说明哪几个没进来）。 */
  importLocalFiles: (runId, paths) =>
    request(`/workflow/runs/${runId}/files/import-batch`, {
      method: 'POST',
      body: { paths },
    }),

  listModels: () => request('/models'),
}

/**
 * 通用 SSE 读取器（POST + Authorization 头 + 手工解帧）。
 *
 * 不能用 `EventSource`：它只支持 GET，且带不上 `Authorization` 头。
 *
 * **必须做跨分片缓冲** —— 一个 JSON 事件被 TCP 拆成两半是常态，
 * 直接按 chunk 解帧会偶发解析失败（且只在网络抖动时复现，极难查）。
 *
 * 服务端帧格式固定为 `data: {json}\n\n`（见 `schemas/qa.py::sse_frame`）。
 */
async function streamSSE(path, { onEvent, signal } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { ...authHeaders() },
    signal,
  })

  if (!res.ok) {
    const raw = await res.text()
    let message = `请求失败（HTTP ${res.status}）`
    try {
      const data = JSON.parse(raw)
      message = data?.error?.message || message
    } catch (e) {
      // 非 JSON 错误体，保留默认文案
    }
    throw new Error(message)
  }
  if (!res.body) throw new Error('当前浏览器不支持流式响应')

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const drain = (flush = false) => {
    let index
    while ((index = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, index)
      buffer = buffer.slice(index + 2)
      const line = block.trim()
      if (!line.startsWith('data:')) continue
      const payload = line.slice(5).trim()
      if (!payload) continue
      try {
        onEvent?.(JSON.parse(payload))
      } catch (e) {
        // 单个事件坏了不该中断整条流
        console.warn('SSE 事件解析失败:', payload.slice(0, 200))
      }
    }
    if (flush && buffer.trim()) {
      const line = buffer.trim()
      if (line.startsWith('data:')) {
        try {
          onEvent?.(JSON.parse(line.slice(5).trim()))
        } catch (e) {
          /* 收尾帧坏了同样忽略 */
        }
      }
      buffer = ''
    }
  }

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    drain()
  }
  buffer += decoder.decode()
  drain(true)
}

/** 生成任务书（SSE）。 */
export function streamPlan(runId, options) {
  return streamSSE(`/workflow/runs/${runId}/plan/stream`, options)
}

/** 产出一轮澄清问题（SSE）。 */
export function streamClarify(runId, options) {
  return streamSSE(`/workflow/runs/${runId}/clarify/stream`, options)
}

/** 执行单个步骤（SSE）。一次请求只推进一步。 */
export function streamExecuteStep(runId, idx, options) {
  return streamSSE(`/workflow/runs/${runId}/steps/${idx}/execute`, options)
}

/**
 * 下载引用文件。
 *
 * 不能直接用 `<a href>`：鉴权走 `Authorization` 头，而链接不会带上它。
 * 因此先 fetch 成 blob，再用临时 object URL 触发下载。
 */
export async function downloadRunFile(fileId, filename) {
  const res = await fetch(`${API_BASE}/workflow/files/${fileId}/download`, {
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** 展示用：把后端状态码翻成中文标签。 */
export const RUN_STATUS_LABELS = {
  draft: '草稿',
  clarifying: '澄清中',
  planning: '规划中',
  awaiting_approval: '待审批',
  approved: '已批准（待执行）',
  running: '执行中',
  paused: '已暂停',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}
