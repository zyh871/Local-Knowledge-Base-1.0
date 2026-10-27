<template>
  <AppLayout>
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <div class="sidebar-header p-5">
          <h1 class="sidebar-title mb-4 font-semibold">模型广场</h1>
          <button class="sidebar-back" @click="router.push('/work')">← 返回工作台</button>
        </div>

        <nav class="mp-side-nav px-3">
          <button
            :class="['mp-side-item', { active: navActive === 'plaza' }]"
            @click="switchNav('plaza')"
          >
            <span>模型广场</span>
          </button>
          <button
            :class="['mp-side-item', { active: navActive === 'mine' }]"
            @click="switchNav('mine')"
          >
            <span>我的接入</span>
            <span class="mp-side-count">{{ endpoints.length }}</span>
          </button>
        </nav>

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

    <div class="mp-page">
      <!-- 移动端页签（桌面由侧栏承担） -->
      <div class="mp-mobile-tabs lg:hidden">
        <button
          :class="{ active: navActive === 'plaza' }"
          @click="switchNav('plaza')"
        >
          模型广场
        </button>
        <button
          :class="{ active: navActive === 'mine' }"
          @click="switchNav('mine')"
        >
          我的接入 ({{ endpoints.length }})
        </button>
      </div>

      <header class="mp-head">
        <div class="min-w-0">
          <nav v-if="crumbs.length" class="mp-breadcrumb" aria-label="面包屑">
            <template v-for="(crumb, i) in crumbs" :key="`${crumb.label}-${i}`">
              <button
                v-if="crumb.target"
                class="mp-crumb"
                @click="goTarget(crumb.target)"
              >{{ crumb.label }}</button>
              <span v-else class="mp-crumb current">{{ crumb.label }}</span>
              <span v-if="i < crumbs.length - 1" class="mp-crumb-sep">/</span>
            </template>
          </nav>
          <h2 class="mp-title">{{ pageTitle }}</h2>
          <p class="mp-sub">{{ pageSubtitle }}</p>
        </div>
        <button
          v-if="view === 'vendor' || view === 'model'"
          class="mp-back-btn"
          @click="goBack"
        >← 返回</button>
      </header>

      <p v-if="!allowed" class="mp-notice">
        当前部署不允许配置自定义模型（管理员可通过 <code>ALLOW_CUSTOM_MODELS=false</code> 关闭）。
        你仍可浏览模型广场与已接入列表。
      </p>
      <p v-if="errorMessage" class="mp-error">{{ errorMessage }}</p>

      <!-- ① 厂商网格 -->
      <template v-if="view === 'plaza'">
        <div class="vendor-grid">
          <button
            v-for="vendor in vendors"
            :key="vendor.id"
            class="vendor-card"
            @click="openVendor(vendor)"
          >
            <span class="vendor-icon">
              <img
                v-if="vendor.logo"
                :src="theme === 'dark' ? vendor.logo.dark : vendor.logo.light"
                :alt="`${vendor.name} 标识`"
                class="vendor-logo"
                loading="lazy"
              />
              <span v-else class="vendor-logo-fallback" :style="{ background: vendor.color }">{{ vendor.icon }}</span>
            </span>
            <span class="vendor-body">
              <span class="vendor-name-row">
                <span class="vendor-name">{{ vendor.name }}</span>
                <span v-if="vendor.en && vendor.en !== vendor.name" class="vendor-en">{{ vendor.en }}</span>
              </span>
              <span class="vendor-desc">{{ vendor.desc }}</span>
              <span class="vendor-meta">
                <span>{{ vendor.models.length }} 个模型</span>
                <span v-if="connectedCount(vendor)" class="vendor-connected">
                  {{ connectedCount(vendor) }} 个已接入
                </span>
              </span>
            </span>
            <span class="vendor-arrow">→</span>
          </button>
        </div>
        <p class="mp-footnote">
          模型清单、模型 ID 与接口地址以厂商开放平台文档为准；接入要求服务兼容 OpenAI 规范。
        </p>
      </template>

      <!-- ② 厂商详情 -->
      <template v-else-if="view === 'vendor' && activeVendor">
        <section class="vendor-hero">
          <span class="vendor-icon lg">
            <img
              v-if="activeVendor.logo"
              :src="theme === 'dark' ? activeVendor.logo.dark : activeVendor.logo.light"
              :alt="`${activeVendor.name} 标识`"
              class="vendor-logo"
            />
            <span v-else class="vendor-logo-fallback" :style="{ background: activeVendor.color }">{{ activeVendor.icon }}</span>
          </span>
          <div class="min-w-0">
            <h3 class="vendor-hero-title">
              {{ activeVendor.name }}
              <span v-if="activeVendor.en && activeVendor.en !== activeVendor.name" class="vendor-en">{{ activeVendor.en }}</span>
            </h3>
            <p class="vendor-hero-desc">{{ activeVendor.desc }}</p>
            <p class="vendor-base">
              Base URL：<code>{{ activeVendor.baseUrl || '以官方文档为准' }}</code>
            </p>
            <p v-if="activeVendor.note" class="vendor-note">{{ activeVendor.note }}</p>
          </div>
        </section>

        <div class="model-grid">
          <article
            v-for="model in activeVendor.models"
            :key="model.id"
            class="model-card"
          >
            <div class="model-card-head">
              <div class="min-w-0">
                <p class="model-name">{{ model.name }}</p>
                <p class="model-id"><code>{{ model.id }}</code></p>
              </div>
              <span v-if="endpointFor(activeVendor, model)" class="model-connected">已接入</span>
            </div>

            <div class="type-chips">
              <span
                v-for="type in model.types"
                :key="type"
                :class="['type-chip', `type-${type}`]"
              >{{ modelTypeLabel(type) }}</span>
            </div>

            <p v-if="model.note" class="model-note">{{ model.note }}</p>

            <button class="model-action" @click="openModel(activeVendor, model)">
              {{ endpointFor(activeVendor, model) ? '编辑接入' : '接入此模型' }}
            </button>
          </article>
        </div>
      </template>

      <!-- ③ 接入配置 -->
      <template v-else-if="view === 'model' && form && activeVendor && activeModel">
        <div class="config-layout">
          <section class="config-card">
            <div class="config-head">
              <span class="vendor-icon sm">
                <img
                  v-if="activeVendor.logo"
                  :src="theme === 'dark' ? activeVendor.logo.dark : activeVendor.logo.light"
                  :alt="`${activeVendor.name} 标识`"
                  class="vendor-logo"
                />
                <span v-else class="vendor-logo-fallback" :style="{ background: activeVendor.color }">{{ activeVendor.icon }}</span>
              </span>
              <div class="min-w-0">
                <h3 class="config-title">{{ activeModel.name }}</h3>
                <p class="config-sub"><code>{{ activeModel.id }}</code> · {{ activeVendor.name }}</p>
              </div>
              <span v-if="editingId" class="model-connected">编辑已有接入</span>
            </div>

            <div class="type-chips config-types">
              <span
                v-for="type in activeModel.types"
                :key="type"
                :class="['type-chip', `type-${type}`]"
              >{{ modelTypeLabel(type) }}</span>
            </div>

            <form class="config-form" @submit.prevent="save">
              <label class="mp-field">
                <span class="mp-label">显示名称</span>
                <input v-model="form.name" class="mp-input" placeholder="例如：DeepSeek 官方" />
                <span class="mp-hint">在对话页模型选择器里显示的名字。</span>
              </label>

              <label class="mp-field">
                <span class="mp-label">Base URL</span>
                <input
                  v-model="form.base_url"
                  class="mp-input"
                  placeholder="https://api.example.com/v1"
                />
                <span class="mp-hint">
                  需兼容 OpenAI 规范（<code>POST /chat/completions</code>）。
                  要写完整的 Base URL，程序不会替你补路径。
                  <template v-if="activeVendor.id === 'claude' || !activeVendor.baseUrl">
                    该厂商需使用 OpenAI 兼容网关地址。
                  </template>
                </span>
              </label>

              <label class="mp-field">
                <span class="mp-label">API Key</span>
                <input
                  v-model="form.api_key"
                  class="mp-input"
                  type="password"
                  autocomplete="new-password"
                  :placeholder="editingId ? `留空则不修改（当前 ${form.api_key_masked}）` : 'sk-…'"
                />
                <span class="mp-hint">只保存在本机数据库，界面与接口都只显示末 4 位。</span>
              </label>

              <label class="mp-field">
                <span class="mp-label">模型 ID</span>
                <input v-model="form.model" class="mp-input" placeholder="deepseek-chat" />
                <span class="mp-hint">必须是厂商文档里的模型标识，不是显示名。</span>
              </label>

              <p v-if="testResult" :class="['mp-test', testResult.ok ? 'ok' : 'bad']">
                <template v-if="testResult.ok">✓ 连接成功，模型可用</template>
                <template v-else>✕ {{ testResult.error || '连接失败' }}</template>
              </p>

              <div class="config-actions">
                <button type="button" class="mp-btn ghost" @click="goBack">返回</button>
                <button
                  type="button"
                  class="mp-btn"
                  :disabled="busy || testing || !allowed"
                  @click="runTest"
                >{{ testing ? '测试中…' : '测试连接' }}</button>
                <button
                  type="submit"
                  class="mp-btn primary"
                  :disabled="busy || testing || !canSave || !allowed"
                >{{ busy ? '接入中…' : '保存并接入' }}</button>
              </div>
            </form>
          </section>

          <aside class="config-aside">
            <h4>接入说明</h4>
            <ul>
              <li>
                服务需兼容 OpenAI：请求 <code>POST {base_url}/chat/completions</code>，
                携带 <code>Authorization: Bearer &lt;key&gt;</code>。
              </li>
              <li>模型 ID 必须与厂商文档一致，填错只会得到 404 或模型不存在。</li>
              <li>密钥明文保存在本机数据库（<code>data/custom_models.db</code>），接口只回传末 4 位。</li>
              <li>接入成功后，可在对话页输入区的模型选择器中切换使用。</li>
              <li>自定义端点只用于对话生成，不会替换检索用的嵌入模型。</li>
            </ul>
          </aside>
        </div>
      </template>

      <!-- ④ 我的接入 -->
      <template v-else-if="view === 'mine'">
        <section class="config-card">
          <div class="card-head">
            <h4 class="config-title">已接入的模型（{{ endpoints.length }}）</h4>
            <button class="mp-btn sm" :disabled="loading" @click="load">
              {{ loading ? '读取中…' : '刷新' }}
            </button>
          </div>

          <p v-if="!loading && !endpoints.length" class="card-hint">
            还没有接入自定义模型。去「模型广场」选一个厂商和模型开始接入；
            系统内置模型无需配置，可直接在对话页切换。
          </p>

          <div v-for="item in endpoints" :key="item.id" class="mp-row">
            <div class="mp-row-main">
              <div class="mp-row-title">
                <span class="mp-name">{{ item.name }}</span>
                <span :class="['mp-chip', item.enabled ? 'on' : 'off']">
                  {{ item.enabled ? '已启用' : '已停用' }}
                </span>
                <span v-if="item.last_test_ok === true" class="mp-chip ok">测试通过</span>
                <span v-else-if="item.last_test_ok === false" class="mp-chip bad">测试失败</span>
                <span v-else class="mp-chip">未测试</span>
              </div>
              <p class="mp-row-meta">
                <code>{{ item.model }}</code> · {{ item.base_url }} · 密钥 {{ item.api_key_masked }}
              </p>
              <p v-if="item.last_test_ok === false && item.last_test_error" class="mp-row-err">
                {{ item.last_test_error }}
              </p>
            </div>

            <div class="mp-row-actions">
              <button class="mp-btn sm" :disabled="busy" @click="editEndpoint(item)">编辑</button>
              <button class="mp-btn sm" :disabled="busy" @click="toggleEnabled(item)">
                {{ item.enabled ? '停用' : '启用' }}
              </button>
              <button class="mp-btn sm" :disabled="busy" @click="runTestOn(item)">
                {{ busyId === item.id ? '测试中…' : '测试' }}
              </button>
              <button class="mp-btn sm danger" :disabled="busy" @click="confirmDelete(item)">
                {{ pendingDelete === item.id ? '确认删除？' : '删除' }}
              </button>
            </div>
          </div>
        </section>

        <section class="config-card">
          <h4 class="config-title">说明</h4>
          <ul class="mp-notes">
            <li>自定义模型只用于对话生成；检索用的嵌入模型不受影响，换嵌入模型需重建向量库。</li>
            <li>密钥明文保存在本机数据库，接口与界面只返回末 4 位；多人共用时建议关闭该功能。</li>
            <li>接口地址需要能被本机访问；跨境服务可能因网络原因连接缓慢或失败。</li>
          </ul>
        </section>
      </template>
    </div>

    <teleport to="body">
      <transition name="toast-fade">
        <div v-if="toast.visible" class="app-toast" role="status">{{ toast.message }}</div>
      </transition>
    </teleport>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import { modelApi } from '../api/models'
