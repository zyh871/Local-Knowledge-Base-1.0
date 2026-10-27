/**
 * 会话分组与「移动到分组」的纯逻辑（F-分组）。
 *
 * 抽离原因：分组是典型的**边界密集**功能 —— 空分组、同名、跨组、
 * 删除分组后归属、脏数据（chat.group 指向已不存在的组）都要有确定行为。
 * 这些规则放在 .vue 里只能靠手点验证，放在这里可以用 Vitest 钉死。
 */

export const PINNED_KEY = '__pinned__'
export const UNGROUPED_KEY = '__ungrouped__'
export const UNGROUPED_LABEL = '未分组'
export const GROUP_NAME_MAX = 20

/** 统一裁剪；非字符串安全转空串。 */
export function normalizeGroupName(name) {
  return String(name == null ? '' : name).trim()
}

/**
 * 校验新分组名。
 * @returns {{ok: boolean, name: string, reason: string}}
 *   reason: '' | 'empty' | 'too_long' | 'reserved' | 'duplicate'
 */
export function validateNewGroupName(groups, raw) {
  const name = normalizeGroupName(raw)
  if (!name) return { ok: false, name, reason: 'empty' }
  if (name.length > GROUP_NAME_MAX) return { ok: false, name, reason: 'too_long' }
  if (name === UNGROUPED_LABEL || name === '置顶') {
    return { ok: false, name, reason: 'reserved' }
  }
  if ((groups || []).includes(name)) return { ok: false, name, reason: 'duplicate' }
  return { ok: true, name, reason: '' }
}

/**
 * 把会话移动到分组（空串 = 移出分组）。
 * @returns {boolean} 是否真的发生了变化（用于决定是否提示/落盘）
 */
export function moveChatToGroup(chat, groupName) {
  if (!chat) return false
  const next = normalizeGroupName(groupName)
  const prev = normalizeGroupName(chat.group)
  if (prev === next) return false
  if (next) chat.group = next
  else delete chat.group
  return true
}

/**
 * 删除分组：组内会话回到「未分组」。
 * @returns {number} 受影响的会话数量
 */
export function deleteGroupEverywhere(chats, groups, name) {
  const target = normalizeGroupName(name)
  const idx = (groups || []).indexOf(target)
  if (idx >= 0) groups.splice(idx, 1)
  let affected = 0
  for (const chat of chats || []) {
    if (normalizeGroupName(chat.group) === target) {
      delete chat.group
      affected += 1
    }
  }
  return affected
}

/**
 * 重命名分组，并同步组内会话的 group 字段。
 * @returns {{ok: boolean, name: string, reason: string}}
 */
export function renameGroupEverywhere(chats, groups, oldName, rawNewName) {
  const from = normalizeGroupName(oldName)
  const idx = (groups || []).indexOf(from)
  if (idx < 0) return { ok: false, name: from, reason: 'missing' }

  // 校验时排除自身，否则「改成同名」会被误判为 duplicate
  const others = groups.filter((g) => g !== from)
  const check = validateNewGroupName(others, rawNewName)
  if (!check.ok) return { ok: false, name: check.name, reason: check.reason }

  groups.splice(idx, 1, check.name)
  for (const chat of chats || []) {
    if (normalizeGroupName(chat.group) === from) chat.group = check.name
  }
  return { ok: true, name: check.name, reason: '' }
}

/**
 * 构建侧栏条目：分组头 / 空分组提示 / 会话，按「置顶 → 各分组 → 未分组」交错。
 *
 * @param {object} opts
 * @param {Array} opts.chats      会话数组（顺序即组内顺序）
 * @param {string[]} opts.groups  已知分组名
 * @param {string[]} [opts.pinnedIds]
 * @param {string} [opts.query]   搜索词（标题匹配，空 = 不过滤）
 * @param {object} [opts.collapsed] 折叠状态 { [groupName]: true }
 * @returns {Array<{type:'group'|'empty'|'chat', key:string}>}
 */
export function buildChatListItems({
  chats,
  groups,
  pinnedIds = [],
  query = '',
  collapsed = {},
} = {}) {
  const q = String(query || '').trim().toLowerCase()
  const isSearching = q.length > 0
  const visible = isSearching
    ? (chats || []).filter((c) =>
        String(c.title || '').toLowerCase().includes(q),
      )
    : (chats || [])

  const pinnedSet = new Set(pinnedIds || [])
  const pinned = visible.filter((c) => pinnedSet.has(c.id))
  const unpinned = visible.filter((c) => !pinnedSet.has(c.id))

  const items = []

  // 折叠键统一用「分组名」（模板切换时只知道名字），同时兼容用内部 key
  // 记录折叠状态的老数据/测试。
  const isCollapsed = (name, key) => !!(collapsed[name] || collapsed[key])

  if (pinned.length) {
    items.push({
      type: 'group',
      key: `g:${PINNED_KEY}`,
      name: '置顶',
      count: pinned.length,
      pinned: true,
    })
    if (!isCollapsed('置顶', PINNED_KEY)) {
      pinned.forEach((c) => items.push({ type: 'chat', key: `c:${c.id}`, chat: c }))
    }
  }

  // 已知分组 + 脏数据里出现但未登记的分组（后者追加到末尾，避免会话消失）
  const known = [...(groups || [])]
  for (const c of unpinned) {
    const g = normalizeGroupName(c.group)
    if (g && !known.includes(g)) known.push(g)
  }

  // 一个分组都没有：保持原来的扁平「最近对话」体验
  if (known.length === 0) {
    unpinned.forEach((c) => items.push({ type: 'chat', key: `c:${c.id}`, chat: c }))
    return items
  }

  for (const g of known) {
    const list = unpinned.filter((c) => normalizeGroupName(c.group) === g)
    // 搜索时隐藏没有命中的分组：满屏「0」的分组头只会干扰结果
    if (isSearching && list.length === 0) continue
    items.push({
      type: 'group',
      key: `g:${g}`,
      name: g,
      count: list.length,
      pinned: false,
    })
    if (list.length === 0) {
      // 空分组仍然展示，否则用户无法从侧栏重命名/删除它
      if (!isCollapsed(g, g)) {
        items.push({ type: 'empty', key: `e:${g}`, name: g })
      }
      continue
    }
    if (!isCollapsed(g, g)) {
      list.forEach((c) => items.push({ type: 'chat', key: `c:${c.id}`, chat: c }))
    }
  }

  const ungrouped = unpinned.filter((c) => {
    const g = normalizeGroupName(c.group)
    return !g || !known.includes(g)
  })
  if (ungrouped.length) {
    items.push({
      type: 'group',
      key: `g:${UNGROUPED_KEY}`,
      name: UNGROUPED_LABEL,
      count: ungrouped.length,
      pinned: false,
    })
    if (!isCollapsed(UNGROUPED_LABEL, UNGROUPED_KEY)) {
      ungrouped.forEach((c) => items.push({ type: 'chat', key: `c:${c.id}`, chat: c }))
    }
  }

  return items
}
