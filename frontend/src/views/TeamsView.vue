<template>
  <AppLayout>
    <template #sidebar>
      <aside class="sidebar hidden lg:flex w-[260px] shrink-0 flex-col min-h-0">
        <SidebarNav mode="teams" @new="router.push('/chat')" />
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

    <div class="teams-body flex-1 min-h-0 overflow-y-auto">
      <div class="teams-inner">
        <header class="page-head">
          <div>
            <h1 class="page-title">团队空间</h1>
            <p class="page-sub">
              成员、角色与权限都在这里管。谁能读、谁能写、谁能发布，由角色一次性决定。
            </p>
          </div>
          <button type="button" class="btn-primary" @click="openCreate">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
            创建团队
          </button>
        </header>

        <p v-if="error" class="banner error">{{ error }}</p>

        <div class="teams-grid">
          <!-- 左：团队列表 -->
          <aside class="team-list">
            <p class="list-label">我的团队（{{ teams.length }}）</p>

            <p v-if="!loadingTeams && teams.length === 0" class="list-empty">
              还没有加入任何团队。<br />创建一个，或让对方把你拉进去。
            </p>

            <button
              v-for="t in teams"
              :key="t.id"
              type="button"
              :class="['team-item', { on: t.id === selectedId }]"
              @click="selectTeam(t.id)"
            >
              <span class="team-avatar">{{ (t.name || '?').charAt(0) }}</span>
              <span class="team-meta">
                <span class="team-name">{{ t.name }}</span>
                <span class="team-sub">{{ t.member_count }} 人 · {{ roleLabel(t.my_role) }}</span>
              </span>
            </button>
          </aside>

          <!-- 右：详情 -->
          <section class="team-detail">
            <p v-if="!selectedId" class="detail-empty">
              选择左侧的团队查看成员与权限。
            </p>

            <template v-else-if="detail">
              <!-- 基本信息 -->
              <div class="card">
                <div class="card-head">
                  <h2 class="card-title">基本信息</h2>
                  <span class="pill">{{ roleLabel(detail.team.my_role) }}</span>
                </div>

                <label class="field">
                  <span class="field-label">团队名称</span>
                  <input
                    v-model="form.name"
                    class="input"
                    maxlength="80"
                    :disabled="!can('team:update')"
                  />
                </label>

                <label class="field">
                  <span class="field-label">描述</span>
                  <textarea
                    v-model="form.description"
                    class="input"
                    rows="2"
                    maxlength="500"
                    :disabled="!can('team:update')"
                    placeholder="这个团队负责什么"
                  ></textarea>
                </label>

                <div class="card-actions">
                  <button
                    v-if="can('team:update')"
                    type="button"
                    class="btn-ghost"
                    :disabled="!dirty || savingBase"
                    @click="saveBase"
                  >
                    {{ savingBase ? '保存中…' : '保存修改' }}
                  </button>
                  <button
                    v-if="can('team:update')"
                    type="button"
                    class="btn-danger"
                    @click="removeTeam"
                  >
                    删除团队
                  </button>
                  <span class="owner-note">所有者：{{ detail.team.owner }}</span>
                </div>
              </div>

              <!-- 成员 -->
              <div class="card">
                <div class="card-head">
                  <h2 class="card-title">成员（{{ detail.members.length }}）</h2>
                </div>

                <ul class="member-list">
                  <li v-for="m in detail.members" :key="m.username" class="member-row">
                    <span class="member-avatar">{{ (m.username || '?').charAt(0).toUpperCase() }}</span>
                    <span class="member-name">
                      {{ m.username }}
                      <span v-if="m.username === detail.team.owner" class="owner-tag">所有者</span>
                    </span>

                    <select
                      v-if="canManageMember(m)"
                      class="role-select"
                      :value="m.role"
                      @change="changeRole(m, $event.target.value)"
                    >
                      <option v-for="r in ASSIGNABLE_ROLES" :key="r.key" :value="r.key">
                        {{ r.label }}
                      </option>
                    </select>
                    <span v-else class="pill quiet">{{ roleLabel(m.role) }}</span>

                    <button
                      v-if="canManageMember(m)"
                      type="button"
                      class="btn-icon"
                      title="移除成员"
                      @click="removeMember(m)"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                    <span v-else class="btn-icon ghost"></span>
                  </li>
                </ul>

                <div v-if="can('team:manage_members')" class="invite-row">
                  <input v-model="inviteUsername" class="input" placeholder="输入已注册的用户名" maxlength="64" />
                  <select v-model="inviteRole" class="role-select">
                    <option v-for="r in ASSIGNABLE_ROLES" :key="r.key" :value="r.key">{{ r.label }}</option>
                  </select>
                  <button type="button" class="btn-primary sm" :disabled="!inviteUsername || inviting" @click="addMember">
                    {{ inviting ? '添加中…' : '添加成员' }}
                  </button>
                </div>

                <div v-if="can('team:manage_members')" class="invite-link">
                  <div class="invite-link-head">
                    <span class="field-label">邀请链接</span>
                    <button type="button" class="btn-ghost sm" :disabled="makingLink" @click="makeInviteLink">
                      {{ makingLink ? '生成中…' : '生成链接' }}
                    </button>
                  </div>
                  <p v-if="inviteLink" class="link-box">
                    <code>{{ inviteLink }}</code>
                    <button type="button" class="btn-ghost sm" @click="copyLink">复制</button>
                  </p>
                  <p v-else class="hint">
                    链接只显示一次，有效期 72 小时，用后即失效。适合发给还没有账号的人。
                  </p>
                </div>
              </div>

              <!-- 我的权限 -->
              <div class="card">
                <div class="card-head">
                  <h2 class="card-title">我的有效权限</h2>
                  <span class="pill">{{ roleLabel(detail.perms.role) }}</span>
                </div>
                <div class="cap-wrap">
                  <span v-for="c in sortedCaps" :key="c" class="cap">
                    {{ capabilityLabel(c) }}
                  </span>
                  <span v-if="!sortedCaps.length" class="hint">没有可用权限。</span>
                </div>
                <p class="hint">
                  权限按角色折算成能力字符串。后端每个端点自己校验，前端只决定「按钮显不显示」，不作为安全边界。
                </p>
              </div>

              <!-- 转让 -->
              <div v-if="can('team:update') && detail.team.my_role === 'owner'" class="card">
                <div class="card-head">
                  <h2 class="card-title">转让所有权</h2>
                </div>
                <div class="invite-row">
                  <select v-model="transferTo" class="role-select wide">
                    <option value="">选择接手的人</option>
                    <option
                      v-for="m in transferTargets"
                      :key="m.username"
                      :value="m.username"
                    >
                      {{ m.username }}（{{ roleLabel(m.role) }}）
                    </option>
                  </select>
                  <button type="button" class="btn-danger" :disabled="!transferTo" @click="transfer">
                    确认转让
                  </button>
                </div>
                <p class="hint">转让后你会降为管理员，且无法撤销。</p>
              </div>
            </template>
          </section>
        </div>
      </div>
    </div>

    <!-- 创建团队弹窗 -->
    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <div class="modal">
        <h2 class="card-title">创建团队</h2>
        <label class="field">
          <span class="field-label">名称</span>
          <input v-model="newTeam.name" class="input" maxlength="80" placeholder="例如：知识库小组" />
        </label>
        <label class="field">
          <span class="field-label">描述（可选）</span>
          <textarea v-model="newTeam.description" class="input" rows="2" maxlength="500"></textarea>
        </label>
        <div class="modal-actions">
          <button type="button" class="btn-ghost" @click="creating = false">取消</button>
          <button type="button" class="btn-primary" :disabled="!newTeam.name || creatingBusy" @click="submitCreate">
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
import {
  ASSIGNABLE_ROLES,
  CAPABILITY_LABEL,
  ROLE_LABEL,
  addMember as apiAddMember,
  createTeam as apiCreateTeam,
  createTeamInvitation,
  deleteTeam as apiDeleteTeam,
  getPermissions,
  getTeam,
  listMembers,
  listTeams,
  removeMember as apiRemoveMember,
  sortCapabilities,
  transferOwner,
  updateTeam as apiUpdateTeam,
} from '../api/teams'