import { useTheme } from '../composables/useTheme'
import {
  CUSTOM_VENDOR,
  MODEL_VENDORS,
  modelTypeLabel,
} from '../data/modelVendors'

const router = useRouter()
const { theme, toggleTheme } = useTheme()

const vendors = MODEL_VENDORS

// ── 视图状态：plaza / vendor / model / mine ──────────────────
const view = ref('plaza')
/** 从「我的接入」进入配置页时，返回应回到 mine，而不是厂商详情。 */
const returnView = ref('vendor')
const activeVendor = ref(null)
const activeModel = ref(null)

// ── 数据 ────────────────────────────────────────────────────
const endpoints = ref([])
const allowed = ref(true)
const loading = ref(false)
const busy = ref(false)
const busyId = ref('')
const pendingDelete = ref('')
const errorMessage = ref('')

const form = ref(null)
const editingId = ref('')
const testResult = ref(null)
const testing = ref(false)

const toast = reactive({ visible: false, message: '' })
let toastTimer = null

// ── 派生 ────────────────────────────────────────────────────
const navActive = computed(() => {
  if (view.value === 'mine') return 'mine'
  if (view.value === 'model' && returnView.value === 'mine') return 'mine'
  return 'plaza'
})

const pageTitle = computed(() => {
  if (view.value === 'vendor') return activeVendor.value?.name || '厂商详情'
  if (view.value === 'model') return activeModel.value?.name || '接入配置'
  if (view.value === 'mine') return '我的接入'
  return '模型广场'
})

