<template>
  <div v-if="visible" class="ad-wrap">
    <div class="ad-backdrop" @click="close"></div>
    <aside class="ad-drawer">
      <header class="ad-head">
        <div class="ad-title">
          <h3>交付物</h3>
          <span class="ad-count">{{ files.length }} 个文件</span>
        </div>
        <button class="ad-close" @click="close" title="关闭（Esc）">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </header>

      <div class="ad-body">
        <!-- 左：文件列表 -->
        <nav class="ad-list">
          <p v-if="!files.length" class="ad-empty">本次任务还没有产出文件</p>
          <button
            v-for="item in files"
            :key="item.id"
            class="ad-item"
            :class="{ active: item.id === activeId }"
            @click="select(item)"
          >
            <span class="ad-icon">{{ iconOf(item.filename) }}</span>
            <span class="ad-item-main">
              <span class="ad-name" :title="item.filename">{{ item.filename }}</span>
              <span class="ad-meta">{{ formatBytes(item.size_bytes) }}</span>
            </span>
          </button>
        </nav>

        <!-- 右：预览 -->
        <section class="ad-view">
          <template v-if="!active">
            <div class="ad-placeholder">
              <p>选择左侧文件预览</p>
              <p class="ad-tip">文本直接在应用内打开，不需要跳出浏览器</p>
            </div>
          </template>

          <template v-else>
            <div class="ad-view-head">
              <span class="ad-view-name" :title="active.filename">{{ active.filename }}</span>
              <div class="ad-actions">
                <button class="mini-btn" :disabled="loading" @click="reload">重新加载</button>
                <button class="mini-btn primary" :disabled="saving" @click="download">
                  {{ saving ? '保存中…' : '下载' }}
                </button>
              </div>
            </div>

            <p v-if="error" class="ad-error">{{ error }}</p>
            <p v-else-if="loading" class="ad-loading">正在读取…</p>

            <template v-else-if="preview">
              <p v-if="preview.truncated" class="ad-warn">
                文件较大，仅显示前 {{ formatBytes(2 * 1024 * 1024) }}，完整内容请下载
              </p>

              <!-- 图片 -->
              <div v-if="preview.kind === 'image'" class="ad-image">
                <img v-if="imageUrl" :src="imageUrl" :alt="active.filename" />
              </div>

              <!-- Markdown -->
              <div
                v-else-if="preview.language === 'markdown'"
                class="ad-markdown markdown-body"
                v-html="renderedMarkdown"
              ></div>

              <!-- 其它文本 -->
              <pre v-else-if="preview.kind === 'text'" class="ad-pre">{{ preview.text }}</pre>

              <!-- 二进制 -->
              <div v-else class="ad-placeholder">
                <p>{{ preview.message || '该类型不支持预览' }}</p>
                <button class="mini-btn primary" @click="download">下载后查看</button>
              </div>
            </template>
          </template>
        </section>
      </div>
    </aside>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import { API_BASE, authHeaders } from '../../api/http'