const router = useRouter()
const { theme, toggleTheme } = useTheme()
const userName = ref(localStorage.getItem('username') || '')

const teams = ref([])
const loadingTeams = ref(false)
const selectedId = ref('')
const detail = ref(null)
const error = ref('')
const toast = ref('')

const form = reactive({ name: '', description: '' })
const savingBase = ref(false)

const inviteUsername = ref('')
const inviteRole = ref('viewer')
const inviting = ref(false)
const makingLink = ref(false)
const inviteLink = ref('')

const transferTo = ref('')

const creating = ref(false)
const creatingBusy = ref(false)
const newTeam = reactive({ name: '', description: '' })

/* ── 派生 ────────────────────────────────────────────── */

/** 只有「名称或描述真的变了」才让保存按钮可点，避免无意义的写请求。 */
const dirty = computed(() => {
  if (!detail.value) return false
  return (
    form.name !== detail.value.team.name ||
    form.description !== detail.value.team.description
  )
})

/** 转让候选人：除自己以外的成员。 */
const transferTargets = computed(() =>
  (detail.value?.members || []).filter((m) => m.username !== detail.value?.team.owner)
)

/** 权限列表稳定排序（后端是 set，顺序每次都可能不同）。 */
const sortedCaps = computed(() =>
  sortCapabilities(detail.value?.perms?.permissions)
)

