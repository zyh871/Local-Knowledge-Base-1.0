<template>
  <div class="min-h-screen flex items-center justify-center px-4 py-8 bg-[var(--color-bg)]">
    <div class="w-full max-w-md bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-lg p-8">
      <div class="text-center mb-8">
        <div class="w-14 h-14 mx-auto mb-4 flex items-center justify-center bg-[var(--color-primary-lighter)] text-[var(--color-primary)] rounded-xl">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </div>
        <h1 class="text-xl font-semibold text-[var(--color-text-primary)] mb-2">知识库问答</h1>
        <p class="text-sm text-[var(--color-text-tertiary)]">基于 RAG 技术的智能问答助手</p>
      </div>

      <div class="space-y-4">
        <div>
          <label class="block text-sm font-medium text-[var(--color-text-secondary)] mb-1">用户名</label>
          <input
            v-model="loginForm.username"
            type="text"
            class="w-full px-4 py-2.5 text-sm text-[var(--color-text-primary)] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-primary)] focus:ring-2 focus:ring-[var(--color-primary-lighter)] transition-all"
            placeholder="请输入用户名"
            @keyup.enter="handleLogin"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-[var(--color-text-secondary)] mb-1">密码</label>
          <input
            v-model="loginForm.password"
            type="password"
            class="w-full px-4 py-2.5 text-sm text-[var(--color-text-primary)] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg focus:outline-none focus:border-[var(--color-primary)] focus:ring-2 focus:ring-[var(--color-primary-lighter)] transition-all"
            placeholder="请输入密码"
            @keyup.enter="handleLogin"
          />
        </div>
      </div>

      <button @click="handleLogin" :disabled="loading" class="w-full mt-6 py-2.5 text-base font-medium text-[var(--color-text-inverse)] bg-[var(--color-primary)] border border-[var(--color-primary)] rounded-lg hover:bg-[var(--color-primary-dark)] hover:border-[var(--color-primary-dark)] disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 transition-all">
        <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
        <span>{{ loading ? '登录中...' : '登 录' }}</span>
      </button>

      <p v-if="loginError" class="mt-4 text-sm text-[var(--state-error)] text-center">{{ loginError }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const loading = ref(false)
const loginError = ref('')
const loginForm = ref({ username: '', password: '' })

const handleLogin = async () => {
  if (!loginForm.value.username || !loginForm.value.password) {
    loginError.value = '请输入用户名和密码'
    return
  }

  loading.value = true
  loginError.value = ''

  try {
    const response = await fetch('/api/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        username: loginForm.value.username,
        password: loginForm.value.password
      })
    })

    if (!response.ok) {
      throw new Error('登录失败，请检查用户名和密码')
    }

    const data = await response.json()
    const expireTime = Date.now() + 24 * 60 * 60 * 1000
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('username', loginForm.value.username)
    localStorage.setItem('tokenExpire', expireTime.toString())
    router.push('/chat')
  } catch (err) {
    loginError.value = err.message
  } finally {
    loading.value = false
  }
}
</script>
