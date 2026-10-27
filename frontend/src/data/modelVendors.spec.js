import { describe, it, expect } from 'vitest'

import {
  CUSTOM_VENDOR,
  MODEL_TYPE_LABELS,
  MODEL_VENDORS,
  modelTypeLabel,
} from './modelVendors'

describe('MODEL_VENDORS', () => {
  it('覆盖需求要求的 10 家厂商', () => {
    expect(MODEL_VENDORS).toHaveLength(10)
    const names = MODEL_VENDORS.map((v) => v.name)
    expect(names).toEqual(
      expect.arrayContaining([
        '豆包',
        '千问',
        'DeepSeek',
        '智谱',
        'Kimi',
        '腾讯混元',
        'MiniMax',
        '小米 MiMo',
        'GPT',
        'Claude',
      ]),
    )
  })

  it('每家厂商都有完整的展示字段', () => {
    for (const vendor of MODEL_VENDORS) {
      expect(vendor.id).toBeTruthy()
      expect(vendor.name).toBeTruthy()
      expect(vendor.icon).toBeTruthy()
      expect(vendor.color).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(vendor.desc.length).toBeGreaterThan(8)
      expect(Array.isArray(vendor.models)).toBe(true)
      expect(vendor.models.length).toBeGreaterThan(0)
    }
  })

  it('厂商 id 唯一', () => {
    const ids = MODEL_VENDORS.map((v) => v.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('同一厂商内模型 id 唯一，且类型合法', () => {
    const validTypes = new Set(Object.keys(MODEL_TYPE_LABELS))
    for (const vendor of MODEL_VENDORS) {
      const ids = vendor.models.map((m) => m.id)
      expect(new Set(ids).size).toBe(ids.length)
      for (const model of vendor.models) {
        expect(model.name).toBeTruthy()
        expect(model.id).toBeTruthy()
        expect(model.types.length).toBeGreaterThan(0)
        for (const type of model.types) {
          expect(validTypes.has(type)).toBe(true)
        }
      }
    }
  })

  it('每家厂商都有明/暗两套官方标识资源', () => {
    for (const vendor of MODEL_VENDORS) {
      expect(vendor.logo, `${vendor.name} 缺少 logo`).toBeTruthy()
      expect(vendor.logo.light).toContain('.png')
      expect(vendor.logo.dark).toContain('.png')
    }
  })

  it('自定义占位厂商没有 logo，回退到字母图标', () => {
    expect(CUSTOM_VENDOR.logo).toBeUndefined()
    expect(CUSTOM_VENDOR.icon).toBeTruthy()
  })

  it('Base URL 要么为空（需用户自填），要么是 https 地址', () => {
    for (const vendor of MODEL_VENDORS) {
      if (vendor.baseUrl) {
        expect(vendor.baseUrl.startsWith('https://')).toBe(true)
      }
    }
  })

  it('自定义占位厂商没有内置模型，避免被误当目录展示', () => {
    expect(CUSTOM_VENDOR.models).toEqual([])
    expect(CUSTOM_VENDOR.baseUrl).toBe('')
  })
})

describe('modelTypeLabel', () => {
  it('把类型映射为中文', () => {
    expect(modelTypeLabel('chat')).toBe('对话')
    expect(modelTypeLabel('reasoning')).toBe('推理')
    expect(modelTypeLabel('multimodal')).toBe('多模态')
    expect(modelTypeLabel('embedding')).toBe('向量')
  })

  it('未知类型原样返回，便于发现新类型未登记', () => {
    expect(modelTypeLabel('unknown')).toBe('unknown')
  })
})
