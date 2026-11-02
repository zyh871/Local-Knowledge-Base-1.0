<template>
  <AppLayout>
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <SidebarNav mode="community" @new="router.push('/chat')" />
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

    <div class="cm-body flex-1 min-h-0 overflow-y-auto">
      <div class="cm-inner">
        <!-- 列表态 -->
        <template v-if="!current">
          <header class="page-head">
            <div>
              <h1 class="page-title">开源社区</h1>
              <p class="page-sub">
                把团队做的东西公开出来：项目、贡献者、讨论。草稿只有团队自己看得见。
              </p>
            </div>
            <button type="button" class="btn-primary" @click="openCreate">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 5v14M5 12h14" />
              </svg>
              创建项目
            </button>
          </header>

          <div class="toolbar">
            <div class="tabs">
              <button
                type="button"
                :class="['tab', { on: scope === 'all' }]"
                @click="switchScope('all')"
              >
                社区项目
              </button>
              <button
                type="button"
                :class="['tab', { on: scope === 'mine' }]"
                @click="switchScope('mine')"
              >
                我的项目
              </button>
            </div>

            <template v-if="scope === 'all'">
              <input v-model="q" class="input search" placeholder="搜索项目名称、简介" @keyup.enter="reload" />
              <select v-model="sort" class="input select" @change="reload">
                <option value="recent">最近发布</option>
                <option value="stars">最多 Star</option>
                <option value="name">按名称</option>
              </select>
            </template>
          </div>

          <p v-if="error" class="banner error">{{ error }}</p>

          <p v-if="loading" class="hint center">加载中…</p>
          <p v-else-if="!projects.length" class="empty">
            <template v-if="scope === 'mine'">
              你还没有创建过项目。点右上角「创建项目」——新项目默认是草稿，只有你和团队看得见。
            </template>
            <template v-else>
              社区还没有已发布的项目。第一个发布的人通常最容易被记住。
            </template>
          </p>

          <div v-else class="project-grid">
            <button
              v-for="p in projects"
              :key="p.id"
              type="button"
              class="project-card"
              @click="openProject(p.id)"
            >
              <div class="pc-top">
                <span class="pc-name">{{ p.name }}</span>
                <span v-if="p.visibility === 'draft'" class="pill draft">草稿</span>
              </div>
              <p class="pc-summary">{{ p.summary || '暂无简介' }}</p>
              <div class="pc-meta">
                <span v-if="p.language" class="pc-lang">{{ p.language }}</span>
                <span class="pc-stat">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 3l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.8 6.2 20.9l1.1-6.5L2.6 9.8l6.5-.9z" />
                  </svg>
                  {{ p.stars_count }}
                </span>
                <span class="pc-time">{{ timeAgo(p.published_at || p.updated_at) }}</span>
              </div>
              <div v-if="p.tags?.length" class="pc-tags">
                <span v-for="t in p.tags.slice(0, 3)" :key="t" class="tag">{{ t }}</span>
              </div>
            </button>
          </div>
        </template>

        <!-- 详情态 -->
        <template v-else>
          <button type="button" class="back" @click="closeProject">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M15 18l-6-6 6-6" />
            </svg>
            返回列表
          </button>

          <p v-if="error" class="banner error">{{ error }}</p>

          <header class="detail-head">
            <div class="detail-title">
              <h1 class="page-title">{{ current.name }}</h1>
              <span :class="['pill', current.visibility === 'draft' ? 'draft' : 'live']">
                {{ VISIBILITY_LABEL[current.visibility] || current.visibility }}
              </span>
            </div>
            <p class="page-sub">{{ current.summary || '暂无简介' }}</p>
            <p class="detail-sub">
              由 {{ current.created_by || '未知' }} 创建 ·
              {{ timeAgo(current.published_at || current.created_at) }}
              <span v-if="current.license"> · {{ current.license }}</span>
            </p>

            <div class="action-row">
              <button
                type="button"
                :class="['btn-ghost', { starred: current.is_starred }]"
                :disabled="current.visibility !== 'published'"
                @click="toggleStar"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" :fill="current.is_starred ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="1.8">
                  <path d="M12 3l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.8 6.2 20.9l1.1-6.5L2.6 9.8l6.5-.9z" />
                </svg>
                {{ current.is_starred ? '已 Star' : 'Star' }} · {{ current.stars_count }}
              </button>

              <button
                type="button"
                :class="['btn-ghost', { starred: current.is_watching }]"
                :disabled="current.visibility !== 'published'"
                @click="toggleWatch"
              >
                {{ current.is_watching ? '已关注' : '关注' }}
              </button>

              <a v-if="current.repo_url" :href="current.repo_url" target="_blank" rel="noreferrer" class="btn-ghost">
                仓库
              </a>
              <a v-if="current.homepage" :href="current.homepage" target="_blank" rel="noreferrer" class="btn-ghost">
                主页
              </a>

              <span class="spacer"></span>

              <button
                v-if="can('project:publish')"
                type="button"
                class="btn-primary"
                @click="current.visibility === 'draft' ? publish() : unpublish()"
              >
                {{ current.visibility === 'draft' ? '发布到社区' : '撤回为草稿' }}
              </button>
              <button v-if="can('project:delete')" type="button" class="btn-danger" @click="removeProject">
                删除
              </button>
            </div>
          </header>

          <div class="detail-grid">
            <div class="detail-main">
              <section class="card">
                <h2 class="card-title">项目说明</h2>
                <p class="prose">{{ current.description || '作者还没有写说明。' }}</p>
                <div v-if="current.tags?.length" class="pc-tags">
                  <span v-for="t in current.tags" :key="t" class="tag">{{ t }}</span>
                </div>
              </section>

              <section class="card">
                <h2 class="card-title">讨论（{{ comments.length }}）</h2>

                <div class="comment-form">
                  <textarea
                    v-model="commentDraft"
                    class="input"
                    rows="2"
                    maxlength="1000"
                    placeholder="说点什么，或者问个问题"
                  ></textarea>
                  <button type="button" class="btn-primary sm" :disabled="!commentDraft.trim() || commenting" @click="submitComment">
                    {{ commenting ? '发送中…' : '发表' }}
                  </button>
                </div>

                <p v-if="!comments.length" class="hint">还没有评论。</p>

                <ul class="comment-list">
                  <li v-for="c in comments" :key="c.id" class="comment">
                    <span class="member-avatar">{{ (c.username || '?').charAt(0).toUpperCase() }}</span>
                    <div class="comment-body">
                      <p class="comment-head">
                        <span class="comment-user">{{ c.username }}</span>
                        <span class="comment-time">{{ timeAgo(c.created_at) }}</span>
                      </p>
                      <p class="comment-text">{{ c.body }}</p>
                    </div>
                    <button
                      v-if="c.username === userName || canManage"
                      type="button"
                      class="btn-icon"
                      title="删除评论"
                      @click="removeComment(c)"
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </li>
                </ul>
              </section>
            </div>

            <aside class="detail-side">
              <section class="card">
                <h2 class="card-title">贡献者（{{ contributors.length }}）</h2>
                <ul class="contributor-list">
                  <li v-for="c in contributors" :key="c.username" class="member-row">
                    <span class="member-avatar">{{ (c.username || '?').charAt(0).toUpperCase() }}</span>
                    <span class="member-name">{{ c.username }}</span>
                    <span class="pill quiet">{{ c.role === 'maintainer' ? '维护者' : '贡献者' }}</span>
                    <button
                      v-if="canManage && c.username !== current.created_by"
                      type="button"
                      class="btn-icon"
                      @click="dropContributor(c)"
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </li>
                </ul>

                <div v-if="canManage" class="add-row">
                  <input v-model="contribUsername" class="input" placeholder="用户名" maxlength="64" />
                  <button type="button" class="btn-ghost sm" :disabled="!contribUsername" @click="addContrib">添加</button>
                </div>
              </section>

              <section class="card">
                <h2 class="card-title">信息</h2>
                <dl class="kv">
                  <dt>归属团队</dt>
                  <dd>{{ teamName }}</dd>
                  <dt>语言</dt>
                  <dd>{{ current.language || '未标注' }}</dd>
                  <dt>我在项目中的角色</dt>
                  <dd>{{ current.my_project_role || '—' }}</dd>
                </dl>
              </section>
            </aside>
          </div>
        </template>
      </div>
    </div>

    <!-- 创建项目 -->
    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <div class="modal">
        <h2 class="card-title">创建项目</h2>
        <p class="hint no-mt">新项目是草稿，只有团队成员看得见。写好之后再发布。</p>

        <label class="field">
          <span class="field-label">归属团队</span>
          <select v-model="newProject.team_id" class="input">
            <option value="">选择团队</option>
            <option v-for="t in teams" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
        </label>

        <label class="field">
          <span class="field-label">项目名称</span>
          <input v-model="newProject.name" class="input" maxlength="80" />
        </label>

        <label class="field">
          <span class="field-label">一句话简介</span>
          <input v-model="newProject.summary" class="input" maxlength="300" />
        </label>

        <div class="field-row">
          <label class="field">
            <span class="field-label">语言</span>
            <select v-model="newProject.language" class="input">
              <option value="">未标注</option>
              <option v-for="l in LANGUAGES" :key="l" :value="l">{{ l }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field-label">标签（逗号分隔）</span>
            <input v-model="newProject.tags" class="input" placeholder="RAG, FastAPI" />
          </label>
        </div>

        <label class="field">
          <span class="field-label">仓库地址（可选）</span>
          <input v-model="newProject.repo_url" class="input" placeholder="https://…" />
        </label>

        <label class="field">
          <span class="field-label">项目说明（可选）</span>
          <textarea v-model="newProject.description" class="input" rows="3"></textarea>
        </label>

        <p v-if="createError" class="banner error">{{ createError }}</p>

        <div class="modal-actions">
          <button type="button" class="btn-ghost" @click="creating = false">取消</button>
          <button
            type="button"
            class="btn-primary"
            :disabled="!newProject.team_id || !newProject.name || creatingBusy"
            @click="submitCreate"
          >
            {{ creatingBusy ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <transition name="toast">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </transition>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '../layouts/AppLayout.vue'
import SidebarNav from '../components/sidebar/SidebarNav.vue'
import UserMenu from '../components/sidebar/UserMenu.vue'
import { useTheme } from '../composables/useTheme'
import { listTeams, getPermissions } from '../api/teams'
import {
  LANGUAGES,
  VISIBILITY_LABEL,
  addComment,
  addContributor,
  createProject,
  deleteComment,
  deleteProject,
  getProject,
  listComments,
  listContributors,
  listProjects,
  publishProject,
  removeContributor,
  starProject,
  timeAgo,
  unpublishProject,
  unstarProject,
  unwatchProject,
  watchProject,
} from '../api/community'

const router = useRouter()
const { theme, toggleTheme } = useTheme()
const userName = ref(localStorage.getItem('username') || '')

const scope = ref('all')
const q = ref('')
const sort = ref('recent')
const projects = ref([])
const loading = ref(false)
const error = ref('')
const toast = ref('')

const teams = ref([])

const current = ref(null)
const contributors = ref([])
const comments = ref([])
const caps = ref([])
const commentDraft = ref('')
const commenting = ref(false)
const contribUsername = ref('')

const creating = ref(false)
const creatingBusy = ref(false)
const createError = ref('')
const newProject = reactive({
  team_id: '',
  name: '',
  summary: '',
  description: '',
  language: '',
  tags: '',
  repo_url: '',
})

/* ── 派生 ────────────────────────────────────────────── */

function can(cap) {
  return caps.value.includes(cap)
}

/** 维护者（或团队里具备 project:write）能管贡献者与评论。 */
const canManage = computed(() => can('project:write') || current.value?.my_project_role === 'maintainer')

/**
 * 归属团队显示名字而不是 `team_xxxx` id。
 * 拿不到（例如刚被移出团队）时退回 id，至少不是空白。
 */
const teamName = computed(() => {
  const id = current.value?.team_id
  if (!id) return '—'
  return teams.value.find((t) => t.id === id)?.name || id
})

/* ── 列表 ────────────────────────────────────────────── */

function flash(text) {
  toast.value = text
  setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2200)
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    projects.value = await listProjects({
      q: scope.value === 'all' ? q.value : '',
      sort: sort.value,
      mine: scope.value === 'mine',
    })
  } catch (e) {
    error.value = e.message
    projects.value = []
  } finally {
    loading.value = false
  }
}

function switchScope(next) {
  if (scope.value === next) return
  scope.value = next
  reload()
}

/* ── 详情 ────────────────────────────────────────────── */

async function openProject(projectId) {
  error.value = ''
  try {
    const project = await getProject(projectId)
    current.value = project
    commentDraft.value = ''
    contribUsername.value = ''

    const [cs, ms, perms] = await Promise.all([
      listContributors(projectId),
      listComments(projectId),
      // 团队权限拿不到（例如项目已不属于我所在的团队）不该阻断详情展示
      getPermissions(project.team_id).catch(() => ({ permissions: [] })),
    ])
    contributors.value = cs
    comments.value = ms
    caps.value = perms.permissions || []
  } catch (e) {
    error.value = e.message
  }
}

function closeProject() {
  current.value = null
  contributors.value = []
  comments.value = []
  caps.value = []
  error.value = ''
  reload()
}

/* ── 互动 ────────────────────────────────────────────── */

async function toggleStar() {
  if (!current.value) return
  const id = current.value.id
  try {
    current.value = current.value.is_starred
      ? await unstarProject(id)
      : await starProject(id)
  } catch (e) {
    error.value = e.message
  }
}

async function toggleWatch() {
  if (!current.value) return
  const id = current.value.id
  try {
    current.value = current.value.is_watching
      ? await unwatchProject(id)
      : await watchProject(id)
  } catch (e) {
    error.value = e.message
  }
}

async function publish() {
  try {
    current.value = await publishProject(current.value.id)
    flash('已发布到社区')
  } catch (e) {
    error.value = e.message
  }
}

async function unpublish() {
  try {
    current.value = await unpublishProject(current.value.id)
    flash('已撤回为草稿')
  } catch (e) {
    error.value = e.message
  }
}

async function removeProject() {
  const name = current.value?.name || '该项目'
  if (!window.confirm(`删除「${name}」？项目、贡献者与讨论会一并删除。`)) return
  try {
    await deleteProject(current.value.id)
    closeProject()
    flash('项目已删除')
  } catch (e) {
    error.value = e.message
  }
}

async function submitComment() {
  const body = commentDraft.value.trim()
  if (!body) return
  commenting.value = true
  try {
    await addComment(current.value.id, body)
    commentDraft.value = ''
    comments.value = await listComments(current.value.id)
  } catch (e) {
    error.value = e.message
  } finally {
    commenting.value = false
  }
}

async function removeComment(comment) {
  if (!window.confirm('删除这条评论？')) return
  try {
    await deleteComment(current.value.id, comment.id)
    comments.value = await listComments(current.value.id)
  } catch (e) {
    error.value = e.message
  }
}

async function addContrib() {
  const username = contribUsername.value.trim()
  if (!username) return
  try {
    await addContributor(current.value.id, { username })
    contribUsername.value = ''
    contributors.value = await listContributors(current.value.id)
    flash('贡献者已添加')
  } catch (e) {
    error.value = e.message
  }
}

async function dropContributor(contributor) {
  if (!window.confirm(`移除贡献者 ${contributor.username}？`)) return
  try {
    await removeContributor(current.value.id, contributor.username)
    contributors.value = await listContributors(current.value.id)
  } catch (e) {
    error.value = e.message
  }
}

/* ── 创建 ────────────────────────────────────────────── */

async function openCreate() {
  createError.value = ''
  Object.assign(newProject, {
    team_id: teams.value[0]?.id || '',
    name: '',
    summary: '',
    description: '',
    language: '',
    tags: '',
    repo_url: '',
  })
  creating.value = true
  if (!teams.value.length) {
    try {
      teams.value = await listTeams()
      if (!newProject.team_id) newProject.team_id = teams.value[0]?.id || ''
    } catch (e) {
      createError.value = e.message
    }
  }
}

async function submitCreate() {
  creatingBusy.value = true
  createError.value = ''
  try {
    const project = await createProject({
      team_id: newProject.team_id,
      name: newProject.name.trim(),
      summary: newProject.summary.trim(),
      description: newProject.description,
      language: newProject.language,
      repo_url: newProject.repo_url.trim(),
      tags: newProject.tags
        .split(/[,，\s]+/)
        .map((t) => t.trim())
        .filter(Boolean)
        .slice(0, 8),
    })
    creating.value = false
    flash('项目已创建（草稿）')
    await openProject(project.id)
  } catch (e) {
    createError.value = e.message
  } finally {
    creatingBusy.value = false
  }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  localStorage.removeItem('username')
  router.push('/login')
}

onMounted(async () => {
  await reload()
  try {
    teams.value = await listTeams()
  } catch {
    // 团队列表只用于创建时选归属，拿不到不影响浏览
    teams.value = []
  }
})
</script>

<style scoped>
.cm-body {
  background-color: var(--color-bg);
}

.cm-inner {
  max-width: 1120px;
  margin: 0 auto;
  padding: 28px 28px 56px;
}

@media (max-width: 900px) {
  .cm-inner {
    padding: 20px 16px 48px;
  }
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.page-title {
  font-family: var(--font-display);
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text-primary);
}

.page-sub {
  margin-top: 6px;
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text-secondary);
}

