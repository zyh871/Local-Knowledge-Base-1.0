<template>
  <AppLayout>
    <!-- 精简侧栏：保持与其他页面一致的三栏骨架。
         少了这一栏，从侧边栏点进来后整页会像「布局坏了」。 -->
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <div class="sidebar-header p-5">
          <h1 class="sidebar-title mb-4 font-semibold">模型设置</h1>
          <button class="sidebar-back" @click="router.push('/work')">← 返回工作台</button>
        </div>
        <div class="sidebar-footer px-3 py-4">
          <nav class="flex flex-col gap-1">
            <button
              class="sidebar-nav-btn flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap"
              @click="toggleTheme"
            >
              {{ theme === 'light' ? '深色模式' : '浅色模式' }}
            </button>
            <button
              class="sidebar-nav-btn logout flex items-center gap-2.5 w-full px-3 py-2 text-sm whitespace-nowrap"
              @click="logout"
            >
              退出登录
            </button>
          </nav>
        </div>
      </aside>
    </template>

    <div class="ms-page max-w-3xl mx-auto">
      <header class="ms-head">
        <div>
          <h2 class="ms-title">模型设置</h2>
          <p class="ms-sub">
            接入任意 <strong>OpenAI 兼容</strong>的服务（DeepSeek / Moonshot / 自建网关…），
            填好接口地址、密钥与模型名称后，即可在对话与工作流里切换使用。
          </p>
        </div>
        <button v-if="allowed && !form" class="btn-primary" @click="startCreate">添加模型</button>
      </header>

      <p v-if="!allowed" class="ms-notice">
        当前部署不允许配置自定义模型（管理员可通过 <code>ALLOW_CUSTOM_MODELS=false</code> 关闭）。
        下方仅展示只读列表。
      </p>

      <p v-if="errorMessage" class="ms-error">{{ errorMessage }}</p>

      <!-- 新增 / 编辑表单 -->
      <section v-if="form" class="card">
        <h4 class="card-title">{{ editingId ? '编辑模型' : '添加模型' }}</h4>

        <div class="ms-form">
          <label class="ms-field">
            <span class="ms-label">显示名称</span>
            <input v-model="form.name" class="form-input" placeholder="例如：DeepSeek 官方" />
          </label>

          <label class="ms-field">
            <span class="ms-label">接口地址</span>
            <input
              v-model="form.base_url"
              class="form-input"
              placeholder="https://api.deepseek.com/v1"
            />
            <span class="ms-hint">
              要写完整的 Base URL（含 <code>/v1</code> 这类路径前缀）。
              各家服务商约定不同，程序**不会替你补** —— 猜错只会得到一个 404。
            </span>
          </label>

          <label class="ms-field">
            <span class="ms-label">API 密钥</span>
            <input
              v-model="form.api_key"
              class="form-input"
              type="password"
              autocomplete="new-password"
              :placeholder="
                editingId ? `留空则不修改（当前 ${form.api_key_masked}）` : 'sk-…'
              "
            />
            <span class="ms-hint">只保存在本机数据库；界面与接口都只显示末 4 位</span>
          </label>

          <label class="ms-field">
            <span class="ms-label">模型名称</span>
            <input v-model="form.model" class="form-input" placeholder="deepseek-chat" />
            <span class="ms-hint">填服务商文档里的模型标识，不是给人看的显示名</span>
          </label>
        </div>

        <p v-if="testResult" :class="['ms-test', testResult.ok ? 'ok' : 'bad']">
          <template v-if="testResult.ok">✓ 连接成功，模型可用</template>
          <template v-else>✕ {{ testResult.error || '连接失败' }}</template>
        </p>

        <div class="ms-form-actions">
          <button class="mini-btn" :disabled="busy || testing" @click="cancelEdit">取消</button>
          <button class="mini-btn" :disabled="busy || testing || !canTest" @click="runTest">
            {{ testing ? '测试中…' : '测试连接' }}
          </button>
          <button class="btn-primary" :disabled="busy || testing || !canSave" @click="save">
            {{ busy ? '保存中…' : '保存' }}
          </button>
        </div>
      </section>

      <!-- 已接入列表 -->
      <section class="card">
        <div class="card-head">
          <h4 class="card-title">已接入的模型（{{ endpoints.length }}）</h4>
          <button class="mini-btn" :disabled="loading" @click="load">刷新</button>
        </div>

        <p v-if="loading" class="card-hint">读取中…</p>
        <p v-else-if="!endpoints.length" class="card-hint">
          还没有接入自定义模型。系统内置的模型无需配置，直接在输入区的模型选择器里就能切换。
        </p>

        <div v-for="item in endpoints" :key="item.id" class="ms-row">
          <div class="ms-row-main">
            <div class="ms-row-title">
              <span class="ms-name">{{ item.name }}</span>
              <span :class="['ms-chip', item.enabled ? 'on' : 'off']">
                {{ item.enabled ? '已启用' : '已停用' }}
              </span>
              <span v-if="item.last_test_ok === true" class="ms-chip ok">测试通过</span>
              <span v-else-if="item.last_test_ok === false" class="ms-chip bad">测试失败</span>
              <span v-else class="ms-chip">未测试</span>
            </div>
            <p class="ms-row-meta">
              <code>{{ item.model }}</code> · {{ item.base_url }} · 密钥 {{ item.api_key_masked }}
            </p>
            <p v-if="item.last_test_ok === false && item.last_test_error" class="ms-row-err">
              {{ item.last_test_error }}
            </p>
          </div>

          <div class="ms-row-actions">
            <button class="mini-btn" :disabled="busy" @click="startEdit(item)">编辑</button>
            <button class="mini-btn" :disabled="busy" @click="toggleEnabled(item)">
              {{ item.enabled ? '停用' : '启用' }}
            </button>
            <button class="mini-btn" :disabled="busy" @click="runTestOn(item)">
              {{ busyId === item.id ? '测试中…' : '测试' }}
            </button>
            <button class="mini-btn danger" :disabled="busy" @click="confirmDelete(item)">
              {{ pendingDelete === item.id ? '确认删除？' : '删除' }}
            </button>
          </div>
        </div>
      </section>

      <section class="card">
        <h4 class="card-title">说明</h4>
        <ul class="ms-notes">
          <li>
            自定义模型只用于<strong>对话生成</strong>；检索用的嵌入模型不受影响 ——
            换嵌入模型会让向量维度不一致，必须重建整个知识库。
          </li>
          <li>
            密钥明文保存在本机数据库（<code>data/custom_models.db</code>），
            接口与界面都只返回末 4 位。多人共用本系统时建议关闭该功能。
          </li>
          <li>接口地址需要能被本机访问；跨境服务可能因网络原因连接缓慢或失败。</li>
        </ul>
      </section>
    </div>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { modelApi } from '../api/models'