const props = defineProps({
  visible: { type: Boolean, default: false },
  //: 产物列表：[{ id, filename, size_bytes }]
  files: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const activeId = ref('')
const preview = ref(null)
const imageUrl = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const active = computed(() => props.files.find((f) => f.id === activeId.value) || null)
const renderedMarkdown = computed(() =>
  preview.value?.text ? marked.parse(preview.value.text) : ''
)

function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function iconOf(name = '') {
  const ext = name.split('.').pop().toLowerCase()
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return '🖼'
  if (['md', 'markdown'].includes(ext)) return '📝'
  if (['json', 'yaml', 'yml', 'toml', 'csv'].includes(ext)) return '📊'
  if (['py', 'js', 'ts', 'vue', 'html', 'css', 'sql'].includes(ext)) return '⌨'
  return '📄'
}

function releaseImage() {
  if (imageUrl.value) {
    URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = ''
  }
}

async function loadPreview(item) {
  releaseImage()
  preview.value = null
  error.value = ''
  loading.value = true
  try {
    const res = await fetch(`${API_BASE}/workflow/files/${item.id}/preview`, {
      headers: authHeaders(),
    })
    if (!res.ok) throw new Error(`读取失败（HTTP ${res.status}）`)
    const data = await res.json()
    preview.value = data

    if (data.kind === 'image') {
      const raw = await fetch(`${API_BASE}/workflow/files/${item.id}/preview/raw`, {
        headers: authHeaders(),
      })
      if (!raw.ok) throw new Error(`图片读取失败（HTTP ${raw.status}）`)
      imageUrl.value = URL.createObjectURL(await raw.blob())
    }
  } catch (e) {
    error.value = e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

function select(item) {
  activeId.value = item.id
  loadPreview(item)
}

function reload() {
  if (active.value) loadPreview(active.value)
}

/**
 * 保存文件。
 *
 * 桌面客户端里 WebView2 没有接下载事件，`a[download]` 点了往往没反应；
 * 所以优先走 pywebview 暴露的原生「另存为」对话框，浏览器里再退回 blob 下载。
 */
async function download() {
  if (!active.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const item = active.value
    const res = await fetch(`${API_BASE}/workflow/files/${item.id}/download`, {
      headers: authHeaders(),
    })
    if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`)
    const blob = await res.blob()

    const bridge = window.pywebview?.api?.save_file
    if (typeof bridge === 'function') {
      const buffer = await blob.arrayBuffer()
      const base64 = btoa(String.fromCharCode(...new Uint8Array(buffer)))
      const saved = await bridge(item.filename, base64)
      if (saved === false) {
        // 用户在对话框里取消了，不算错误
        return
      }
      return
    }

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = item.filename || 'download'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

function close() {
  emit('close')
}

function onKey(e) {
  if (e.key === 'Escape' && props.visible) close()
}

watch(
  () => props.visible,
  (open) => {
    if (open && props.files.length && !activeId.value) select(props.files[0])
    if (!open) {
      releaseImage()
      preview.value = null
      activeId.value = ''
    }
  }
)

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  releaseImage()
})
</script>

<style scoped>
.ad-wrap {
  position: fixed;
  inset: 0;
  z-index: 60;
}

.ad-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 32, 0.28);
}

.ad-drawer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(760px, 82vw);
  display: flex;
  flex-direction: column;
  background: var(--color-surface, #fff);
  border-left: 1px solid var(--color-border);
  box-shadow: -12px 0 32px rgba(15, 23, 32, 0.12);
  animation: ad-slide 0.18s ease-out;
}

@keyframes ad-slide {
  from { transform: translateX(24px); opacity: 0.6; }
  to { transform: translateX(0); opacity: 1; }
}

.ad-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--color-border);
}

.ad-title { display: flex; align-items: baseline; gap: 10px; }
.ad-title h3 { margin: 0; font-size: var(--fs-base, 15px); font-weight: 600; }
.ad-count { font-size: var(--text-xs); color: var(--color-text-tertiary); }

.ad-close {
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.ad-close:hover { background: var(--color-hover); }

.ad-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

.ad-list {
  width: 244px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  overflow-y: auto;
  padding: 8px;
}

.ad-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.ad-item:hover { background: var(--color-hover); }
.ad-item.active { background: rgba(26, 74, 122, 0.08); }

.ad-icon { font-size: 15px; flex-shrink: 0; }
.ad-item-main { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ad-name {
  font-size: var(--text-xs);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ad-meta { font-size: 11px; color: var(--color-text-tertiary); }

.ad-view {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.ad-view-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--color-border);
}
.ad-view-name {
  font-size: var(--text-xs);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ad-actions { display: flex; gap: 8px; flex-shrink: 0; }

.ad-markdown,
.ad-pre {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 18px;
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text-primary);
}

.ad-pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}

.ad-image {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px;
  display: flex;
  align-items: flex-start;
  justify-content: center;
}
.ad-image img { max-width: 100%; border-radius: 8px; border: 1px solid var(--color-border); }

.ad-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
}

.ad-tip { color: var(--color-text-tertiary); opacity: 0.75; }
.ad-empty { padding: 20px; text-align: center; color: var(--color-text-tertiary); font-size: var(--text-xs); }
.ad-loading { padding: 16px 18px; font-size: var(--text-xs); color: var(--color-text-tertiary); }
.ad-error { padding: 12px 18px; font-size: var(--text-xs); color: var(--color-error, #c0392b); }
.ad-warn { margin: 10px 18px 0; font-size: 11px; color: var(--color-warning, #9a6b00); }
</style>
