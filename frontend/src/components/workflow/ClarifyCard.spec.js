/**
 * 组件级测试（第一批）。
 *
 * 之前前端只有 4 个**纯函数**模块有测试（api/http、utils/citations、
 * utils/chatGroups、data/modelVendors），组件层是零覆盖 —— 也就是说
 * "引文上标能不能点"、"审批按钮回传什么载荷"、"流式消息会不会重复渲染"
 * 这些真正影响用户的行为，全靠人工看。
 *
 * @vue/test-utils 与 jsdom 都已在 devDependencies 里，vitest 环境也已经是 jsdom。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ClarifyCard from './ClarifyCard.vue'

const questions = [
  {
    id: 'q1',
    question: '要生成哪种交付物？',
    reason: '默认选脚本',
    recommended: 'script',
    options: [
      { value: 'script', label: 'Python 脚本' },
      { value: 'doc', label: '说明文档' },
    ],
  },
  {
    id: 'q2',
    question: '目标目录？',
    reason: '',
    recommended: '',
    options: [],
  },
]

describe('ClarifyCard', () => {
  it('展示题目数量与轮次', () => {
    const w = mount(ClarifyCard, { props: { questions, round: 1, maxRounds: 3 } })
    expect(w.text()).toContain('需要确认 2 件事')
    expect(w.text()).toContain('第 1 / 3 轮')
  })

  it('点选推荐项会回传 submit 事件与答案载荷', async () => {
    const w = mount(ClarifyCard, { props: { questions } })
    // 点第二个选项（"说明文档"）—— 默认是自动选中推荐项的
    await w.findAll('.q-option')[1].trigger('click')
    await w.find('.btn-primary').trigger('click')

    const emitted = w.emitted('submit')
    expect(emitted).toBeTruthy()
    expect(emitted[0][0]).toEqual([
      // 只回传**已作答**的题目（未作答的 q2 不会出现在载荷里）
      { id: 'q1', value: 'doc', text: '' },
    ])
  })

  it('默认自动选中推荐答案（多数情况用户直接提交就行）', async () => {
    const w = mount(ClarifyCard, { props: { questions } })
    await w.find('.btn-primary').trigger('click')
    const emitted = w.emitted('submit')
    expect(emitted[0][0][0]).toEqual({ id: 'q1', value: 'script', text: '' })
  })

  it('点「跳过澄清」发出 skip 事件', async () => {
    const w = mount(ClarifyCard, { props: { questions } })
    await w.find('.mini-btn').trigger('click')
    expect(w.emitted('skip')).toBeTruthy()
  })

  it('busy 时禁用交互', () => {
    const w = mount(ClarifyCard, { props: { questions, busy: true } })
    expect(w.find('.mini-btn').attributes('disabled')).toBeDefined()
    expect(w.findAll('.q-option')[0].attributes('disabled')).toBeDefined()
  })

  it('没有推荐标签时不渲染「推荐」角标', () => {
    const w = mount(ClarifyCard, { props: { questions } })
    // q1 的推荐项应带角标
    expect(w.findAll('.q-option-tag')).toHaveLength(1)
  })
})