function roleLabel(role) {
  return ROLE_LABEL[role] || role || '未知'
}

function capabilityLabel(cap) {
  return CAPABILITY_LABEL[cap] || cap
}

/** 当前用户在这个团队里是否具备某项能力（仅用于控制 UI）。 */
function can(cap) {
  return Boolean(detail.value?.perms?.permissions?.includes(cap))
}

/**
 * 能否管理这个成员：需要有 manage_members 能力，且
 * 不能动所有者、不能动自己、不能动比自己高的角色。
 * 后端也有同一套规则，这里只是别让用户点出一个必然 403 的按钮。
 */
function canManageMember(member) {
  if (!detail.value) return false
  const team = detail.value.team
  if (member.username === team.owner) return false
  if (member.username === userName.value) return false
  if (!can('team:manage_members')) return false
  if (team.my_role === 'admin' && member.role === 'admin') return false
  return true
}

/* ── 数据加载 ────────────────────────────────────────── */

async function loadTeams() {
  loadingTeams.value = true
  error.value = ''
  try {
    teams.value = await listTeams()
  } catch (e) {
    error.value = e.message
  } finally {
    loadingTeams.value = false
  }
}

async function selectTeam(teamId) {
  if (teamId === selectedId.value) return
  selectedId.value = teamId
  detail.value = null
  transferTo.value = ''
  inviteLink.value = ''
  inviteUsername.value = ''
  error.value = ''
  try {
    const [team, members, perms] = await Promise.all([
      getTeam(teamId),
      listMembers(teamId),
      getPermissions(teamId),
    ])
    detail.value = { team, members, perms }
    form.name = team.name
    form.description = team.description || ''
  } catch (e) {
    error.value = e.message
    selectedId.value = ''
  }
}

/* ── 操作 ────────────────────────────────────────────── */

function flash(text) {
  toast.value = text
  setTimeout(() => {
    if (toast.value === text) toast.value = ''
  }, 2200)
}

function openCreate() {
  newTeam.name = ''
  newTeam.description = ''
  creating.value = true
}

async function submitCreate() {
  creatingBusy.value = true
  error.value = ''
  try {
    const team = await apiCreateTeam({
      name: newTeam.name.trim(),
      description: newTeam.description.trim(),
    })
    creating.value = false
    await loadTeams()
    await selectTeam(team.id)
    flash('团队已创建')
  } catch (e) {
    error.value = e.message
  } finally {
    creatingBusy.value = false
  }
}

async function saveBase() {
  savingBase.value = true
  error.value = ''
  try {
    const team = await apiUpdateTeam(selectedId.value, {
      name: form.name.trim(),
      description: form.description.trim(),
    })
    detail.value.team = team
    const hit = teams.value.find((t) => t.id === team.id)
    if (hit) Object.assign(hit, { name: team.name, description: team.description })
    flash('已保存')
  } catch (e) {
    error.value = e.message
  } finally {
    savingBase.value = false
  }
}

async function removeTeam() {
  const name = detail.value?.team?.name || '该团队'
  if (!window.confirm(`确定删除「${name}」？成员与权限会一并删除，不可恢复。`)) return
  try {
    await apiDeleteTeam(selectedId.value)
    selectedId.value = ''
    detail.value = null
    await loadTeams()
    flash('团队已删除')
  } catch (e) {
    error.value = e.message
  }
}

async function addMember() {
  inviting.value = true
  error.value = ''
  try {
    await apiAddMember(selectedId.value, {
      username: inviteUsername.value.trim(),
      role: inviteRole.value,
    })
    inviteUsername.value = ''
    detail.value.members = await listMembers(selectedId.value)
    await loadTeams()
    flash('成员已添加')
  } catch (e) {
    error.value = e.message
  } finally {
    inviting.value = false
  }
}

async function changeRole(member, role) {
  if (role === member.role) return
  error.value = ''
  try {
    // 后端是 upsert：同一个 POST 既能拉人也能改角色
    await apiAddMember(selectedId.value, { username: member.username, role })
    detail.value.members = await listMembers(selectedId.value)
    flash(`已将 ${member.username} 设为${roleLabel(role)}`)
  } catch (e) {
    error.value = e.message
  }
}

async function removeMember(member) {
  if (!window.confirm(`把 ${member.username} 移出团队？`)) return
  error.value = ''
  try {
    await apiRemoveMember(selectedId.value, member.username)
    detail.value.members = await listMembers(selectedId.value)
    await loadTeams()
    flash('成员已移除')
  } catch (e) {
    error.value = e.message
  }
}