const pageSubtitle = computed(() => {
  if (view.value === 'vendor') return activeVendor.value?.desc || ''
  if (view.value === 'model') {
    return '填写 API Key、Base URL 与模型 ID，测试通过后即可接入。'
  }
  if (view.value === 'mine') {
    return '管理已接入的模型端点：启用/停用、测试连通性、编辑或删除。'
  }
  return '选择厂商，查看旗下模型的能力类型，并按 OpenAI 兼容规范完成接入。'
})

const crumbs = computed(() => {
  if (view.value === 'vendor') {
    return [
      { label: '模型广场', target: 'plaza' },
      { label: activeVendor.value?.name || '' },
    ]
  }
  if (view.value === 'model') {
    if (returnView.value === 'mine') {
      return [
        { label: '我的接入', target: 'mine' },
        { label: activeModel.value?.name || '' },
      ]
    }
    return [
      { label: '模型广场', target: 'plaza' },
      { label: activeVendor.value?.name || '', target: 'vendor' },
      { label: activeModel.value?.name || '' },
    ]
  }
  return []
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

// ── 导航 ────────────────────────────────────────────────────
function openVendor(vendor) {
  activeVendor.value = vendor
  activeModel.value = null
  errorMessage.value = ''
  view.value = 'vendor'
}

function endpointFor(vendor, model) {
  return endpoints.value.find((e) => e.model === model.id) || null
}

function connectedCount(vendor) {
  return vendor.models.filter((m) => endpointFor(vendor, m)).length
}

function openModel(vendor, model) {
  const existing = endpointFor(vendor, model)
  activeVendor.value = vendor
  activeModel.value = model
  editingId.value = existing?.id || ''
  returnView.value = 'vendor'
  form.value = {
    name: existing?.name || `${vendor.name} · ${model.name}`,
    base_url: existing?.base_url || vendor.baseUrl || '',
    model: existing?.model || model.id,
    api_key: '',
    api_key_masked: existing?.api_key_masked || '',
  }
  testResult.value = null
  errorMessage.value = ''
  view.value = 'model'
}

function editEndpoint(item) {
  const vendor =
    vendors.find(
      (v) =>
        v.baseUrl &&
        item.base_url.replace(/\/+$/, '').startsWith(v.baseUrl.replace(/\/+$/, '')),
    ) || CUSTOM_VENDOR
  const model =
    vendor.models.find((m) => m.id === item.model) ||
    { id: item.model, name: item.name, types: ['chat'] }

  activeVendor.value = vendor
  activeModel.value = model
  editingId.value = item.id
  returnView.value = 'mine'
  form.value = {
    name: item.name,
    base_url: item.base_url,
    model: item.model,
    api_key: '',
    api_key_masked: item.api_key_masked,
  }
  testResult.value = null
  errorMessage.value = ''
  view.value = 'model'
}

function switchNav(target) {
  errorMessage.value = ''
  testResult.value = null
  form.value = null
  activeVendor.value = null
  activeModel.value = null
  if (target === 'mine') {
    view.value = 'mine'
    load()
  } else {
    view.value = 'plaza'
  }
}

function goTarget(target) {
  if (target === 'plaza') {
    view.value = 'plaza'
    activeVendor.value = null
    activeModel.value = null
  } else if (target === 'mine') {
    view.value = 'mine'
  } else if (target === 'vendor' && activeVendor.value) {
    view.value = 'vendor'
  }
}

function goBack() {
  errorMessage.value = ''
  testResult.value = null
  if (view.value === 'model') {
    view.value = returnView.value === 'mine' ? 'mine' : 'vendor'
  } else if (view.value === 'vendor') {
    view.value = 'plaza'
    activeVendor.value = null
  }
}

// ── 数据加载 ────────────────────────────────────────────────
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

// ── 表单动作 ────────────────────────────────────────────────
function showToast(message) {
  toast.message = message
  toast.visible = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.visible = false
  }, 1900)
}

