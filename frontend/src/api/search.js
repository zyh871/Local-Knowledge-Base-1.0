/**
 * 联网搜索配置 API。
 *
 * 以前这些配置只能写在 `backend/.env` 里 —— 用户要自己去搜供应商、
 * 注册、拿 key、用记事本改文件、重启客户端，整个过程跨三个应用、六步，
 * 而且填错格式只有下一次搜索才报错。
 *
 * 现在这些都在界面里完成：填一次立即生效，不需要重启。
 */

import { request } from './http'

export const searchApi = {
  /** 当前配置：供应商、是否已配置、密钥掩码、可用供应商列表 */
  config: () => request('/search/config'),

  /** 保存（立即生效） */
  save: (provider, apiKey) =>
    request('/search/config', { method: 'PUT', body: { provider, api_key: apiKey } }),

  /** 清除界面里保存的配置（回退到环境变量 / 免密钥方案） */
  clear: () => request('/search/config', { method: 'DELETE' }),

  /** 真跑一次搜索，确认能不能用 */
  test: () => request('/search/test', { method: 'POST' }),
}