/* ── 工具栏 ──────────────────────────────────────────── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.tabs {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.tab {
  padding: 5px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
}

.tab.on {
  color: var(--color-text-primary);
  font-weight: 600;
  background-color: var(--color-surface);
  box-shadow: var(--shadow-static);
}

.input {
  padding: 8px 10px;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  background-color: var(--color-surface-secondary);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition: border-color 0.15s, background-color 0.15s;
}

.input:focus {
  background-color: var(--color-surface);
  border-color: var(--color-primary);
}

.input.search {
  flex: 1;
  min-width: 160px;
}

.input.select {
  cursor: pointer;
}

textarea.input {
  width: 100%;
  resize: vertical;
}

/* ── 项目网格 ────────────────────────────────────────── */
.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.project-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  text-align: left;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.project-card:hover {
  border-color: var(--color-border);
  box-shadow: var(--shadow-elevated);
  transform: translateY(-1px);
}

.pc-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pc-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.pc-summary {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.9em;
  font-size: var(--text-sm);
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.pc-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.pc-lang {
  padding: 1px 7px;
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
}

.pc-stat {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--state-warning);
}

.pc-time {
  margin-left: auto;
}

.pc-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.tag {
  padding: 1px 7px;
  font-size: var(--text-xs);
  color: var(--color-primary);
  border-radius: var(--radius-full);
  background-color: var(--color-primary-lighter);
}

