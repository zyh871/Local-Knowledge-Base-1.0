<template>
  <AppLayout>
    <!-- 侧栏：与对话页同一套导航（对话/工作 + 新对话 + 智鲸创作） -->
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <SidebarNav mode="create" @new="startNew" />
        <div class="flex-1"></div>
        <UserMenu
          :name="userName"
          :theme="theme"
          :items="['models', 'theme']"
          @models="router.push('/models')"
          @theme="toggleTheme"
          @logout="logout"
        />
      </aside>
    </template>
    <!-- 主区 -->
    <div class="create-body flex-1 min-h-0 overflow-y-auto">
      <div :class="['create-inner', { 'has-results': results.length > 0 }]">
        <!-- 标题 -->
        <header class="create-hero">
          <h2 class="hero-title">AI 创作</h2>
          <p class="hero-sub">让创作随灵感而生</p>
        </header>

        <!-- 输入区 -->
        <section :class="['composer', { 'is-busy': pending.length > 0 }]">
          <textarea
            ref="textareaRef"
            v-model="draft"
            class="composer-textarea"
            rows="2"
            maxlength="1000"
            :placeholder="kind === 'video' ? '描述你想要的视频画面' : '描述你想要的图片'"
            @input="autogrow"
            @keydown.enter.exact.prevent="generate"
          ></textarea>

          <!-- 参考图描述：它是「被读成了什么」，所以必须显示出来 -->
          <div v-if="referenceDesc" class="ref-chip">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <path d="M21 15l-5-5L5 21" />
            </svg>
            <span class="ref-text" :title="referenceDesc">参考画面：{{ referenceDesc }}</span>
            <button class="ref-remove" title="移除参考图" @click="clearReference">×</button>
          </div>
          <p v-if="referenceBusy" class="ref-busy">正在读取参考图…</p>

          <!-- 源图：图生图 / 图生视频是「直接改这张图」，
               与上面「参考画面描述」不是一回事 —— 后者只是一段文字修饰。 -->
          <div v-if="needsSource" class="ref-chip source-chip">
            <label class="source-pick">
              <input type="file" accept="image/*" class="hidden-input" @change="onSourcePicked" />
              <img v-if="source" :src="source.url" alt="" class="source-thumb" />
              <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 5v14M5 12h14" />
              </svg>
              <span class="ref-text">
                {{ source ? `源图：${source.name}` : `上传源图（${kind === 'video' ? '图生视频' : '图生图'}必需）` }}
              </span>
            </label>
            <button v-if="source" class="ref-remove" title="移除源图" @click="clearSource">×</button>
          </div>

          <div class="composer-toolbar">
            <div class="toolbar-group">
              <!-- 添加 -->
              <div class="menu-anchor">
                <button
                  class="icon-btn"
                  data-role="add-chip"
                  title="添加参考图 / 灵感"
                  @click.stop="toggleMenu('add')"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                </button>
                <div v-if="openMenu === 'add'" class="menu" @click.stop>
                  <button class="menu-item" @click="pickReference">
                    <span class="menu-item-label">上传参考图</span>
                    <span class="menu-item-tag">读成描述</span>
                  </button>
                  <button class="menu-item" @click="useIdea">
                    <span class="menu-item-label">换个灵感</span>
                  </button>
                  <button v-if="referenceDesc" class="menu-item" @click="clearReference">
                    <span class="menu-item-label">清除参考画面</span>
                  </button>
                  <p class="menu-note">
                    参考图不会原样喂给模型：视觉模型会先把它读成一段画面描述，再拼进提示词。
                  </p>
                </div>
              </div>
              <input ref="fileRef" type="file" accept="image/*" class="hidden-input" @change="onReferencePicked" />

              <!-- 图像 / 视频 -->
              <div class="mode-seg" role="tablist" aria-label="创作类型">
                <button
                  class="seg-btn"
                  :class="{ on: kind === 'image' }"
                  role="tab"
                  :aria-selected="kind === 'image'"
                  @click="setKind('image')"
                >
                  图像
                </button>
                <button
                  class="seg-btn"
                  :class="{ on: kind === 'video' }"
                  role="tab"
                  :aria-selected="kind === 'video'"
                  :disabled="!options.video_enabled"
                  :title="options.video_enabled ? '文生视频 / 图生视频' : options.video_message || '视频生成不可用'"
                  @click="setKind('video')"
                >
                  视频
                </button>
              </div>

              <!-- 模型 -->
              <div class="menu-anchor">
                <button class="chip" :class="{ on: openMenu === 'model' }" @click.stop="toggleMenu('model')">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M12 3l2.2 5.4L20 10l-4.4 3.8L16.8 20 12 16.8 7.2 20l1.2-6.2L4 10l5.8-1.6z" />
                  </svg>
                  {{ modelLabel }}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <div v-if="openMenu === 'model'" class="menu" @click.stop>
                  <p v-if="!activeModels.length" class="menu-empty">暂无可用{{ kindLabel }}模型</p>
                  <button
                    v-for="m in activeModels"
                    :key="m.id"
                    :class="['menu-item', { active: m.id === activeModel }]"
                    @click="selectModel(m.id)"
                  >
                    <span class="menu-item-label">{{ m.label || m.id }}</span>
                    <span v-if="m.needs_image" class="menu-item-tag">需源图</span>
                    <span v-if="m.id === defaultModel" class="menu-item-tag">默认</span>
                  </button>
                </div>
              </div>

              <!-- 比例 -->
              <div class="menu-anchor">
                <button class="chip" :class="{ on: openMenu === 'ratio' }" @click.stop="toggleMenu('ratio')">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M4 8h16M4 16h16M8 4v16M16 4v16" />
                  </svg>
                  {{ ratioLabel }}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <div v-if="openMenu === 'ratio'" class="menu" @click.stop>
                  <button
                    v-for="r in activeRatios"
                    :key="r.id"
                    :class="['menu-item', { active: r.id === activeRatio }]"
                    @click="selectRatio(r.id)"
                  >
                    <span class="menu-item-label">{{ r.label }}</span>
                    <span class="menu-item-tag">{{ r.id }}</span>
                  </button>
                </div>
              </div>

              <!-- 风格 -->
              <div class="menu-anchor">
                <button class="chip" :class="{ on: openMenu === 'style' }" @click.stop="toggleMenu('style')">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M12 3a9 9 0 100 18c1.2 0 1.8-.9 1.8-2 0-1.6 1.1-2.4 2.6-2.4H18a3 3 0 003-3c0-5.5-4-10.6-9-10.6z" />
                    <circle cx="8" cy="10" r="1" />
                    <circle cx="12" cy="7.5" r="1" />
                  </svg>
                  {{ styleLabel }}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <div v-if="openMenu === 'style'" class="menu" @click.stop>
                  <button
                    v-for="s in options.styles"
                    :key="s.id || 'none'"
                    :class="['menu-item', { active: s.id === style }]"
                    @click="selectStyle(s.id)"
                  >
                    <span class="menu-item-label">{{ s.label }}</span>
                  </button>
                  <p class="menu-note">风格会作为提示词修饰追加在原描述之后，不会覆盖你写的细节。</p>
                </div>
              </div>
            </div>

            <div class="toolbar-group">
              <!-- 语音输入：浏览器不支持时整颗按钮不出现，而不是点了没反应 -->
              <button
                v-if="speechSupported"
                class="icon-btn"
                :class="{ listening }"
                :title="listening ? '正在听…点击停止' : '用语音描述画面'"
                @click="toggleSpeech"
              >
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                  <rect x="9" y="2" width="6" height="11" rx="3" />
                  <path d="M5 10a7 7 0 0014 0M12 19v3" />
                </svg>
              </button>
              <button class="send-btn" :disabled="!canGenerate" aria-label="生成" @click="generate">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 19V5" />
                  <path d="M5 12l7-7 7 7" />
                </svg>
              </button>
            </div>
          </div>
        </section>

        <!-- 提示 / 错误 -->
        <p v-if="error" class="notice is-error">
          {{ error }}
          <button class="notice-close" title="关闭" @click="error = ''">✕</button>
        </p>
        <p v-else-if="!options.enabled && options.message" class="notice">
          {{ options.message }}
        </p>
        <p v-else-if="pending.length" class="notice is-info">
          {{ kind === 'video' ? '正在提交视频任务…随后进入生成队列，约 1-3 分钟。' : '正在绘制…通常 10-40 秒，完成后会出现在下面。' }}
        </p>

        <!-- 空态：灵感（生成中也要藏起来，否则会和新出的占位块抢注意力） -->
        <section v-if="!results.length && !pending.length" class="inspirations">
          <p class="inspiration-caption">没有头绪？试试这些</p>
          <div class="inspiration-list">
            <button v-for="idea in IDEAS" :key="idea" class="inspiration-chip" @click="usePrompt(idea)">
              {{ idea }}
            </button>
          </div>
        </section>

        <!-- 结果 -->
        <section v-if="results.length || pending.length" ref="resultsRef" class="results">
          <div class="results-head">
            <span>{{ results.length ? `本次创作 · ${results.length} 个作品` : '生成中…' }}</span>
            <button v-if="results.length" class="text-btn" @click="clearResults">清空本次</button>
          </div>
          <div class="result-grid">
            <figure
              v-for="item in results"
              :key="item.id"
              class="result-card"
              :style="{ aspectRatio: aspectOf(item.size) }"
            >
              <video
                v-if="item.kind === 'video' && mediaUrls[item.id]"
                :src="mediaUrls[item.id]"
                class="result-video"
                controls
                muted
                loop
                playsinline
                preload="metadata"
              ></video>
              <img
                v-else-if="mediaUrls[item.id]"
                :src="mediaUrls[item.id]"
                :alt="item.prompt"
                @click="preview = item"
              />
              <div v-else-if="item.status === 'running'" class="result-placeholder">
                <span class="spinner"></span>
                <span>
                  {{ item.kind === 'video' ? '视频生成中' : '排队生成中' }}
                  {{ elapsedSeconds(item) }}s
                  {{ item.kind === 'video' ? '· 约 1-3 分钟' : '· ModelScope 排队，通常几十秒到几分钟' }}
                </span>
              </div>
              <div v-else class="result-placeholder">
                <span>{{ item.status === 'failed' ? item.error || '生成失败' : mediaErrors[item.id] || '正在取回文件…' }}</span>
                <button v-if="item.status === 'failed'" class="mini-btn" @click.stop="remove(item)">删除</button>
              </div>

              <div class="result-overlay">
                <button class="ov-btn" title="查看大图" @click="preview = item">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="11" cy="11" r="7" />
                    <path d="M21 21l-4.3-4.3" />
                  </svg>
                </button>
                <button class="ov-btn" title="下载" @click="download(item)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                </button>
                <button class="ov-btn" title="复制提示词" @click="copyPrompt(item)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="12" height="12" rx="2" />
                    <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                  </svg>
                </button>
                <button class="ov-btn danger" title="删除" @click="remove(item)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M3 6h18M8 6V4a1 1 0 011-1h6a1 1 0 011 1v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6" />
                  </svg>
                </button>
              </div>

              <figcaption class="result-caption">
                <span class="result-prompt" :title="item.prompt">{{ item.prompt }}</span>
                <span class="result-meta">
                  {{ item.kind === 'video' ? '视频' : '图片' }} · {{ item.size }}
                  <template v-if="item.kind !== 'video'"> · {{ (item.elapsed_ms / 1000).toFixed(1) }}s</template>
                </span>
              </figcaption>
            </figure>

            <div
              v-for="p in pending"
              :key="p.tempId"
              class="result-card is-pending"
              :style="{ aspectRatio: aspectOf(p.size) }"
            >
              <div class="result-placeholder">
                <span class="spinner"></span>
                <span>{{ p.kind === 'video' ? '正在提交任务' : '正在绘制' }} {{ elapsedSeconds(p) }}s</span>
              </div>
            </div>
          </div>
        </section>

        <!-- 资产库：所有已完成的图片与视频都在这里，不再挤在侧栏 -->
        <section class="asset-library">
          <header class="asset-head">
            <h3 class="asset-title">
              资产库
              <span class="asset-total">{{ assetCounts.image + assetCounts.video }}</span>
            </h3>
            <div class="asset-tabs" role="tablist" aria-label="资产类型">
              <button
                v-for="tab in ASSET_TABS"
                :key="tab.key"
                type="button"
                role="tab"
                :aria-selected="assetTab === tab.key"
                :class="['asset-tab', { on: assetTab === tab.key }]"
                @click="assetTab = tab.key"
              >
                {{ tab.label }}
                <span class="asset-tab-count">{{ assetCounts[tab.key] }}</span>
              </button>
            </div>
          </header>

          <div v-if="assetItems.length" class="asset-grid">
            <button
              v-for="item in assetItems"
              :key="item.id"
              type="button"
              class="asset-card"
              :title="item.prompt"
              @click="openAsset(item)"
            >
              <img
                v-if="item.kind !== 'video' && mediaUrls[item.id]"
                :src="mediaUrls[item.id]"
                alt=""
                loading="lazy"
              />
              <video
                v-else-if="item.kind === 'video' && mediaUrls[item.id]"
                :src="mediaUrls[item.id]"
                muted
                loop
                playsinline
                preload="metadata"
                class="asset-video"
              ></video>
              <!-- 没取到文件时给一个明确的占位，而不是空白格子 -->
              <span v-else class="asset-ph">
                <svg v-if="item.kind === 'video'" width="20" height="20" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" stroke-width="1.6">
                  <rect x="2" y="5" width="14" height="14" rx="2" />
                  <path d="M16 10l6-3v10l-6-3z" />
                </svg>
                <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="1.6">
                  <rect x="3" y="3" width="18" height="18" rx="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <path d="M21 15l-5-5L5 21" />
                </svg>
              </span>
              <span class="asset-badge">{{ item.kind === 'video' ? '视频' : '图片' }}</span>
            </button>
          </div>
          <p v-else class="asset-empty">
            还没有{{ assetTab === 'video' ? '视频' : '图片' }}作品。在上方描述画面，生成后会自动收进这里。
          </p>
        </section>
      </div>
    </div>

    <!-- 大图预览 -->
    <div v-if="preview" class="lightbox" @click.self="preview = null">
      <div class="lightbox-panel">
        <video
          v-if="preview.kind === 'video' && mediaUrls[preview.id]"
          :src="mediaUrls[preview.id]"
          class="lightbox-media"
          controls
          autoplay
          loop
          playsinline
        ></video>
        <img
          v-else-if="mediaUrls[preview.id]"
          class="lightbox-media"
          :src="mediaUrls[preview.id]"
          :alt="preview.prompt"
        />
        <div class="lightbox-side">
          <p class="lightbox-title">{{ preview.prompt }}</p>
          <p class="lightbox-meta">{{ preview.size }} · {{ preview.model }}</p>
          <p v-if="preview.style" class="lightbox-meta">风格：{{ styleName(preview.style) }}</p>
          <p v-if="preview.reference_desc" class="lightbox-meta">参考画面：{{ preview.reference_desc }}</p>
          <div class="lightbox-actions">
            <button class="mini-btn" @click="download(preview)">下载</button>
            <button class="mini-btn" @click="copyPrompt(preview)">复制提示词</button>
            <button class="mini-btn" @click="reusePrompt(preview)">再画一次</button>
            <button class="mini-btn" @click="preview = null">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </AppLayout>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import SidebarNav from '../components/sidebar/SidebarNav.vue'
