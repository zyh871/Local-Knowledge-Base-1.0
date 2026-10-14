import { describe, it, expect } from 'vitest'

import { query } from './http'

/**
 * `src/` 下此前**没有任何单测文件**，而 vitest 在"找不到测试"时以退出码 1
 * 结束 —— CI 会红。这里先给最容易出错、也最值得钉住的纯函数补上覆盖。
 *
 * `query()` 的风险点是「空值处理」：后端分页接口对 `?page=` 这种空参数
 * 的处理与「不带参数」不同，把空值拼进去会得到意外结果。
 */
describe('query()', () => {
  it('把普通对象拼成查询串', () => {
    expect(query({ a: 1, b: 'x' })).toBe('?a=1&b=x')
  })

  it('跳过 undefined / null / 空串', () => {
    expect(query({ a: 1, b: undefined, c: null, d: '' })).toBe('?a=1')
  })

  it('全部为空时返回空串，不留孤立的问号', () => {
    expect(query({ a: undefined, b: null, c: '' })).toBe('')
  })

  it('空对象与无参调用都返回空串', () => {
    expect(query({})).toBe('')
    expect(query()).toBe('')
    expect(query(null)).toBe('')
  })

  it('对特殊字符做编码', () => {
    expect(query({ q: 'a b' })).toBe('?q=a+b')
    expect(query({ q: '中' })).toBe('?q=%E4%B8%AD')
  })

  it('保留数字 0 与布尔值（它们是有效值，不是空值）', () => {
    expect(query({ page: 0, flag: false })).toBe('?page=0&flag=false')
  })
})