/* ── 详情 ────────────────────────────────────────────── */
.back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 14px;
  padding: 4px 8px 4px 4px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
}

.back:hover {
  background-color: var(--color-hover);
  color: var(--color-text-primary);
}

.detail-head {
  margin-bottom: 16px;
}

.detail-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-sub {
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.action-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  flex-wrap: wrap;
}

.spacer {
  flex: 1;
}

.detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  align-items: start;
}

@media (max-width: 900px) {
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.detail-main,
.detail-side {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.card {
  padding: 18px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
}

.card-title {
  margin-bottom: 12px;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.prose {
  font-size: var(--text-sm);
  line-height: 1.75;
  white-space: pre-wrap;
  color: var(--color-text-secondary);
}

/* ── 评论 ────────────────────────────────────────────── */
.comment-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.comment-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.comment {
  display: flex;
  gap: 9px;
  padding: 9px 8px;
  border-radius: var(--radius-md);
}

.comment:hover {
  background-color: var(--color-hover);
}

.comment-body {
  flex: 1;
  min-width: 0;
}

.comment-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.comment-user {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.comment-time {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.comment-text {
  margin-top: 3px;
  font-size: var(--text-sm);
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text-secondary);
}

/* ── 贡献者 / 键值 ───────────────────────────────────── */
.contributor-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 6px;
  border-radius: var(--radius-md);
}

.member-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-inverse);
  border-radius: 50%;
  background-color: var(--color-text-tertiary);
}

.member-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.add-row {
  display: flex;
  gap: 6px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-light);
}

