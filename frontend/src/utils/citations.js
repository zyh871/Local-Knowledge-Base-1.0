/**
 * 引用标记解析（F3）。
 *
 * 抽成纯函数而不是留在 Chat.vue 里，原因有二：
 *  1. 正则的边界条件（Markdown 链接、引用式链接、代码块）很容易写错，
 *     而这类错误在界面上只表现为"某个链接点不动"，极难排查；
 *  2. 纯函数可以直接用 Vitest 钉住行为，不依赖浏览器。
 */

/**
 * 把答案里的 `[n]` 换成可点击的上标，n 必须落在来源数量范围内。
 *
 * 明确不处理的情况：
 *  - `[n](url)`        —— 普通 Markdown 链接
 *  - `[n]: url`        —— 引用式链接定义
 *  - 围栏代码块 ```...``` —— 代码里的 `[1]` 通常是数组下标
 *
 * @param {string} md 原始 Markdown
 * @param {Array} sources 来源列表（长度决定合法编号范围）
 * @returns {string}
 */
export function injectCitations(md, sources) {
  if (!md || !sources || sources.length === 0) return md
  const max = sources.length

  const replaceSegment = (segment) =>
    segment.replace(
      /(^|[^\][<])\[(\d{1,2})\](?!\(|:)/g,
      (match, lead, num) => {
        const index = Number(num)
        if (index < 1 || index > max) return match
        return `${lead}<sup class="cite-ref" data-cite="${index}" role="button" tabindex="0">[${index}]</sup>`
      },
    )

  // split 带捕获组时，奇数下标即被保留的分隔符（代码块）
  return md
    .split(/(```[\s\S]*?```)/g)
    .map((part, i) => (i % 2 === 1 ? part : replaceSegment(part)))
    .join('')
}

/** 来源置信度的中文标签（后端只给 high/medium/low）。 */
export function confidenceLabel(confidence) {
  return { high: '高相关', medium: '中相关', low: '低相关' }[confidence] || ''
}

/** 是否值得提供「展开原文」：全文必须比预览更长。 */
export function hasMoreSourceText(source) {
  return !!(
    source &&
    source.content &&
    source.content.length > (source.content_preview || '').length
  )
}
