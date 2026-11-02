/**
 * 团队空间 API。
 *
 * 与 `workflow.js` 里的 workspace 接口**不是一回事**：
 *   * workspace —— 任务绑定的本地文件夹（`/workflow/workspaces`）
 *   * team      —— 成员与角色权限（`/teams`）
 *
 * 所有错误统一由 `request()` 抛出 `Error`，并带 `status`（403 = 角色不够、
 * 404 = 不是成员，后端刻意用 404 防枚举）。
 */

import { request } from './http'

const enc = encodeURIComponent

/** 我加入的团队（含 `my_role` / `member_count`）。 */
export const listTeams = () => request('/teams')

export const createTeam = (body) => request('/teams', { method: 'POST', body })

export const getTeam = (teamId) => request(`/teams/${enc(teamId)}`)

export const updateTeam = (teamId, body) =>
  request(`/teams/${enc(teamId)}`, { method: 'PATCH', body })

export const deleteTeam = (teamId) =>
  request(`/teams/${enc(teamId)}`, { method: 'DELETE' })

export const listMembers = (teamId) =>
  request(`/teams/${enc(teamId)}/members`)

/** 直接拉人入组（要求对方账号已存在），或改已有成员的角色。 */
export const addMember = (teamId, { username, role }) =>
  request(`/teams/${enc(teamId)}/members`, {
    method: 'POST',
    body: { username, role },
  })

export const removeMember = (teamId, username) =>
  request(`/teams/${enc(teamId)}/members/${enc(username)}`, {
    method: 'DELETE',
  })

export const transferOwner = (teamId, username) =>
  request(`/teams/${enc(teamId)}/transfer`, {
    method: 'POST',
    body: { username },
  })

/** 生成一次性邀请链接。明文 token 只在这次响应里出现，之后拿不到。 */
export const createTeamInvitation = (teamId, { role, ttlHours = 72 }) =>
  request(`/teams/${enc(teamId)}/invitations`, {
    method: 'POST',
    body: { role, ttl_hours: ttlHours },
  })

/** 当前用户在团队里的有效能力字符串列表。 */
export const getPermissions = (teamId) =>
  request(`/teams/${enc(teamId)}/permissions`)

/* ── 角色元数据（与后端 ROLE_CAPABILITIES 对齐，只用于展示） ────────── */

export const ROLES = [
  { key: 'owner', label: '所有者', desc: '可转让、可删除团队，成员与项目全权' },
  { key: 'admin', label: '管理员', desc: '可管理成员与发布项目' },
  { key: 'editor', label: '编辑', desc: '可创建与编辑项目，不能改成员' },
  { key: 'viewer', label: '只读', desc: '仅可查看团队内容' },
]

/** 可邀请 / 可委派的角色（owner 只能通过转让产生）。 */
export const ASSIGNABLE_ROLES = ROLES.filter((r) => r.key !== 'owner')

export const ROLE_LABEL = Object.fromEntries(
  ROLES.map((r) => [r.key, r.label])
)

/** 能力字符串 → 人话，用于「我的权限」展示。 */
export const CAPABILITY_LABEL = {
  'team:read': '查看团队',
  'team:update': '修改团队信息',
  'team:manage_members': '管理成员',
  'team:transfer': '转让所有权',
  'team:delete': '删除团队',
  'content:read': '读取知识内容',
  'content:write': '写入知识内容',
  'project:read': '查看项目',
  'project:write': '创建/编辑项目',
  'project:publish': '发布项目到社区',
  'project:manage_members': '管理项目成员',
  'project:delete': '删除项目',
}

/**
 * 展示顺序。
 *
 * 后端返回的 `permissions` 是 set 转出来的列表，顺序不稳定 ——
 * 不排序的话"删除团队"会随机夹在"查看团队"和"修改团队信息"之间，
 * 每次刷新顺序都变，看起来像在闪。
 * 未登记的未知能力统一排到最后，仍然显示原文而不是被丢掉。
 */
const CAP_ORDER = Object.keys(CAPABILITY_LABEL)

export function sortCapabilities(caps) {
  return [...(caps || [])].sort((a, b) => {
    const ia = CAP_ORDER.indexOf(a)
    const ib = CAP_ORDER.indexOf(b)
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib)
  })
}