async function makeInviteLink() {
  makingLink.value = true
  error.value = ''
  try {
    const res = await createTeamInvitation(selectedId.value, { role: inviteRole.value })
    inviteLink.value = `${window.location.origin}${res.invite_path}`
  } catch (e) {
    error.value = e.message
  } finally {
    makingLink.value = false
  }
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(inviteLink.value)
    flash('链接已复制')
  } catch {
    // 非 HTTPS / 无剪贴板权限：让用户自己选中，别静默失败
    flash('复制失败，请手动选中链接')
  }
}

async function transfer() {
  const target = transferTo.value
  if (!target) return
  if (!window.confirm(`把所有权转给 ${target}？你会降为管理员。`)) return
  error.value = ''
  try {
    const team = await transferOwner(selectedId.value, target)
    detail.value.team = team
    detail.value.members = await listMembers(selectedId.value)
    transferTo.value = ''
    await loadTeams()
    flash('所有权已转让')
  } catch (e) {
    error.value = e.message
  }
}

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('tokenExpire')
  localStorage.removeItem('username')
  router.push('/login')
}

onMounted(loadTeams)
</script>

<style scoped>
.teams-body {
  background-color: var(--color-bg);
}

.teams-inner {
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 28px 56px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
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

.teams-grid {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

@media (max-width: 900px) {
  .teams-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .teams-inner {
    padding: 20px 16px 48px;
  }
}

/* ── 左侧列表 ────────────────────────────────────────── */
.team-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
}

.list-label {
  padding: 4px 6px 8px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.list-empty {
  padding: 6px;
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--color-text-tertiary);
}

.team-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px;
  text-align: left;
  border-radius: var(--radius-md);
  transition: background-color 0.15s;
}

.team-item:hover {
  background-color: var(--color-hover);
}

.team-item.on {
  background-color: var(--color-primary-lighter);
}

.team-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-primary-contrast);
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, var(--color-primary), #7c5cff);
}

.team-meta {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.4;
}

.team-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
}

.team-sub {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

/* ── 右侧详情 ────────────────────────────────────────── */
.team-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.detail-empty {
  padding: 48px 20px;
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-tertiary);
  border-radius: var(--radius-lg);
  border: 1px dashed var(--color-border);
}

.card {
  padding: 18px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.card-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.owner-note {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.pill {
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-primary);
  border-radius: var(--radius-full);
  background-color: var(--color-primary-lighter);
}

.pill.quiet {
  color: var(--color-text-secondary);
  background-color: var(--color-surface-secondary);
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

.input {
  width: 100%;
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

.input:disabled {
  color: var(--color-text-secondary);
  cursor: not-allowed;
}

/* ── 成员 ────────────────────────────────────────────── */
.member-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 8px;
  border-radius: var(--radius-md);
}

.member-row:hover {
  background-color: var(--color-hover);
}

.member-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  font-size: var(--text-xs);
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

.owner-tag {
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--state-warning);
  border-radius: var(--radius-full);
  background-color: var(--state-warning-bg);
}

.role-select {
  padding: 5px 8px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  background-color: var(--color-surface-secondary);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
}

.role-select.wide {
  flex: 1;
}

.invite-row {
  display: flex;
  gap: 8px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-light);
}

/* 输入框可压缩，按钮不换行：否则「添加成员」会被挤成「添加成/员」 */
.invite-row .input {
  flex: 1;
  min-width: 0;
}

.invite-row .btn-primary,
.invite-row .btn-danger {
  flex-shrink: 0;
  white-space: nowrap;
}

.invite-link {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-light);
}

.invite-link-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.link-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 7px 9px;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.link-box code {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.cap-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cap {
  padding: 3px 9px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  border-radius: var(--radius-full);
  background-color: var(--color-surface-secondary);
}

.hint {
  margin-top: 10px;
  font-size: var(--text-xs);
  line-height: 1.7;
  color: var(--color-text-tertiary);
}

/* ── 按钮 ────────────────────────────────────────────── */
.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  font-size: var(--text-sm);
  font-weight: 500;
  white-space: nowrap;
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
  white-space: nowrap;
}

.btn-ghost {
  padding: 7px 12px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
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
  padding: 4px 9px;
  font-size: var(--text-xs);
}

.btn-danger {
  padding: 7px 12px;
  font-size: var(--text-sm);
  color: var(--color-error);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-error);
  transition: background-color 0.15s;
}

.btn-danger:hover {
  background-color: var(--state-error-bg);
}

.btn-danger:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  color: var(--color-text-tertiary);
  border-radius: var(--radius-sm);
}

.btn-icon:hover {
  color: var(--color-error);
  background-color: var(--state-error-bg);
}

.btn-icon.ghost {
  pointer-events: none;
}

/* ── 提示 / 弹窗 ─────────────────────────────────────── */
.banner.error {
  margin-bottom: 14px;
  padding: 9px 12px;
  font-size: var(--text-sm);
  color: var(--color-error);
  border-radius: var(--radius-md);
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
  max-width: 420px;
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
  color: var(--color-text-inverse);
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