function validateForm(f) {
  if (!f.name.trim()) return '请填写显示名称'
  if (!f.base_url.trim()) return '请填写 Base URL'
  if (!/^https?:\/\//i.test(f.base_url.trim())) {
    return 'Base URL 要以 http:// 或 https:// 开头'
  }
  if (!f.model.trim()) return '请填写模型 ID'
  if (!editingId.value && !f.api_key.trim()) return '请填写 API Key'
  return ''
}

async function runTest() {
  const f = form.value
  if (!f || testing.value) return
  const invalid = validateForm(f)
  if (invalid) {
    testResult.value = { ok: false, error: invalid }
    return
  }
  testing.value = true
  testResult.value = null
  errorMessage.value = ''
  try {
    if (editingId.value && !f.api_key.trim()) {
      // 密钥留空时没有可用凭据，只能测已保存的配置
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
  const invalid = validateForm(f)
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
      // 留空表示保持原密钥
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
    await load()
    showToast(`已接入「${f.name.trim()}」`)
    view.value = returnView.value === 'mine' ? 'mine' : 'vendor'
    editingId.value = ''
  } catch (e) {
    errorMessage.value = e.message || '接入失败'
  } finally {
    busy.value = false
  }
}

// ── 我的接入：管理动作 ──────────────────────────────────────
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
    await load()
    showToast('已删除该接入')
  } catch (e) {
    errorMessage.value = e.message || '删除失败'
  } finally {
    busy.value = false
  }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  router.push('/login')
}