.add-row .input {
  flex: 1;
  min-width: 0;
}

.kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  font-size: var(--text-xs);
}

.kv dt {
  color: var(--color-text-tertiary);
}

.kv dd {
  margin: 0;
  overflow: hidden;
  word-break: break-all;
  color: var(--color-text-secondary);
}

/* ── 通用小件 ────────────────────────────────────────── */
.pill {
  padding: 2px 8px;
  font-size: var(--text-xs);
  border-radius: var(--radius-full);
}

.pill.draft {
  color: var(--color-text-secondary);
  background-color: var(--color-surface-secondary);
}

.pill.live {
  color: var(--state-success);
  background-color: var(--state-success-bg);
}

.pill.quiet {
  color: var(--color-text-secondary);
  background-color: var(--color-surface-secondary);
}

.hint {
  margin-top: 10px;
  font-size: var(--text-xs);
  line-height: 1.7;
  color: var(--color-text-tertiary);
}

.hint.no-mt {
  margin-top: 0;
  margin-bottom: 12px;
}

.hint.center {
  padding: 40px 0;
  text-align: center;
  font-size: var(--text-sm);
}

.empty {
  padding: 48px 20px;
  text-align: center;
  font-size: var(--text-sm);
  line-height: 1.8;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-lg);
  border: 1px dashed var(--color-border);
}