import { useTheme } from '../composables/useTheme'

const router = useRouter()
const { theme, toggleTheme } = useTheme()

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  router.push('/login')
}

const endpoints = ref([])
const allowed = ref(true)
const loading = ref(false)
const busy = ref(false)
const busyId = ref('')
const errorMessage = ref('')
const pendingDelete = ref('')

/** 表单对象；为 null 表示表单关闭。 */
const form = ref(null)
/** 正在编辑的端点 id；空串表示新增。 */
const editingId = ref('')
const testResult = ref(null)
const testing = ref(false)

const canTest = computed(() => {
  const f = form.value
  if (!f) return false
  // 编辑已有端点时不重填密钥也应能测（用保存着的那把）
  return !!(f.base_url && f.model && (f.api_key || editingId.value))
})

const canSave = computed(() => {
  const f = form.value
  if (!f) return false
  return !!(
    f.name.trim() &&
    f.base_url.trim() &&
    f.model.trim() &&
    (f.api_key.trim() || editingId.value)
  )
})

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await modelApi.list()
    endpoints.value = data.endpoints || []
    allowed.value = data.enabled !== false
  } catch (e) {
    errorMessage.value = e.message || '读取失败'
  } finally {
    loading.value = false
  }
}

function startCreate() {
  form.value = {
    name: '',
    base_url: '',
    model: '',
    api_key: '',
    api_key_masked: '',
  }
  editingId.value = ''
  testResult.value = null
  pendingDelete.value = ''
}

function startEdit(item) {
  form.value = {
    name: item.name,
    base_url: item.base_url,
    model: item.model,
    api_key: '', // 留空 = 不修改
    api_key_masked: item.api_key_masked,
  }
  editingId.value = item.id
  testResult.value = null
  pendingDelete.value = ''
}

function cancelEdit() {
  form.value = null
  editingId.value = ''
  testResult.value = null
}

/** 客户端先挡掉明显错误，省一次往返；服务端仍会再校验。 */
function validate(f, { needKey }) {
  if (!f.name.trim()) return '请填写显示名称'
  if (!f.base_url.trim()) return '请填写接口地址'
  if (!/^https?:\/\//i.test(f.base_url.trim())) {
    return '接口地址要以 http:// 或 https:// 开头'
  }
  if (!f.model.trim()) return '请填写模型名称'
  if (needKey && !f.api_key.trim()) return '请填写 API 密钥'
  return ''
}