onMounted(load)
onUnmounted(() => {
  if (toastTimer) clearTimeout(toastTimer)
})
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

/* ── 侧栏页签 ─────────────────────────────────────────────── */
.mp-side-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.mp-side-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  text-align: left;
}

.mp-side-item:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mp-side-item.active {
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
  font-weight: 500;
}

.mp-side-count {
  min-width: 18px;
  padding: 0 6px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

.mp-side-item.active .mp-side-count {
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.mp-mobile-tabs {
  display: flex;
  gap: 6px;
  padding: 4px;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.mp-mobile-tabs button {
  flex: 1;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.mp-mobile-tabs button.active {
  background-color: var(--color-surface);
  color: var(--color-primary);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

/* scoped 样式与 Tailwind 的 lg:hidden 同优先级，且注入更晚，
   必须在媒体查询里显式隐藏，否则桌面端会多出一条移动端页签。 */
@media (min-width: 1024px) {
  .mp-mobile-tabs {
    display: none;
  }
}

/* ── 页面骨架 ─────────────────────────────────────────────── */
.mp-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  max-width: 1180px;
  height: 100%;
  margin: 0 auto;
  padding: 24px 24px 56px;
  overflow-y: auto;
}

.mp-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.mp-title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  color: var(--color-text-primary);
}

.mp-sub {
  margin-top: 6px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.mp-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  flex-wrap: wrap;
}

.mp-crumb {
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
}

.mp-crumb:not(.current):hover {
  color: var(--color-primary);
  text-decoration: underline;
}

.mp-crumb.current {
  color: var(--color-text-secondary);
}

.mp-crumb-sep {
  color: var(--color-text-tertiary);
}

.mp-back-btn {
  flex-shrink: 0;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.mp-back-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.mp-notice {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background-color: var(--state-warning-bg);
  color: var(--state-warning);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.mp-error {
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
  color: var(--state-error);
  font-size: var(--text-sm);
}

.mp-footnote {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.7;
}

/* ── 厂商网格 ─────────────────────────────────────────────── */
.vendor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(232px, 1fr));
  gap: 14px;
}

.vendor-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--color-border-light);
  border-radius: 14px;
  background-color: var(--color-surface);
  text-align: left;
  transition: border-color 0.16s ease, transform 0.16s ease, box-shadow 0.16s ease;
}

.vendor-card:hover {
  border-color: var(--color-primary);
  transform: translateY(-2px);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
}

.vendor-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--color-surface-secondary);
  border: 1px solid var(--color-border-light);
  overflow: hidden;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.vendor-logo {
  display: block;
  width: 28px;
  height: 28px;
  object-fit: contain;
  border-radius: 6px;
}

