/**
 * 开源社区 API。
 *
 * 可见性规则（后端强制，前端只做展示）：
 *   * `published` —— 任何人可见
 *   * `draft`     —— 仅团队成员可见，其他人拿到 404（防枚举）
 *
 * 所以前端不能根据「列表里没有」推断「不存在」，只能按 `visibility` 展示。
 */

import { request, query } from './http'

const enc = encodeURIComponent
const base = (projectId) => `/community/projects/${enc(projectId)}`

export const listProjects = ({ q = '', language = '', tag = '', sort = 'recent', limit = 50, offset = 0 } = {}) =>
  request(`/community/projects${query({ q, language, tag, sort, limit, offset })}`)

export const createProject = (body) =>
  request('/community/projects', { method: 'POST', body })

export const getProject = (projectId) => request(base(projectId))

export const updateProject = (projectId, body) =>
  request(base(projectId), { method: 'PATCH', body })

export const deleteProject = (projectId) =>
  request(base(projectId), { method: 'DELETE' })

export const publishProject = (projectId) =>
  request(`${base(projectId)}/publish`, { method: 'POST' })

export const unpublishProject = (projectId) =>
  request(`${base(projectId)}/unpublish`, { method: 'POST' })

export const listContributors = (projectId) =>
  request(`${base(projectId)}/contributors`)

export const addContributor = (projectId, { username, role = 'contributor' }) =>
  request(`${base(projectId)}/contributors`, {
    method: 'POST',
    body: { username, role },
  })

export const removeContributor = (projectId, username) =>
  request(`${base(projectId)}/contributors/${enc(username)}`, {
    method: 'DELETE',
  })

/** star / watch 都是幂等的：重复 POST 不会让计数虚高。 */
export const starProject = (projectId) =>
  request(`${base(projectId)}/star`, { method: 'POST' })

export const unstarProject = (projectId) =>
  request(`${base(projectId)}/star`, { method: 'DELETE' })

export const watchProject = (projectId) =>
  request(`${base(projectId)}/watch`, { method: 'POST' })

export const unwatchProject = (projectId) =>
  request(`${base(projectId)}/watch`, { method: 'DELETE' })

export const listComments = (projectId) => request(`${base(projectId)}/comments`)

export const addComment = (projectId, body) =>
  request(`${base(projectId)}/comments`, { method: 'POST', body: { body } })

export const deleteComment = (projectId, commentId) =>
  request(`${base(projectId)}/comments/${enc(commentId)}`, { method: 'DELETE' })

export const getUserProfile = (username) =>
  request(`/community/users/${enc(username)}`)

export const followUser = (username) =>
  request(`/community/users/${enc(username)}/follow`, { method: 'POST' })

export const unfollowUser = (username) =>
  request(`/community/users/${enc(username)}/follow`, { method: 'DELETE' })

/* ── 展示用元数据 ─────────────────────────────────────────────── */

/** 常用语言：下拉选择，避免手打错别字导致筛选失配。 */
export const LANGUAGES = [
  'Python',
  'TypeScript',
  'JavaScript',
  'Vue',
  'Go',
  'Rust',
  'Java',
  'C++',
  'Markdown',
  '其他',
]

export const VISIBILITY_LABEL = {
  published: '已发布',
  draft: '草稿',
}

/** 相对时间：社区列表里「3 天前」比一串时间戳更好读。 */
export function timeAgo(ts) {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return new Date(ts * 1000).toLocaleDateString('zh-CN')
}