/** 表单上的「测试连接」：新建时用表单值，编辑时先存后测。 */
async function runTest() {
  const f = form.value
  if (!f || testing.value) return
  const invalid = validate(f, { needKey: !editingId.value })
  if (invalid) {
    testResult.value = { ok: false, error: invalid }
    return
  }
  testing.value = true
  testResult.value = null
  errorMessage.value = ''
  try {
    if (editingId.value && !f.api_key.trim()) {
      // 密钥留空时表单里没有可用凭据，只能测已保存的配置
      const saved = await modelApi.test(editingId.value)
      testResult.value = {
        ok: saved.last_test_ok === true,
        error: saved.last_test_error || '',
      }
    } else {
      const res = await modelApi.testInline({
        name: f.name,
        base_url: f.base_url,
        api_key: f.api_key,
        model: f.model,
      })
      testResult.value = { ok: res.ok, error: res.error }
    }
  } catch (e) {
    testResult.value = { ok: false, error: e.message || '测试失败' }
  } finally {
    testing.value = false
    await load()
  }
}

async function save() {
  const f = form.value
  if (!f || busy.value) return
  const invalid = validate(f, { needKey: !editingId.value })
  if (invalid) {
    errorMessage.value = invalid
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    if (editingId.value) {
      const payload = {
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        model: f.model.trim(),
      }
      // 留空表示保持原密钥 —— 不该为了改个名字被迫重贴一遍
      if (f.api_key.trim()) payload.api_key = f.api_key.trim()
      await modelApi.update(editingId.value, payload)
    } else {
      await modelApi.create({
        name: f.name.trim(),
        base_url: f.base_url.trim(),
        api_key: f.api_key.trim(),
        model: f.model.trim(),
      })
    }
    cancelEdit()
    await load()
  } catch (e) {
    errorMessage.value = e.message || '保存失败'
  } finally {
    busy.value = false
  }
}

async function runTestOn(item) {
  busyId.value = item.id
  errorMessage.value = ''
  try {
    await modelApi.test(item.id)
    await load()
  } catch (e) {
    errorMessage.value = e.message || '测试失败'
  } finally {
    busyId.value = ''
  }
}

async function toggleEnabled(item) {
  busy.value = true
  errorMessage.value = ''
  try {
    await modelApi.update(item.id, { enabled: !item.enabled })
    await load()
  } catch (e) {
    errorMessage.value = e.message || '操作失败'
  } finally {
    busy.value = false
  }
}

/**
 * 两步删除：这里存着密钥与地址，误删要重新配一遍，值得多问一次。
 * 不做弹窗 —— 二次点击确认足够，且不打断整页操作。
 */
async function confirmDelete(item) {
  if (pendingDelete.value !== item.id) {
    pendingDelete.value = item.id
    return
  }
  pendingDelete.value = ''
  busy.value = true
  errorMessage.value = ''
  try {
    await modelApi.remove(item.id)
    if (editingId.value === item.id) cancelEdit()
    await load()
  } catch (e) {
    errorMessage.value = e.message || '删除失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sidebar {
  background-color: var(--color-surface);
  border-right: 1px solid var(--color-border);
}

.sidebar-title {
  font-family: var(--font-display);
  font-size: var(--text-lg);
  color: var(--color-text-primary);
}

.sidebar-back {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  padding: 5px 9px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.sidebar-back:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.sidebar-footer {
  margin-top: auto;
  border-top: 1px solid var(--color-border);
}

.sidebar-nav-btn {
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  text-align: left;
}

.sidebar-nav-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.sidebar-nav-btn.logout:hover {
  color: var(--state-error);
}

.ms-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 24px 20px 48px;
  overflow-y: auto;
  height: 100%;
}

.ms-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.ms-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}

.ms-sub {
  margin-top: 6px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.ms-notice {
  padding: 10px 12px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
  line-height: 1.7;
}

.ms-error {
  padding: 8px 12px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.ms-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 12px;
}

.ms-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ms-label {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.ms-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

.form-input {
  width: 100%;
  padding: 7px 10px;
  font-size: var(--text-sm);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
}

.ms-test {
  margin-top: 12px;
  padding: 8px 10px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  line-height: 1.7;
}

.ms-test.ok {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.ms-test.bad {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.ms-form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}

.ms-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--color-border-light);
  flex-wrap: wrap;
}

.ms-row:first-of-type {
  border-top: none;
}

.ms-row-main {
  flex: 1;
  min-width: 220px;
}

.ms-row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ms-name {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.ms-chip {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.ms-chip.on,
.ms-chip.ok {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.ms-chip.off,
.ms-chip.bad {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.ms-row-meta {
  margin-top: 5px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.7;
  word-break: break-all;
}

.ms-row-err {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--state-error);
}

.ms-row-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ms-notes {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.9;
}

.btn-primary {
  padding: 7px 16px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
  white-space: nowrap;
}

.btn-primary[disabled],
.mini-btn[disabled] {
  opacity: 0.5;
  cursor: default;
}
</style>