import UserMenu from '../components/sidebar/UserMenu.vue'
import { useTheme } from '../composables/useTheme'
import { creationApi } from '../api/creations'

const router = useRouter()
const { theme, toggleTheme } = useTheme()
const userName = ref(localStorage.getItem('username') || '')

/** 空态灵感：具体、可直接出图，不含「帮我画」这类无效前缀。 */
const IDEAS = [
  '一只橘猫趴在午后窗台上，暖光斜照，水彩质感',
  '赛博朋克城市雨夜，霓虹灯倒映在湿漉漉的街道上',
  '青绿山水，云雾中的一叶孤舟，大量留白',
  '极简线稿：一只手托起一颗星球，纯白背景',
]

const MAX_HISTORY = 200
/** 资产库的筛选页签：image / video */
const assetTab = ref('image')

// ── 状态 ───────────────────────────────────────────────────────────
const options = ref({
  enabled: false,
  message: '',
  models: [],
  default_model: '',
  ratios: [{ id: '1:1', label: '方形', size: '1024x1024' }],
  styles: [{ id: '', label: '无风格' }],
  // 视频能力来自百炼；未配置密钥时 video_enabled=false，整组参数置为不可用
  video_enabled: false,
  video_models: [],
  default_video_model: '',
  video_message: '',
  i2i_enabled: false,
  i2i_models: [],
  i2v_enabled: false,
  i2v_models: [],
  video_ratios: [{ id: '16:9', label: '横屏', size: '1920x1080' }],
  video_duration_seconds: 5,
  max_prompt_chars: 1000,
})

