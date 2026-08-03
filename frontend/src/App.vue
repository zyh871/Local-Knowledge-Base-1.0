<template>
  <div :class="['app-root', theme]">
    <router-view />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'

const theme = ref(localStorage.getItem('theme') || 'light')

function toggleTheme() {
  theme.value = theme.value === 'light' ? 'dark' : 'light'
  localStorage.setItem('theme', theme.value)
}

onMounted(() => {
  document.documentElement.classList.add(theme.value)
})

watch(theme, (newVal, oldVal) => {
  document.documentElement.classList.remove(oldVal)
  document.documentElement.classList.add(newVal)
})

defineExpose({ theme, toggleTheme })
</script>

<style>
@import "tailwindcss";

@theme {
  --font-display: 'Georgia', 'Noto Serif SC', serif;
  --font-body: 'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;

  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.875rem;
  --text-lg: 1rem;
  --text-xl: 1.125rem;
  --text-2xl: 1.375rem;
  --text-3xl: 1.75rem;

  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-full: 9999px;

  --shadow-static: 0 1px 2px rgba(0,0,0,0.04);
  --shadow-elevated: 0 8px 24px rgba(0,0,0,0.05);

  --color-primary: #5B7FA5;
  --color-primary-light: #7B9BBF;
  --color-primary-lighter: #E8EEF4;
  --color-primary-dark: #3D5A75;
  --color-primary-contrast: #FFFFFF;

  --color-bg: #F8F9FB;
  --color-surface: #FFFFFF;
  --color-surface-secondary: #F2F4F7;
  --color-border: #E4E8ED;
  --color-border-light: #EEF0F3;

  --color-text-primary: #1A2332;
  --color-text-secondary: #5F6B7A;
  --color-text-tertiary: #8C95A1;
  --color-text-inverse: #FFFFFF;

  --state-success: #4A9B6E;
  --state-success-bg: #EDF7F1;
  --state-warning: #C49A2E;
  --state-warning-bg: #FBF5E4;
  --state-error: #C45050;
  --state-error-bg: #FBEEED;
  --state-info: #5B7FA5;
  --state-info-bg: #E8EEF4;
}

.dark {
  --color-primary: #7B9BBF;
  --color-primary-light: #9BB3CF;
  --color-primary-lighter: #2A3A4A;
  --color-primary-dark: #5B7FA5;
  --color-primary-contrast: #1A2332;

  --color-bg: #121820;
  --color-surface: #1A2330;
  --color-surface-secondary: #232D3B;
  --color-border: #2E3A4A;
  --color-border-light: #262F3D;

  --color-text-primary: #E8EEF4;
  --color-text-secondary: #9AA8B8;
  --color-text-tertiary: #6B7A8C;
  --color-text-inverse: #1A2332;

  --state-success: #5DB380;
  --state-success-bg: #1E3A2E;
  --state-warning: #D9B35A;
  --state-warning-bg: #3A3020;
  --state-error: #D97070;
  --state-error-bg: #3A2525;
  --state-info: #7B9BBF;
  --state-info-bg: #2A3A4A;
}

html, body {
  height: 100%;
}

body {
  font-family: var(--font-body);
  background-color: var(--color-bg);
  color: var(--color-text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app, .app-root {
  height: 100%;
  min-height: 100vh;
}

::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--color-text-tertiary);
}

button {
  font-family: var(--font-body);
  cursor: pointer;
  border: none;
  outline: none;
  background: none;
  color: inherit;
}

input, textarea, select {
  font-family: var(--font-body);
  outline: none;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes typing {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}
</style>