.vendor-icon.lg .vendor-logo {
  width: 36px;
  height: 36px;
  border-radius: 8px;
}

.vendor-icon.sm .vendor-logo {
  width: 22px;
  height: 22px;
  border-radius: 5px;
}

.vendor-logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: #fff;
}

.vendor-icon.lg {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  font-size: 20px;
}

.vendor-icon.sm {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  font-size: 14px;
}

.vendor-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.vendor-name-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.vendor-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.vendor-en {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.vendor-desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.vendor-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.vendor-connected {
  color: var(--state-success);
}

.vendor-arrow {
  flex-shrink: 0;
  align-self: center;
  color: var(--color-text-tertiary);
  font-size: 14px;
}

/* ── 厂商详情 ─────────────────────────────────────────────── */
.vendor-hero {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px;
  border: 1px solid var(--color-border-light);
  border-radius: 16px;
  background-color: var(--color-surface);
}

.vendor-hero-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.vendor-hero-desc {
  margin-top: 4px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.7;
}

.vendor-base {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  word-break: break-all;
}

.vendor-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--state-warning);
  line-height: 1.6;
}

.model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
}

.model-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 15px;
  border: 1px solid var(--color-border-light);
  border-radius: 14px;
  background-color: var(--color-surface);
}

.model-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.model-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.model-id {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-tertiary);
  word-break: break-all;
}

.model-connected {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background-color: var(--state-success-bg);
  color: var(--state-success);
  font-size: 10px;
  line-height: 18px;
  white-space: nowrap;
}

.type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.type-chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--chip) 14%, transparent);
  color: var(--chip);
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
}