const draft = ref('')
/** 创作类型：image / video。两套模型与比例互不共用 */
const kind = ref('image')
const model = ref(localStorage.getItem('create:model') || '')
const ratio = ref(localStorage.getItem('create:ratio') || '1:1')
const videoModel = ref(localStorage.getItem('create:videoModel') || '')
const videoRatio = ref(localStorage.getItem('create:videoRatio') || '16:9')
const style = ref(localStorage.getItem('create:style') || '')

const history = ref([])
const results = ref([])
const pending = ref([])
const activeId = ref('')
const loadingHistory = ref(false)
const error = ref('')

const referenceDesc = ref('')
const referenceBusy = ref(false)

/** 图生图 / 图生视频的源图：{ file, url, name, size } */
const source = ref(null)

const openMenu = ref(null)
const preview = ref(null)

const mediaUrls = reactive({})
const mediaErrors = reactive({})

const textareaRef = ref(null)
const fileRef = ref(null)
const resultsRef = ref(null)

/** 秒表：只影响「正在绘制 x 秒」这行字，所以用一个每秒跳动的 now。 */
const now = ref(Date.now())
let ticker = null

// ── 计算属性 ───────────────────────────────────────────────────────
const kindLabel = computed(() => (kind.value === 'video' ? '视频' : '图像'))

