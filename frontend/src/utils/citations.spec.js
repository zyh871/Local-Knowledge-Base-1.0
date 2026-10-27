import { describe, it, expect } from 'vitest'

import {
  injectCitations,
  confidenceLabel,
  hasMoreSourceText,
} from './citations'

// 三个来源：合法编号范围是 1..3
const THREE_SOURCES = [{}, {}, {}]

describe('injectCitations', () => {
  it('没有来源时原样返回', () => {
    expect(injectCitations('见 [1]', [])).toBe('见 [1]')
    expect(injectCitations('', THREE_SOURCES)).toBe('')
  })

  it('把范围内的 [n] 换成可点击上标', () => {
    const out = injectCitations('结论[1]，另见[2]。', THREE_SOURCES)
    expect(out).toContain('data-cite="1"')
    expect(out).toContain('data-cite="2"')
    expect(out.match(/class="cite-ref"/g)).toHaveLength(2)
  })

  it('紧跟在英文单词后也能识别', () => {
    const out = injectCitations('see[1] for details', THREE_SOURCES)
    expect(out).toContain('data-cite="1"')
  })

  it('超出来源数量的编号不替换', () => {
    const out = injectCitations('见[9]', THREE_SOURCES)
    expect(out).toContain('[9]')
    expect(out).not.toContain('cite-ref')
  })

  it('不破坏普通 Markdown 链接与引用式链接', () => {
    const md = '[1](https://a.com) 与\n[1]: https://b.com'
    const out = injectCitations(md, THREE_SOURCES)
    expect(out).toContain('[1](https://a.com)')
    expect(out).toContain('[1]: https://b.com')
    expect(out).not.toContain('cite-ref')
  })

  it('不替换围栏代码块里的 [1]', () => {
    const md = '说明[1]\n```js\nconst a = arr[1]\n```\n'
    const out = injectCitations(md, THREE_SOURCES)
    expect(out).toContain('const a = arr[1]')
    expect(out.match(/cite-ref/g)).toHaveLength(1)
  })

  it('不把嵌套方括号 [[1]] 误判为引用', () => {
    expect(injectCitations('[[1]]', THREE_SOURCES)).toBe('[[1]]')
  })
})

describe('confidenceLabel', () => {
  it('映射三档置信度', () => {
    expect(confidenceLabel('high')).toBe('高相关')
    expect(confidenceLabel('medium')).toBe('中相关')
    expect(confidenceLabel('low')).toBe('低相关')
  })

  it('未知值返回空串', () => {
    expect(confidenceLabel('')).toBe('')
    expect(confidenceLabel(undefined)).toBe('')
  })
})

describe('hasMoreSourceText', () => {
  it('全文比预览长才提供展开', () => {
    expect(hasMoreSourceText({ content: 'x'.repeat(200), content_preview: 'x'.repeat(100) })).toBe(true)
    expect(hasMoreSourceText({ content: 'short', content_preview: 'short' })).toBe(false)
  })

  it('缺字段时安全返回 false', () => {
    expect(hasMoreSourceText(null)).toBe(false)
    expect(hasMoreSourceText({})).toBe(false)
  })
})
