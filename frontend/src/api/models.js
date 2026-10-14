/**
 * 自定义模型端点客户端。
 *
 * ⚠️ 后端**只返回掩码密钥**（`api_key_masked`），这里也从不尝试读取完整密钥。
 * 编辑表单里密钥留空即表示「不修改」。
 */

import { request } from './http'

export const modelApi = {
  /** 端点清单 + 当前部署是否允许配置（对应 ALLOW_CUSTOM_MODELS）。 */
  list: () => request('/model-endpoints'),

  create: (payload) => request('/model-endpoints', { method: 'POST', body: payload }),

  /** 局部更新；`api_key` 留空表示保持原值。 */
  update: (id, payload) =>
    request(`/model-endpoints/${id}`, { method: 'PATCH', body: payload }),

  remove: (id) => request(`/model-endpoints/${id}`, { method: 'DELETE' }),

  /** 测试已保存端点的连通性。 */
  test: (id) => request(`/model-endpoints/${id}/test`, { method: 'POST' }),

  /** 保存前先测一次（参数来自表单，不落库）。 */
  testInline: (payload) =>
    request('/model-endpoints/test-connection', { method: 'POST', body: payload }),
}
