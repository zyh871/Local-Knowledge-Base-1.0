/**
 * AI 创作客户端（文生图 / 文生视频）。
 *
 * ⚠️ 媒体必须是**带鉴权头取回来的 blob**，不能直接把 `/creations/{id}/file`
 * 塞进 `<img src>` / `<video src>` —— 浏览器加载媒体时不会带 `Authorization`，
 * 结果是一整面 401 裂图。`mediaBlobUrl()` 负责这一层并返回可直接绑定的
 * `blob:` 地址；调用方在媒体不再使用时**必须** `URL.revokeObjectURL`。
 */

import { API_BASE, authHeaders, query, request } from './http'

export const creationApi = {
  /** 模型 / 比例 / 风格 / 视频能力，一次拿全。 */
  options: () => request('/creations/options'),

  list: (limit = 30) => request(`/creations${query({ limit })}`),

  /** 生成图片。**同步等待出图**（通常 10-40 秒）。 */
  generate: (payload) => request('/creations', { method: 'POST', body: payload }),

  /** 提交视频任务。**立即返回**一条 status=running 的记录。 */
  generateVideo: (payload) => request('/creations/videos', { method: 'POST', body: payload }),

  /**
   * 图生图（图像编辑）。multipart：源图 + 编辑指令。
   * 与 `generate` 的分工是「改一张已有图」vs「从零生成」。
   */
  editImage: (formData) => request('/creations/edits', { method: 'POST', body: formData }),

  /** 图生视频。与 `generateVideo` 一样立即返回任务记录。 */
  videoFromImage: (formData) =>
    request('/creations/videos/from-image', { method: 'POST', body: formData }),

  /** 查询单条记录（视频轮询用；后端会在查询时向上游推进状态）。 */
  get: (id) => request(`/creations/${id}`),

  remove: (id) => request(`/creations/${id}`, { method: 'DELETE' }),

  /** 上传参考图，换回一段画面描述（会拼进提示词）。 */
  describeReference: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request('/creations/reference', { method: 'POST', body: form })
  },

  /** 取回图片 / 视频并转成可直接给 <img> / <video> 用的 blob 地址。 */
  async mediaBlobUrl(id) {
    const res = await fetch(`${API_BASE}/creations/${id}/file`, {
      headers: { ...authHeaders() },
    })
    if (!res.ok) {
      throw new Error(`媒体加载失败（HTTP ${res.status}）`)
    }
    const blob = await res.blob()
    return URL.createObjectURL(blob)
  },

  /** 兼容旧调用名。 */
  imageBlobUrl(id) {
    return creationApi.mediaBlobUrl(id)
  },

  /** 下载到本地。走同一个鉴权接口，拿 blob 再触发另存为。 */
  async download(id, filename) {
    const url = await creationApi.mediaBlobUrl(id)
    const link = document.createElement('a')
    link.href = url
    link.download = filename || id
    document.body.appendChild(link)
    link.click()
    link.remove()
    // 下载是异步开始的，立刻回收会让部分浏览器拿到空文件
    setTimeout(() => URL.revokeObjectURL(url), 15_000)
  },
}
