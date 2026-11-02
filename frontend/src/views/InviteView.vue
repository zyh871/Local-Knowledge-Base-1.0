<template>
  <div class="invite-page">
    <div class="invite-card">
      <div class="brand">
        <BrandTitle text="智鲸" />
      </div>

      <template v-if="!done">
        <h1 class="title">接受邀请</h1>
        <p class="sub">
          设置一个账号即可加入。邀请链接只能用一次，过期或已被使用都会失败。
        </p>

        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="form.username"
            class="input"
            maxlength="32"
            autocomplete="username"
            placeholder="3–32 位，字母/数字/._-"
          />
        </label>

        <label class="field">
          <span class="field-label">密码</span>
          <input
            v-model="form.password"
            class="input"
            type="password"
            autocomplete="new-password"
            placeholder="至少 8 位"
          />
        </label>

        <label class="field">
          <span class="field-label">显示名称（可选）</span>
          <input v-model="form.display_name" class="input" maxlength="64" />
        </label>

        <p v-if="error" class="banner error">{{ error }}</p>

        <button type="button" class="submit" :disabled="!canSubmit || busy" @click="accept">
          {{ busy ? '提交中…' : '接受邀请并创建账号' }}
        </button>

        <p class="foot">
          已有账号？<a href="/login">直接登录</a>
        </p>
      </template>

      <template v-else>
        <h1 class="title">已加入</h1>
        <p class="sub">
          账号 <strong>{{ createdUser }}</strong> 创建成功。
          <template v-if="teamId">你已被加入对应团队。</template>
        </p>
        <button type="button" class="submit" @click="goLogin">去登录</button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandTitle from '../components/BrandTitle.vue'
import { request } from '../api/http'

const route = useRoute()
const router = useRouter()

const token = computed(() => String(route.params.token || ''))

const form = reactive({ username: '', password: '', display_name: '' })
const busy = ref(false)
const error = ref('')
const done = ref(false)
const createdUser = ref('')
const teamId = ref('')

const canSubmit = computed(
  () => form.username.trim().length >= 3 && form.password.length >= 8
)

async function accept() {
  busy.value = true
  error.value = ''
  try {
    const res = await request('/invitations/accept', {
      method: 'POST',
      body: {
        token: token.value,
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
      },
    })
    createdUser.value = res.user?.username || form.username.trim()
    teamId.value = res.invitation?.team_id || ''
    done.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function goLogin() {
  router.push('/login')
}
</script>

<style scoped>
.invite-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background-color: var(--color-bg);
}

.invite-card {
  width: 100%;
  max-width: 400px;
  padding: 28px;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-elevated);
}

.brand {
  margin-bottom: 18px;
}

.title {
  font-family: var(--font-display);
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text-primary);
}

.sub {
  margin: 8px 0 18px;
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--color-text-secondary);
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
  padding: 9px 11px;
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

.submit {
  width: 100%;
  margin-top: 6px;
  padding: 10px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-primary-contrast);
  border-radius: var(--radius-md);
  background-color: var(--color-primary);
}

.submit:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.banner.error {
  margin-bottom: 12px;
  padding: 9px 11px;
  font-size: var(--text-sm);
  color: var(--color-error);
  border-radius: var(--radius-md);
  background-color: var(--state-error-bg);
}

.foot {
  margin-top: 16px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

.foot a {
  color: var(--color-primary);
  text-decoration: none;
}
</style>
