import { describe, it, expect } from 'vitest'

import {
  GROUP_NAME_MAX,
  UNGROUPED_KEY,
  buildChatListItems,
  deleteGroupEverywhere,
  moveChatToGroup,
  renameGroupEverywhere,
  validateNewGroupName,
} from './chatGroups'

const chat = (id, title, group) => {
  const c = { id, title }
  if (group) c.group = group
  return c
}

describe('validateNewGroupName', () => {
  it('拒绝空名、超长与保留名', () => {
    expect(validateNewGroupName([], '   ').reason).toBe('empty')
    expect(validateNewGroupName([], 'x'.repeat(GROUP_NAME_MAX + 1)).reason).toBe('too_long')
    expect(validateNewGroupName([], '未分组').reason).toBe('reserved')
    expect(validateNewGroupName([], '置顶').reason).toBe('reserved')
  })

  it('拒绝重名，接受新名并裁剪空格', () => {
    expect(validateNewGroupName(['研发'], '研发').reason).toBe('duplicate')
    expect(validateNewGroupName(['研发'], '  产品  ')).toEqual({
      ok: true,
      name: '产品',
      reason: '',
    })
  })
})

describe('moveChatToGroup', () => {
  it('同组移动不产生变化', () => {
    const c = chat('1', 'a', '研发')
    expect(moveChatToGroup(c, '研发')).toBe(false)
    expect(c.group).toBe('研发')
  })

  it('跨组移动更新 group', () => {
    const c = chat('1', 'a', '研发')
    expect(moveChatToGroup(c, '产品')).toBe(true)
    expect(c.group).toBe('产品')
  })

  it('移出分组会删除 group 字段', () => {
    const c = chat('1', 'a', '研发')
    expect(moveChatToGroup(c, '')).toBe(true)
    expect('group' in c).toBe(false)
  })
})

describe('deleteGroupEverywhere', () => {
  it('删除分组并把组内会话移回未分组', () => {
    const chats = [chat('1', 'a', '研发'), chat('2', 'b', '研发'), chat('3', 'c', '产品')]
    const groups = ['研发', '产品']
    const affected = deleteGroupEverywhere(chats, groups, '研发')

    expect(affected).toBe(2)
    expect(groups).toEqual(['产品'])
    expect('group' in chats[0]).toBe(false)
    expect(chats[2].group).toBe('产品')
  })

  it('删除不存在的分组返回 0 且不改动会话', () => {
    const chats = [chat('1', 'a', '研发')]
    expect(deleteGroupEverywhere(chats, ['研发'], '不存在')).toBe(0)
    expect(chats[0].group).toBe('研发')
  })
})

describe('renameGroupEverywhere', () => {
  it('改名并同步组内会话', () => {
    const chats = [chat('1', 'a', '研发'), chat('2', 'b', '产品')]
    const groups = ['研发', '产品']
    const res = renameGroupEverywhere(chats, groups, '研发', '技术')

    expect(res).toEqual({ ok: true, name: '技术', reason: '' })
    expect(groups).toEqual(['技术', '产品'])
    expect(chats[0].group).toBe('技术')
    expect(chats[1].group).toBe('产品')
  })

  it('允许改成「只是大小写/空格不同」的原名', () => {
    const chats = [chat('1', 'a', '研发')]
    const groups = ['研发']
    const res = renameGroupEverywhere(chats, groups, '研发', ' 研发 ')
    // 与原名等价（裁剪后相同），仍视为合法且不报 duplicate
    expect(res.ok).toBe(true)
    expect(groups).toEqual(['研发'])
  })

  it('重名/空名被拒绝且不改动数据', () => {
    const chats = [chat('1', 'a', '研发')]
    const groups = ['研发', '产品']
    expect(renameGroupEverywhere(chats, groups, '研发', '产品').reason).toBe('duplicate')
    expect(renameGroupEverywhere(chats, groups, '研发', '').reason).toBe('empty')
    expect(groups).toEqual(['研发', '产品'])
    expect(chats[0].group).toBe('研发')
  })
})

describe('buildChatListItems', () => {
  it('没有任何分组时保持扁平列表', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'a'), chat('2', 'b')],
      groups: [],
    })
    expect(items.map((i) => i.type)).toEqual(['chat', 'chat'])
  })

  it('顺序为：置顶 → 分组 → 未分组', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'p', '研发'), chat('2', 'a', '研发'), chat('3', 'u')],
      groups: ['研发'],
      pinnedIds: ['1'],
    })
    expect(items.map((i) => `${i.type}:${i.name || i.chat?.id}`)).toEqual([
      'group:置顶',
      'chat:1',
      'group:研发',
      'chat:2',
      'group:未分组',
      'chat:3',
    ])
  })

  it('空分组显示占位提示', () => {
    const items = buildChatListItems({ chats: [], groups: ['空组'] })
    expect(items.map((i) => i.type)).toEqual(['group', 'empty'])
  })

  it('折叠后保留分组头但隐藏会话', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'a', '研发')],
      groups: ['研发'],
      collapsed: { 研发: true },
    })
    expect(items.map((i) => i.type)).toEqual(['group'])
  })

  it('搜索时过滤标题，并隐藏无命中的分组', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'Java 模组', '研发'), chat('2', 'ASS 字幕', '产品')],
      groups: ['研发', '产品'],
      query: 'java',
    })
    expect(items.map((i) => `${i.type}:${i.name || i.chat?.id}`)).toEqual([
      'group:研发',
      'chat:1',
    ])
  })

  it('脏数据（group 指向未登记分组）不会让会话消失', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'a', '幽灵组')],
      groups: [],
    })
    expect(items.map((i) => i.type)).toEqual(['group', 'chat'])
    expect(items[0].name).toBe('幽灵组')
  })

  it('未分组 key 稳定，便于折叠', () => {
    const items = buildChatListItems({
      chats: [chat('1', 'a')],
      groups: ['研发'],
      collapsed: { [UNGROUPED_KEY]: true },
    })
    const ungroupedHeader = items.find((i) => i.name === '未分组')
    expect(ungroupedHeader).toBeTruthy()
    // 折叠后不应出现该组的 chat
    expect(items.some((i) => i.type === 'chat')).toBe(false)
  })
})
