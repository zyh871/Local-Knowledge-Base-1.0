<script setup>
/**
 * 联网搜索设置。
 *
 * 为什么单独做一个界面
 *   搜索工具以前只认环境变量里的 TAVILY_API_KEY。用户在对话框里看到
 *   「TAVILY_API_KEY not configured」时，除了去改 .env 没有任何出路 ——
 *   他甚至不知道该去哪家申请密钥。
 *
 * 这个面板把三件事摆到台面上：
 *   1. **免密钥方案**：DuckDuckGo 不用注册就能用，先保证功能可用；
 *   2. **要密钥的方案**：Tavily / Serper，写清楚去哪申请、免费额度多少；
 *   3. **测试按钮**：填完立刻知道对不对，不用等下一次搜索才发现填错。
 *
 * 保存后立即生效（不用重启）—— 这是相对「改 .env」最实在的一处差别。
 */
import { computed, onMounted, ref } from 'vue'

import { searchApi } from '../api/search'

const props = defineProps({
  /** 打开时为 true */
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const config = ref(null)
const provider = ref('duckduckgo')
const apiKey = ref('')
const saving = ref(false)
const testing = ref(false)
const message = ref('')
const messageType = ref('info')

const providers = computed(() => config.value?.providers || [])
const current = computed(() => providers.value.find((p) => p.id === provider.value) || null)
const needsKey = computed(() => !!current.value?.requires_key)
const configured = computed(() => !!config.value?.configured)

async function load() {
  try {
    const data = await searchApi.config()
    config.value = data
    provider.value = data.provider || 'duckduckgo'
  } catch (e) {
    messageType.value = 'error'
    message.value = e.message || '读取配置失败'
  }
}

async function save() {
  if (saving.value) return
  saving.value = true
  message.value = ''
  try {
    const data = await searchApi.save(provider.value, apiKey.value.trim())
    config.value = data
    apiKey.value = ''
    messageType.value = 'ok'
    message.value = '已保存，联网搜索立即生效（不需要重启）'
  } catch (e) {
    messageType.value = 'error'
    message.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function test() {
  if (testing.value) return
  testing.value = true
  message.value = ''
  try {
    const r = await searchApi.test()
    if (r.success) {
      messageType.value = 'ok'
      message.value = `连接正常：${r.elapsed_ms} 毫秒返回 ${r.result_count} 条结果`
    } else {
      messageType.value = 'error'
      message.value = `搜索失败：${r.error || '未知错误'}`
    }
  } catch (e) {
    messageType.value = 'error'
    message.value = e.message || '测试失败'
  } finally {
    testing.value = false
  }
}

async function reset() {
  try {
    const data = await searchApi.clear()
    config.value = data
    provider.value = data.provider || 'duckduckgo'
    apiKey.value = ''
    messageType.value = 'ok'
    message.value = '已清除界面配置，回到默认供应商'
  } catch (e) {
    messageType.value = 'error'
    message.value = e.message || '清除失败'
  }
}

function close() {
  emit('close')
}

onMounted(load)
</script>

<template>
  <div v-if="visible" class="modal-overlay-wrap">
    <div class="modal-overlay" @click="close"></div>
    <div class="modal-content search-modal">
      <div class="modal-header">
        <div class="min-w-0">
          <h3 class="modal-title">联网搜索</h3>
          <p class="modal-sub">
            {{ configured ? '已配置，保存后立即生效' : '还没配置 —— 选一个免密钥方案就能先用起来' }}
          </p>
        </div>
        <button class="modal-close-btn" title="关闭" @click="close">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="modal-body">
        <div class="field">
          <span class="field-label">搜索供应商</span>
          <div class="provider-list">
            <button
              v-for="p in providers"
              :key="p.id"
              type="button"
              class="provider-item"
              :class="{ on: provider === p.id }"
              @click="provider = p.id"
            >
              <span class="provider-name">
                {{ p.name }}
                <span v-if="p.configured" class="provider-ok" title="已配置">已配置</span>
              </span>
              <span class="provider-hint">{{ p.hint }}</span>
              <span class="provider-meta">
                <span v-if="p.free_tier">{{ p.free_tier }}</span>
                <a
                  v-if="p.requires_key"
                  :href="p.docs"
                  target="_blank"
                  rel="noopener noreferrer"
                  @click.stop
                  >申请 API Key ↗</a
                >
              </span>
            </button>
          </div>
        </div>

        <label v-if="needsKey" class="field">
          <span class="field-label">API Key</span>
          <input
            v-model="apiKey"
            class="field-input"
            type="password"
            autocomplete="new-password"
            :placeholder="config?.key_masked ? `留空表示不修改（当前 …${config.key_masked}）` : '粘贴你的 API Key'"
          />
          <span class="field-hint">
            只保存在本机数据库，界面与接口都只显示末 4 位。留空保存则沿用原来的密钥。
          </span>
        </label>

        <p v-if="message" :class="['search-msg', messageType === 'error' ? 'is-error' : 'is-ok']">
          {{ message }}
        </p>
      </div>

      <div class="modal-footer">
        <button class="mini-btn" @click="reset">清除配置</button>
        <button class="mini-btn" :disabled="testing" @click="test">
          {{ testing ? '测试中…' : '测试连接' }}
        </button>
        <button class="mini-btn primary" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 遮罩 + 内容：z-index 必须显式给，遮罩带 backdrop-filter 会自成一层压住内容 */
.modal-overlay-wrap {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-overlay {
  position: absolute;
  inset: 0;
  z-index: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal-content {
  position: relative;
  z-index: 1;
  width: 92%;
  max-width: 560px;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-elevated);
}

.modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--color-border);
}

.modal-title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--color-text-primary);
}

.modal-sub {
  margin: 4px 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.modal-close-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}

.modal-close-btn:hover {
  background: var(--color-hover, rgba(11, 17, 24, 0.06));
  color: var(--color-text-primary);
}

.modal-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.field { display: block; }

.field-label {
  display: block;
  margin-bottom: 6px;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text-secondary);
}

.field-input {
  width: 100%;
  padding: 9px 12px;
  font-size: var(--fs-sm);
  color: var(--color-text-primary);
  background: var(--color-input-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  outline: none;
}

.field-input:focus {
  border-color: var(--color-primary);
}

.field-hint {
  display: block;
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.provider-list { display: flex; flex-direction: column; gap: 8px; }

.provider-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;
}

.provider-item:hover { border-color: var(--color-primary); }

.provider-item.on {
  border-color: var(--color-primary);
  background: var(--color-brand-soft, rgba(26, 74, 122, 0.06));
}

.provider-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.provider-ok {
  font-size: 10.5px;
  font-weight: 500;
  color: var(--color-success, #2f7d5b);
  border: 1px solid currentColor;
  border-radius: 999px;
  padding: 0 6px;
}

.provider-hint {
  display: block;
  margin-top: 3px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.provider-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.provider-meta a { color: var(--color-primary); }

.search-msg {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
}

.search-msg.is-ok {
  color: var(--color-success, #2f7d5b);
  background: rgba(47, 125, 91, 0.08);
}

.search-msg.is-error {
  color: var(--color-error, #c0392b);
  background: rgba(192, 57, 43, 0.08);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--color-border);
}

.mini-btn {
  padding: 6px 12px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.mini-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.mini-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.mini-btn.primary {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}

.mini-btn.primary:hover:not(:disabled) { color: #fff; filter: brightness(1.06); }
</style>