/** 当前类型可用的模型 / 比例。图像与视频的模型池完全不同，不能混用 */
const activeModels = computed(() =>
  kind.value === 'video' ? options.value.video_models : options.value.models
)
const activeRatios = computed(() =>
  kind.value === 'video' ? options.value.video_ratios : options.value.ratios
)
const defaultModel = computed(() =>
  kind.value === 'video' ? options.value.default_video_model : options.value.default_model
)

const activeModel = computed({
  get: () => (kind.value === 'video' ? videoModel.value : model.value),
  set: (value) => {
    if (kind.value === 'video') {
      videoModel.value = value
      persist('create:videoModel', value)
    } else {
      model.value = value
      persist('create:model', value)
    }
  },
})

const activeRatio = computed({
  get: () => (kind.value === 'video' ? videoRatio.value : ratio.value),
  set: (value) => {
    if (kind.value === 'video') {
      videoRatio.value = value
      persist('create:videoRatio', value)
    } else {
      ratio.value = value
      persist('create:ratio', value)
    }
  },
})

const modelLabel = computed(() => {
  const hit = activeModels.value.find((m) => m.id === activeModel.value)
  return hit ? hit.label || hit.id : '选择模型'
})

const ratioOption = computed(
  () => activeRatios.value.find((r) => r.id === activeRatio.value) || activeRatios.value[0]
)
const ratioLabel = computed(() => ratioOption.value?.label || activeRatio.value)

const styleLabel = computed(() => {
  const hit = options.value.styles.find((s) => s.id === style.value)
  return hit ? hit.label : '风格'
})

/** 当前选中的模型定义（含 needs_image / category），用于决定要不要源图 */
const activeMeta = computed(
  () => activeModels.value.find((m) => m.id === activeModel.value) || null
)
/** 图生图 / 图生视频：模型会直接改这张图，与「参考画面描述」不是一回事 */
const needsSource = computed(() => !!activeMeta.value?.needs_image)

/** 资产库页签 */
const ASSET_TABS = [
  { key: 'image', label: '图片' },
  { key: 'video', label: '视频' },
]

/** 已完成的资产（跑着的任务还在上面的结果区，不该重复出现两次） */
const assetItems = computed(() =>
  history.value.filter(
    (a) =>
      a.status === 'done' &&
      (assetTab.value === 'video' ? a.kind === 'video' : a.kind !== 'video')
  )
)

const assetCounts = computed(() => ({
  image: history.value.filter((a) => a.status === 'done' && a.kind !== 'video').length,
  video: history.value.filter((a) => a.status === 'done' && a.kind === 'video').length,
}))

/**
 * 视频缩略图：只给当前页签可见的前几条取 blob。
 * 视频动辄几兆到几十兆，整库预取会把内存和带宽一起吃光 ——
 * 但没有缩略图时卡片只是一块空白，用户分不清哪条是哪条。
 */
const VIDEO_THUMB_LIMIT = 6

watch(
  assetTab,
  (tab) => {
    if (tab !== 'video') return
    assetItems.value
      .slice(0, VIDEO_THUMB_LIMIT)
      .forEach((item) => {
        if (item.media_url) ensureMedia(item.id)
      })
  },
  { immediate: true }
)

/** 点资产卡：先给灯箱，再按需取文件（视频几十兆，不能整库预取） */
function openAsset(item) {
  preview.value = item
  if (item.media_url) ensureMedia(item.id)
}

const canGenerate = computed(
  () =>
    (kind.value === 'video' ? options.value.video_enabled : options.value.enabled) &&
    draft.value.trim().length > 0 &&
    pending.value.length === 0 &&
    (!needsSource.value || !!source.value)
)

// ── 初始化 ─────────────────────────────────────────────────────────
async function bootstrap() {
  try {
    const data = await creationApi.options()
    options.value = { ...options.value, ...data }
    // 白名单变了（换了后端/模型下架）→ 回落到服务端默认，不静默保留非法值
    const ids = options.value.models.map((m) => m.id)
    if (!model.value || !ids.includes(model.value)) {
      model.value = data.default_model || ids[0] || ''
      persist('create:model', model.value)
    }
    if (!options.value.ratios.some((r) => r.id === ratio.value)) {
      ratio.value = options.value.ratios[0]?.id || '1:1'
    }
    if (!options.value.styles.some((s) => s.id === style.value)) {
      style.value = ''
    }

    // 视频参数同理：模型下架 / 换了供应商时回落到服务端默认
    const videoIds = options.value.video_models.map((m) => m.id)
    if (!videoModel.value || !videoIds.includes(videoModel.value)) {
      videoModel.value = data.default_video_model || videoIds[0] || ''
      persist('create:videoModel', videoModel.value)
    }
    if (!options.value.video_ratios.some((r) => r.id === videoRatio.value)) {
      videoRatio.value = options.value.video_ratios[0]?.id || '16:9'
    }
    if (!options.value.video_enabled) kind.value = 'image'
  } catch (e) {
    error.value = e.message || '读取参数选项失败'
  }
  await loadHistory()
}

