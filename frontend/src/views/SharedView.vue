<template>
  <div class="shared-page">
    <header class="shared-header">
      <div class="shared-header-inner">
        <div class="brand">
          <span class="brand-icon">📚</span>
          <span class="brand-title">知识库问答</span>
          <span class="brand-tag">只读分享</span>
        </div>
        <button v-if="data" @click="downloadImage" class="download-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          导出长图
        </button>
      </div>
    </header>

    <div ref="containerRef" class="shared-content">
      <div v-if="loading" class="state-block">加载中...</div>
      <div v-else-if="error" class="state-block state-error">{{ error }}</div>
      <template v-else-if="data">
        <h1 class="shared-title">{{ data.title || '对话' }}</h1>
        <p class="shared-meta">
          创建于 {{ formatDate(data.created_at) }} ·
          有效期至 {{ formatDate(data.expires_at) }}
        </p>
        <div class="shared-messages">
          <div
            v-for="(m, idx) in messagesList"
            :key="idx"
            :class="['shared-msg', m.role]"
          >
            <div class="shared-avatar">
              <span v-if="m.role === 'user'">U</span>
              <span v-else>AI</span>
            </div>
            <div class="shared-bubble">
              <pre class="shared-text">{{ m.content }}</pre>
              <div v-if="m.sources && m.sources.length" class="shared-sources">
                <p class="src-title">参考来源：</p>
                <ul>
                  <li v-for="(s, si) in m.sources" :key="si">
                    <template v-if="s.href || s.url">
                      <a :href="s.href || s.url" target="_blank" rel="noopener noreferrer">
                        {{ s.title || s.href || s.url }}
                      </a>
                    </template>
                    <template v-else>
                      {{ s.file }}
                    </template>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const token = route.params.token
const data = ref(null)
const loading = ref(true)
const error = ref('')
const containerRef = ref(null)

const messagesList = computed(() => {
  if (!data.value || !data.value.data) return []
  return data.value.data.messages || data.value.data.history || []
})

function formatDate(s) {
  if (!s) return ''
  try {
    return new Date(s).toLocaleString('zh-CN')
  } catch {
    return s
  }
}

async function load() {
  try {
    const res = await fetch(`/api/share/${encodeURIComponent(token)}`)
    if (!res.ok) {
      const d = await res.json().catch(() => ({}))
      error.value = d.detail || `加载失败 (${res.status})`
      return
    }
    data.value = await res.json()
  } catch (e) {
    error.value = '网络错误：' + e.message
  } finally {
    loading.value = false
  }
}

function wrapText(ctx, text, maxWidth) {
  const lines = []
  for (const para of text.split('\n')) {
    if (!para) {
      lines.push('')
      continue
    }
    let line = ''
    for (const ch of para) {
      const test = line + ch
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line)
        line = ch
      } else {
        line = test
      }
    }
    if (line) lines.push(line)
  }
  return lines
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