.banner.error {
  margin-bottom: 14px;
  padding: 9px 12px;
  font-size: var(--text-sm);
  color: var(--color-error);
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
}

.field {
  display: block;
  margin-bottom: 12px;
}

.field-label {
  display: block;
  margin-bottom: 5px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.field-row .input {
  width: 100%;
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-primary-contrast);
  border-radius: var(--radius-md);
  background-color: var(--color-primary);
  transition: opacity 0.15s;
}

.btn-primary:hover {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.btn-primary.sm {
  padding: 7px 12px;
  font-size: var(--text-xs);
  align-self: flex-end;
}

.btn-ghost {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  text-decoration: none;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  transition: background-color 0.15s, color 0.15s;
}

.btn-ghost:hover {
  background-color: var(--color-hover);
  color: var(--color-text-primary);
}

.btn-ghost:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.btn-ghost.sm {
  padding: 5px 10px;
  font-size: var(--text-xs);
}

.btn-ghost.starred {
  color: var(--state-warning);
  border-color: var(--state-warning);
  background-color: var(--state-warning-bg);
}

.btn-danger {
  padding: 7px 12px;
  font-size: var(--text-sm);
  color: var(--color-error);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-error);
}

.btn-danger:hover {
  background-color: var(--state-error-bg);
}

.btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
}

.btn-icon:hover {
  color: var(--color-error);
  background-color: var(--state-error-bg);
}

.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background-color: rgba(15, 23, 42, 0.45);
}

.modal {
  width: 100%;
  max-width: 500px;
  max-height: 90vh;
  overflow-y: auto;
  padding: 20px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-elevated);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.toast {
  position: fixed;
  left: 50%;
  bottom: 32px;
  z-index: 70;
  transform: translateX(-50%);
  padding: 8px 16px;
  font-size: var(--text-sm);
  color: #fff;
  border-radius: var(--radius-full);
  background-color: rgba(26, 35, 50, 0.92);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 8px);
}
</style>