async function loadHistory() {
  loadingHistory.value = true
  try {
    const data = await creationApi.list(MAX_HISTORY)
    history.value = data.creations || []
    // 只为最近 8 张**图片**取缩略图：视频动辄十几兆，塞进侧栏纯粹浪费带宽，
    // 而侧栏里根本看不清第 9 张之后的区别
    await Promise.allSettled(
      history.value
        .filter((item) => item.kind !== 'video' && item.media_url)
        .slice(0, 18)
        .map((item) => ensureMedia(item.id))
    )
  } catch (e) {
    // 历史拿不到不应阻塞创作
    console.warn('读取创作历史失败:', e)
  } finally {
    loadingHistory.value = false
  }
}

// ── 图片 ───────────────────────────────────────────────────────────
async function ensureMedia(id) {
  if (!id || mediaUrls[id]) return
  try {
    mediaUrls[id] = await creationApi.imageBlobUrl(id)
    delete mediaErrors[id]
  } catch (e) {
    mediaErrors[id] = e.message || '图片加载失败'
  }
}

function releaseMedia(id) {
  if (mediaUrls[id]) {
    URL.revokeObjectURL(mediaUrls[id])
    delete mediaUrls[id]
  }
}

// ── 生成 ───────────────────────────────────────────────────────────
async function generate() {
  if (!canGenerate.value) return
  error.value = ''
  const text = draft.value.trim()
  const size = ratioOption.value?.size || ''
  const tempId = `pending-${Date.now()}`
  const isVideo = kind.value === 'video'
  pending.value.push({ tempId, size, startedAt: Date.now(), kind: kind.value })
  startTicker()

  const payload = {
    prompt: text,
    model: activeModel.value,
    ratio: activeRatio.value,
    style: style.value,
    reference_desc: referenceDesc.value,
  }

  /** 图生图 / 图生视频走 multipart：源图必须以文件形式上传，
      塞进 JSON 的 base64 会让请求体膨胀 33%，且后端还要再解一次码 */
  const form = () => {
    const data = new FormData()
    data.append('file', source.value.file)
    data.append('prompt', text)
    data.append('model', activeModel.value)
    data.append('ratio', activeRatio.value)
    data.append('style', style.value)
    data.append('reference_desc', referenceDesc.value)
    return data
  }
  const withSource = needsSource.value && !!source.value

  try {
    if (isVideo) {
      // 提交成功就立刻把占位换成「生成中」卡片：视频要 1-3 分钟，
      // 停在转圈而没有任何进度，用户会以为卡死
      const item = withSource
        ? await creationApi.videoFromImage(form())
        : await creationApi.generateVideo(payload)
      pending.value = pending.value.filter((p) => p.tempId !== tempId)
      adopt(item)
      pollCreation(item.id)
    } else {
      const item = withSource
        ? await creationApi.editImage(form())
        : await creationApi.generate(payload)
      adopt(item)
      if (item.status === 'running') {
        // ModelScope 是排队制，提交后要等上游轮到 —— 与视频同一套轮询
        pollCreation(item.id)
      } else {
        await ensureMedia(item.id)
      }
      // 出图后把结果带进视野：页面较长时，不给这一下用户会以为「点了没反应」
      await nextTick()
      resultsRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  } catch (e) {
    // 失败原因（额度 / 内容审核 / 尺寸 / 超时）必须原样给出，
    // 统一的「生成失败」会让用户完全无从下手
    error.value = e.message || '生成失败，请重试'
  } finally {
    pending.value = pending.value.filter((p) => p.tempId !== tempId)
    stopTickerIfIdle()
  }
}

/** 把一条记录并入结果区与历史（同 id 覆盖，不产生重复卡片） */
function adopt(item) {
  const normalized =
    item.status === 'running' && !item.startedAt
      ? { ...item, startedAt: Date.now() }
      : item
  results.value = [normalized, ...results.value.filter((r) => r.id !== item.id)]
  history.value = [normalized, ...history.value.filter((h) => h.id !== item.id)].slice(
    0,
    MAX_HISTORY
  )
  activeId.value = item.id
}

/**
 * 视频进度轮询。
 *
 * 每 4 秒问一次后端（后端顺带问一次百炼），上限 90 次 ≈ 6 分钟 ——
 * 超过这个时间一定是上游出问题了，继续轮询只是让页面永远停在「生成中」。
 */
const pollTimers = new Map()
const POLL_INTERVAL_MS = 4000
const POLL_MAX_ATTEMPTS = 200

function pollCreation(id, attempt = 0) {
  if (attempt >= POLL_MAX_ATTEMPTS) return
  const timer = setTimeout(async () => {
    pollTimers.delete(id)
    try {
      const item = await creationApi.get(id)
      adopt(item)
      if (item.status === 'done') {
        await ensureMedia(id)
        await nextTick()
        resultsRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        return
      }
      if (item.status === 'failed') {
        error.value = item.error || '视频生成失败'
        return
      }
      pollCreation(id, attempt + 1)
    } catch (e) {
      // 单次查询失败（网络抖动）继续重试，由次数上限兜住
      pollCreation(id, attempt + 1)
    }
  }, POLL_INTERVAL_MS)
  pollTimers.set(id, timer)
}

function elapsedSeconds(item) {
  return Math.max(0, Math.floor((now.value - item.startedAt) / 1000))
}

function startTicker() {
  if (ticker) return
  now.value = Date.now()
  ticker = setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

function stopTickerIfIdle() {
  if (pending.value.length) return
  if (ticker) {
    clearInterval(ticker)
    ticker = null
  }
}

// ── 结果操作 ───────────────────────────────────────────────────────
async function download(item) {
  const ext =
    item.mime === 'video/mp4'
      ? 'mp4'
      : item.mime === 'image/jpeg'
        ? 'jpg'
        : (item.mime || '').split('/')[1] || 'png'
  try {
    await creationApi.download(item.id, `${item.id}.${ext}`)
  } catch (e) {
    error.value = e.message || '下载失败'
  }
}

async function copyPrompt(item) {
  try {
    await navigator.clipboard.writeText(item.prompt)
  } catch (e) {
    error.value = '复制失败，请手动选中文案复制'
  }
}

async function remove(item) {
  if (!window.confirm('删除这个作品？对应文件会一并从磁盘移除。')) return
  try {
    await creationApi.remove(item.id)
    results.value = results.value.filter((r) => r.id !== item.id)
    history.value = history.value.filter((h) => h.id !== item.id)
    if (preview.value?.id === item.id) preview.value = null
    releaseMedia(item.id)
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

function clearResults() {
  pollTimers.forEach((timer) => clearTimeout(timer))
  pollTimers.clear()
  results.value = []
  activeId.value = ''
}

function reusePrompt(item) {
  draft.value = item.prompt
  if (item.kind === 'video') {
    if (options.value.video_enabled) kind.value = 'video'
    if (item.ratio && options.value.video_ratios.some((r) => r.id === item.ratio)) {
      videoRatio.value = item.ratio
    }
    if (item.model && options.value.video_models.some((m) => m.id === item.model)) {
      videoModel.value = item.model
    }
  } else {
    kind.value = 'image'
    if (item.ratio && options.value.ratios.some((r) => r.id === item.ratio)) {
      ratio.value = item.ratio
    }
    if (item.model && options.value.models.some((m) => m.id === item.model)) {
      model.value = item.model
    }
  }
  if (item.style) style.value = item.style
  preview.value = null
  textareaRef.value?.focus()
}

function openHistory(item) {
  activeId.value = item.id
  reusePrompt(item)
  results.value = [item]
  if (item.status === 'running') pollCreation(item.id)
  if (item.media_url) ensureMedia(item.id)
}

function startNew() {
  draft.value = ''
  referenceDesc.value = ''
  results.value = []
  activeId.value = ''
  preview.value = null
  error.value = ''
  textareaRef.value?.focus()
}

// ── 参数 ───────────────────────────────────────────────────────────
/** 切换图像 / 视频。两套模型与比例互不共用，切换时不保留对方的取值 */
function setKind(next) {
  if (next === kind.value) return
  if (next === 'video' && !options.value.video_enabled) return
  kind.value = next
  closeMenus()
}

function selectModel(id) {
  activeModel.value = id
  closeMenus()
}

function selectRatio(id) {
  activeRatio.value = id
  closeMenus()
}

function selectStyle(id) {
  style.value = id
  persist('create:style', id)
  closeMenus()
}

function styleName(id) {
  return options.value.styles.find((s) => s.id === id)?.label || id
}

function aspectOf(size) {
  const [w, h] = String(size || '1024x1024').split('x').map(Number)
  return w > 0 && h > 0 ? `${w} / ${h}` : '1 / 1'
}

// ── 参考图 ─────────────────────────────────────────────────────────
function pickReference() {
  closeMenus()
  fileRef.value?.click()
}

async function onReferencePicked(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // 允许重复选同一张
  if (!file) return
  referenceBusy.value = true
  error.value = ''
  try {
    const data = await creationApi.describeReference(file)
    referenceDesc.value = data.description
  } catch (e) {
    error.value = e.message || '参考图读取失败'
  } finally {
    referenceBusy.value = false
  }
}

function clearReference() {
  referenceDesc.value = ''
  closeMenus()
}

// ── 源图（图生图 / 图生视频）────────────────────────────────────────
const MAX_SOURCE_MB = 10

function onSourcePicked(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!file.type.startsWith('image/')) {
    error.value = '请选择图片文件'
    return
  }
  if (file.size > MAX_SOURCE_MB * 1024 * 1024) {
    error.value = `源图不能超过 ${MAX_SOURCE_MB}MB`
    return
  }
  // 这张图会被 base64 编码后塞进上游请求体，几十兆的图会在半路被掐断，
  // 而报错发生在别人的服务器上 —— 所以体积必须在前端先卡住
  clearSource()
  source.value = {
    file,
    url: URL.createObjectURL(file),
    name: file.name,
    size: file.size,
  }
  error.value = ''
}

function clearSource() {
  if (source.value?.url) URL.revokeObjectURL(source.value.url)
  source.value = null
}

// ── 灵感 ───────────────────────────────────────────────────────────
function usePrompt(text) {
  draft.value = text
  textareaRef.value?.focus()
}

function useIdea() {
  const pool = IDEAS.filter((idea) => idea !== draft.value.trim())
  usePrompt(pool[Math.floor(Math.random() * pool.length)] || IDEAS[0])
  closeMenus()
}

// ── 语音输入 ───────────────────────────────────────────────────────
const speechSupported = ref(false)
const listening = ref(false)
let recognition = null

function setupSpeech() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SpeechRecognition) return
  speechSupported.value = true
  recognition = new SpeechRecognition()
  recognition.lang = 'zh-CN'
  recognition.interimResults = false
  recognition.continuous = false
  recognition.onresult = (event) => {
    const text = event.results?.[0]?.[0]?.transcript || ''
    if (!text) return
    draft.value = draft.value ? `${draft.value}${text}` : text
  }
  recognition.onerror = (event) => {
    listening.value = false
    // `not-allowed` 是权限被拒，说清楚比静默失败好
    if (event.error === 'not-allowed') error.value = '麦克风权限被拒绝，请在浏览器设置里允许'
  }
  recognition.onend = () => {
    listening.value = false
  }
}

function toggleSpeech() {
  if (!recognition) return
  if (listening.value) {
    recognition.stop()
    return
  }
  try {
    recognition.start()
    listening.value = true
  } catch (e) {
    listening.value = false
  }
}

// ── 通用 ───────────────────────────────────────────────────────────
function autogrow(event) {
  const el = event.target
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`
}

function toggleMenu(name) {
  openMenu.value = openMenu.value === name ? null : name
}

function closeMenus() {
  openMenu.value = null
}

function persist(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch (e) {
    // 隐私模式下 localStorage 可能不可用，失败不影响功能
  }
}

function formatTime(ts) {
  if (!ts) return ''
  const date = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function onKeydown(event) {
  if (event.key === 'Escape') {
    if (preview.value) preview.value = null
    else closeMenus()
  }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  localStorage.removeItem('chatData')
  router.push('/login')
}

onMounted(() => {
  document.addEventListener('click', closeMenus)
  document.addEventListener('keydown', onKeydown)
  setupSpeech()
  bootstrap()
})

onUnmounted(() => {
  document.removeEventListener('click', closeMenus)
  document.removeEventListener('keydown', onKeydown)
  if (ticker) clearInterval(ticker)
  pollTimers.forEach((timer) => clearTimeout(timer))
  pollTimers.clear()
  if (recognition && listening.value) recognition.stop()
  if (source.value?.url) URL.revokeObjectURL(source.value.url)
  Object.keys(mediaUrls).forEach((id) => releaseMedia(id))
})
</script>

<style scoped>
/* ===== 主区 ===== */
.create-body {
  background-color: var(--color-bg);
}

.create-inner {
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: 6px 20px 64px;
}

.create-hero {
  padding: 34px 0 26px;
  text-align: center;
}

.create-inner.has-results .create-hero {
  padding: 14px 0 16px;
}

.hero-title {
  font-family: var(--font-display);
  font-size: 30px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.create-inner.has-results .hero-title {
  font-size: 22px;
}

.hero-sub {
  margin-top: 8px;
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
}

/* ===== 输入区 ===== */
.composer {
  border: 1px solid var(--color-border);
  border-radius: 18px;
  background-color: var(--color-surface);
  padding: 16px 16px 10px;
  /* 参考豆包：一圈很淡的主色光晕，让输入框成为页面焦点 */
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--color-primary) 7%, transparent),
    0 6px 22px color-mix(in srgb, var(--color-primary) 8%, transparent);
  transition: box-shadow 0.2s, border-color 0.2s;
}

.composer:focus-within {
  border-color: color-mix(in srgb, var(--color-primary) 55%, var(--color-border));
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--color-primary) 14%, transparent),
    0 8px 26px color-mix(in srgb, var(--color-primary) 12%, transparent);
}

.composer.is-busy {
  border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border));
}

.composer-textarea {
  width: 100%;
  min-height: 54px;
  max-height: 180px;
  border: none;
  resize: none;
  background: transparent;
  color: var(--color-text-primary);
  font-size: var(--text-base);
  line-height: 1.7;
}

.composer-textarea::placeholder {
  color: var(--color-text-tertiary);
}

.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  margin-top: 2px;
  padding: 4px 8px;
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  background-color: var(--color-primary-lighter);
  color: var(--color-primary);
}

.ref-text {
  max-width: 520px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 源图：图生图 / 图生视频的输入，视觉上要比「参考画面」更实（有缩略图） */
.source-pick {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.source-thumb {
  width: 22px;
  height: 22px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid var(--color-border);
}

.ref-remove {
  font-size: 14px;
  line-height: 1;
  color: var(--color-text-tertiary);
}

.ref-busy {
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.toolbar-group {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  transition: background-color 0.15s, color 0.15s;
}

.icon-btn:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.icon-btn.listening {
  color: var(--state-error);
  background-color: var(--state-error-bg);
}

/* 图像 / 视频 分段控件 */
.mode-seg {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
}

.seg-btn {
  padding: 4px 12px;
  font-size: var(--text-xs);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  transition: background-color 0.15s, color 0.15s;
}

.seg-btn.on {
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-static);
}

.seg-btn[disabled] {
  color: var(--color-text-tertiary);
  opacity: 0.6;
  cursor: not-allowed;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 200px;
  padding: 5px 10px;
  font-size: var(--text-xs);
  border-radius: var(--radius-full);
  color: var(--color-text-secondary);
  white-space: nowrap;
  transition: background-color 0.15s, color 0.15s;
}

.chip:hover,
.chip.on {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.send-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background-color: var(--color-primary);
  color: var(--color-primary-contrast);
  transition: opacity 0.15s;
}

.send-btn[disabled] {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
  cursor: default;
}

/* ===== 下拉菜单 ===== */
.menu-anchor {
  position: relative;
}

.menu {
  position: absolute;
  /* 向下展开：向上会把正在编辑的提示词盖住，而下面是留白区 */
  top: calc(100% + 8px);
  left: 0;
  z-index: 30;
  min-width: 190px;
  /* 上限要留够「风格」这一项：9 个选项 + 底部说明刚好放得下，
     少了会把说明裁成半行，看起来像渲染坏了 */
  max-height: min(420px, 55vh);
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-elevated);
}

.menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 9px;
  font-size: var(--text-sm);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  text-align: left;
}

.menu-item:hover {
  background-color: var(--color-surface-secondary);
  color: var(--color-text-primary);
}

.menu-item.active {
  color: var(--color-primary);
  background-color: var(--color-primary-lighter);
}

.menu-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-item-tag {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.menu-note {
  padding: 6px 9px 2px;
  font-size: var(--text-xs);
  line-height: 1.6;
  color: var(--color-text-tertiary);
}

.menu-empty {
  padding: 8px 9px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

/* ===== 提示 ===== */
.notice {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding: 8px 11px;
  font-size: var(--text-xs);
  line-height: 1.7;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
}

.notice.is-error {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.notice.is-info {
  background-color: var(--state-info-bg);
  color: var(--state-info);
}

.notice-close {
  color: inherit;
  opacity: 0.7;
}

/* ===== 灵感 ===== */
.inspirations {
  margin-top: 22px;
}

.inspiration-caption {
  margin-bottom: 8px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.inspiration-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.inspiration-chip {
  padding: 6px 12px;
  font-size: var(--text-xs);
  line-height: 1.6;
  text-align: left;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-full);
  background-color: var(--color-surface);
  color: var(--color-text-secondary);
  transition: border-color 0.15s, color 0.15s;
}

.inspiration-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* ===== 结果 ===== */
.results {
  margin-top: 26px;
}

.results-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.text-btn {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.text-btn:hover {
  color: var(--color-primary);
}


/* ── 资产库（所有已完成的图片 / 视频）──────────────────────── */
.asset-library {
  margin-top: 30px;
  padding-top: 18px;
  border-top: 1px solid var(--color-border-light);
}

.asset-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.asset-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text-primary);
}

.asset-total {
  padding: 1px 7px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-tertiary);
  background-color: var(--color-surface-secondary);
  border-radius: var(--radius-full);
}

.asset-tabs {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.asset-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 14px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  transition: background-color 0.15s, color 0.15s;
}

.asset-tab.on {
  background-color: var(--color-surface);
  color: var(--color-text-primary);
  font-weight: 600;
  box-shadow: var(--shadow-static);
}

.asset-tab-count {
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 10px;
}

.asset-card {
  position: relative;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-secondary);
}

.asset-card img,
.asset-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.2s ease;
}

.asset-card:hover img,
.asset-card:hover .asset-video {
  transform: scale(1.04);
}

.asset-ph {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-tertiary);
}

.asset-badge {
  position: absolute;
  left: 6px;
  bottom: 6px;
  padding: 1px 6px;
  font-size: 11px;
  color: #fff;
  background-color: rgba(0, 0, 0, 0.55);
  border-radius: var(--radius-sm);
}

.asset-empty {
  padding: 16px 0;
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
}

.result-grid {
  /* auto-fit + 固定上限轨道：1 张时居中不撑满，多张时排成整齐一行 */
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 320px));
  justify-content: center;
  gap: 14px;
}

.result-card {
  position: relative;
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
}

.result-card img,
.result-video {
  display: block;
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  object-fit: cover;
  cursor: zoom-in;
}

.result-video {
  cursor: default;
  background-color: #000;
}

.result-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  height: 100%;
  min-height: 160px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  background: linear-gradient(
    110deg,
    var(--color-surface-secondary) 30%,
    color-mix(in srgb, var(--color-primary) 6%, var(--color-surface-secondary)) 50%,
    var(--color-surface-secondary) 70%
  );
  background-size: 200% 100%;
  animation: shimmer 1.6s linear infinite;
}

@keyframes shimmer {
  from {
    background-position: 120% 0;
  }
  to {
    background-position: -120% 0;
  }
}

.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}

.result-overlay {
  position: absolute;
  inset: 0 0 auto 0;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  padding: 8px;
  opacity: 0;
  /* 隐藏时必须同时让出指针事件：否则这块透明层会挡住视频的原生播放按钮 */
  pointer-events: none;
  transition: opacity 0.18s;
  background: linear-gradient(to bottom, rgba(0, 0, 0, 0.34), transparent);
}

.result-card:hover .result-overlay {
  opacity: 1;
  pointer-events: auto;
}

.ov-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  background-color: rgba(255, 255, 255, 0.9);
  color: #1a2332;
}

.ov-btn:hover {
  background-color: #fff;
}

.ov-btn.danger:hover {
  color: var(--state-error);
}

.result-caption {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border-light);
}

.result-prompt {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.result-meta {
  font-size: 11px;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);
}

/* ===== 大图预览 ===== */
.lightbox {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  background-color: rgba(9, 14, 20, 0.72);
}

.lightbox-panel {
  display: flex;
  gap: 0;
  max-width: 1080px;
  max-height: 86vh;
  overflow: hidden;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-elevated);
}

.lightbox-media {
  display: block;
  max-width: min(70vw, 760px);
  max-height: 86vh;
  object-fit: contain;
  background-color: #000;
}

.lightbox-side {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 260px;
  padding: 18px;
  border-left: 1px solid var(--color-border-light);
}

.lightbox-title {
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--color-text-primary);
  word-break: break-word;
}

.lightbox-meta {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  word-break: break-word;
}

.lightbox-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
}

.mini-btn {
  padding: 5px 10px;
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.mini-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* ===== 侧栏 ===== */
.sidebar {
  background-color: var(--color-surface);
  border-right: 1px solid var(--color-border);
}

.sidebar-header {
  padding: 20px;
}

.new-chat-btn {
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
  background-color: transparent;
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.new-chat-btn:hover {
  background-color: var(--color-primary-lighter);
}

.list-caption {
  padding: 0 9px 6px;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--color-text-tertiary);
}

.task-hint {
  color: var(--color-text-tertiary);
  line-height: 1.7;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 7px 8px;
  margin-bottom: 2px;
  border-radius: var(--radius-md);
  text-align: left;
  transition: background-color 0.15s;
}

.history-item:hover {
  background-color: var(--color-surface-secondary);
}

.history-item.active {
  background-color: var(--color-primary-lighter);
}

.history-thumb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-tertiary);
}

.history-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.history-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.history-prompt {
  font-size: var(--text-xs);
  color: var(--color-text-primary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.history-meta {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--color-text-tertiary);
}

.sidebar-footer {
  border-top: 1px solid var(--color-border);
}

.sidebar-nav-btn {
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.sidebar-nav-btn:hover {
  background-color: var(--color-surface-secondary);
}

.sidebar-nav-btn.logout:hover {
  background-color: var(--state-error-bg);
  color: var(--state-error);
}

.chat-list::-webkit-scrollbar {
  width: 4px;
}

.hidden-input {
  display: none;
}

/* 窄屏：大图预览改成上下排布，避免右侧信息被挤没 */
@media (max-width: 900px) {
  .lightbox-panel {
    flex-direction: column;
    max-width: 92vw;
  }

  .lightbox-media {
    max-width: 92vw;
    max-height: 60vh;
  }

  .lightbox-side {
    width: auto;
    border-left: none;
    border-top: 1px solid var(--color-border-light);
  }
}
</style>