async function downloadImage() {
  if (!data.value || !containerRef.value) return
  const w = 760
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const msgs = messagesList.value
  const isDark = document.documentElement.classList.contains('dark')
  const fontFamily = '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'

  const measure = document.createElement('canvas').getContext('2d')
  measure.font = `14px ${fontFamily}`
  const bubbleW = w * 0.55
  let totalH = 24 + 36 + 8 + 24 + 12 + 16
  const heights = []
  for (const m of msgs) {
    const text = (m.content || '').trim()
    if (!text) {
      heights.push(0)
      continue
    }
    const lines = wrapText(measure, text, bubbleW - 24)
    const h = lines.length * 22 + 24 + 12
    heights.push(h)
    totalH += h
  }
  totalH += 40

  const canvas = document.createElement('canvas')
  canvas.width = w * dpr
  canvas.height = totalH * dpr
  const ctx = canvas.getContext('2d')
  ctx.scale(dpr, dpr)
  ctx.font = `14px ${fontFamily}`
  ctx.textBaseline = 'alphabetic'

  ctx.fillStyle = isDark ? '#18181b' : '#fafafa'
  ctx.fillRect(0, 0, w, totalH)

  let y = 24
  ctx.fillStyle = isDark ? '#fafafa' : '#18181b'
  ctx.font = `bold 20px ${fontFamily}`
  ctx.fillText(data.value.title || '对话', 24, y + 20)
  y += 36
  ctx.font = `12px ${fontFamily}`
  ctx.fillStyle = isDark ? '#a1a1aa' : '#71717a'
  ctx.fillText(
    `创建于 ${formatDate(data.value.created_at)}  ·  有效期至 ${formatDate(data.value.expires_at)}`,
    24,
    y + 12
  )
  y += 24
  ctx.strokeStyle = isDark ? '#3f3f46' : '#e4e4e7'
  ctx.beginPath()
  ctx.moveTo(24, y)
  ctx.lineTo(w - 24, y)
  ctx.stroke()
  y += 16

  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (!heights[i]) continue
    const isUser = m.role === 'user'
    const text = (m.content || '').trim()
    const lines = wrapText(ctx, text, bubbleW - 24)
    const bubbleH = heights[i]
    const avatarX = isUser ? w - 24 - 32 : 24
    const bubbleX = isUser ? avatarX - 16 - bubbleW : avatarX + 32 + 16

    ctx.fillStyle = isUser ? '#3b82f6' : '#71717a'
    ctx.beginPath()
    ctx.arc(avatarX + 16, y + 16, 16, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.font = `bold 12px ${fontFamily}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(isUser ? 'U' : 'AI', avatarX + 16, y + 16)
    ctx.textAlign = 'start'
    ctx.textBaseline = 'alphabetic'

    ctx.fillStyle = isUser ? '#3b82f6' : isDark ? '#27272a' : '#f4f4f5'
    roundRect(ctx, bubbleX, y, bubbleW, bubbleH, 10)
    ctx.fill()

    ctx.fillStyle = isUser ? '#ffffff' : isDark ? '#fafafa' : '#18181b'
    ctx.font = `14px ${fontFamily}`
    let ty = y + 12 + 16
    for (const line of lines) {
      ctx.fillText(line, bubbleX + 12, ty)
      ty += 22
    }
    y += bubbleH + 12
  }

  const blob = await new Promise((res) => canvas.toBlob(res, 'image/png'))
  if (!blob) {
    alert('生成图片失败')
    return
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${(data.value.title || 'shared-conversation').replace(
    /[<>:"/\\|?*]/g,
    '_'
  )}.png`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  await load()
  await nextTick()
})
</script>

<style scoped>
.shared-page {
  min-height: 100vh;
  background: #fafafa;
  color: #18181b;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
:global(html.dark) .shared-page {
  background: #18181b;
  color: #fafafa;
}

.shared-header {
  position: sticky;
  top: 0;
  z-index: 10;
  background: #fff;
  border-bottom: 1px solid #e4e4e7;
}
:global(html.dark) .shared-header {
  background: #1f1f23;
  border-color: #3f3f46;
}

.shared-header-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.brand-icon {
  font-size: 18px;
}
.brand-tag {
  font-size: 11px;
  padding: 2px 8px;
  background: #3b82f6;
  color: #fff;
  border-radius: 999px;
  font-weight: 500;
}
.download-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
}
.download-btn:hover {
  background: #2563eb;
}

.shared-content {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 20px 60px;
}
.state-block {
  text-align: center;
  padding: 60px 20px;
  color: #71717a;
}
.state-error {
  color: #ef4444;
}

.shared-title {
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 8px;
}
.shared-meta {
  font-size: 12px;
  color: #71717a;
  margin: 0 0 24px;
}

.shared-messages {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.shared-msg {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.shared-msg.user {
  flex-direction: row-reverse;
}
.shared-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #3b82f6;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.shared-msg.assistant .shared-avatar {
  background: #71717a;
}
.shared-bubble {
  flex: 1;
  min-width: 0;
  background: #fff;
  border: 1px solid #e4e4e7;
  border-radius: 12px;
  padding: 12px 14px;
  max-width: 80%;
}
:global(html.dark) .shared-bubble {
  background: #27272a;
  border-color: #3f3f46;
}
.shared-msg.user .shared-bubble {
  background: #3b82f6;
  color: #fff;
  border-color: transparent;
}

.shared-text {
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.shared-sources {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(127, 127, 127, 0.25);
  font-size: 12px;
}
.src-title {
  margin: 0 0 4px;
  opacity: 0.8;
}
.shared-sources ul {
  margin: 0;
  padding-left: 18px;
}
.shared-sources li {
  margin: 2px 0;
}
.shared-sources a {
  color: inherit;
}
</style>