.type-chat { --chip: #1971c2; }
.type-reasoning { --chip: #6741d9; }
.type-multimodal { --chip: #c2255c; }
.type-text { --chip: #2b8a3e; }
.type-embedding { --chip: #d9480f; }
.type-code { --chip: #0c8599; }
.type-image { --chip: #9c36b5; }
.type-video { --chip: #c92a2a; }
.type-speech { --chip: #495057; }

.model-note {
  font-size: 11px;
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

.model-action {
  margin-top: auto;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  font-size: var(--text-sm);
  transition: border-color 0.15s ease, color 0.15s ease, background-color 0.15s ease;
}

.model-action:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background-color: var(--color-primary-lighter);
}

/* ── 接入配置 ─────────────────────────────────────────────── */
.config-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(250px, 0.8fr);
  gap: 16px;
  align-items: start;
}

.config-card {
  padding: 20px;
  border: 1px solid var(--color-border-light);
  border-radius: 16px;
  background-color: var(--color-surface);
}

.config-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.config-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text-primary);
}

.config-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  word-break: break-all;
}

.config-types {
  margin-top: 12px;
}

.config-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 16px;
}

.mp-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.mp-label {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.mp-input {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  font-size: var(--text-sm);
}

.mp-input:focus {
  outline: none;
  border-color: var(--color-primary);
}

.mp-hint {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.6;
}

.mp-test {
  padding: 8px 10px;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.mp-test.ok {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.mp-test.bad {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.mp-btn {
  padding: 8px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-primary);
  font-size: var(--text-sm);
  background-color: var(--color-surface);
  white-space: nowrap;
}

.mp-btn:hover:not(:disabled) {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.mp-btn.primary {
  border-color: var(--color-primary);
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
}

.mp-btn.primary:hover:not(:disabled) {
  color: var(--color-primary-contrast);
  opacity: 0.92;
}

.mp-btn.ghost {
  background-color: transparent;
}

.mp-btn.sm {
  padding: 5px 10px;
  font-size: var(--text-xs);
}

.mp-btn.danger {
  color: var(--state-error);
  border-color: transparent;
}

.mp-btn.danger:hover:not(:disabled) {
  border-color: var(--state-error);
  color: var(--state-error);
}

.mp-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.config-aside {
  padding: 18px;
  border: 1px solid var(--color-border-light);
  border-radius: 16px;
  background-color: var(--color-surface-secondary);
}

.config-aside h4 {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.config-aside ul {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.8;
}

/* ── 我的接入 ─────────────────────────────────────────────── */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-hint {
  margin-top: 10px;
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
  line-height: 1.7;
}

.mp-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid var(--color-border-light);
  flex-wrap: wrap;
}

.mp-row:first-of-type {
  margin-top: 8px;
}

.mp-row-main {
  flex: 1;
  min-width: 220px;
}

.mp-row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mp-name {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.mp-chip {
  padding: 1px 7px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  font-size: 11px;
  line-height: 18px;
}

.mp-chip.on,
.mp-chip.ok {
  background-color: var(--state-success-bg);
  color: var(--state-success);
}

.mp-chip.off,
.mp-chip.bad {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.mp-row-meta {
  margin-top: 5px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  line-height: 1.7;
  word-break: break-all;
}

.mp-row-err {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--state-error);
}

.mp-row-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.mp-notes {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  line-height: 1.9;
}

/* ── Toast ────────────────────────────────────────────────── */
.app-toast {
  position: fixed;
  left: 50%;
  bottom: 36px;
  z-index: 1300;
  transform: translateX(-50%);
  padding: 9px 16px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.88);
  color: #fff;
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.25);
}

.toast-fade-enter-active,
.toast-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.toast-fade-enter-from,
.toast-fade-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(6px);
}

/* ── 响应式 ───────────────────────────────────────────────── */
@media (max-width: 900px) {
  .mp-page {
    max-width: 100%;
  }

  .config-layout {
    grid-template-columns: 1fr;
  }

  .vendor-hero {
    flex-direction: column;
  }
}

@media (max-width: 640px) {
  .mp-page {
    padding: 16px 14px 40px;
  }

  .mp-head {
    flex-direction: column;
    align-items: stretch;
  }

  .vendor-grid,
  .model-grid {
    grid-template-columns: 1fr;
  }

  .config-actions {
    flex-wrap: wrap;
  }

  .config-actions .mp-btn {
    flex: 1;
  }
}
</style>
