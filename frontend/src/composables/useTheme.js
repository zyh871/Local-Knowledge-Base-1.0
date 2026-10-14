import { ref } from 'vue'

const STORAGE_KEY = 'theme'

function readStored() {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'light'
  } catch (e) {
    return 'light'
  }
}

// 模块级单例：AppLayout 与各模式视图共享同一个主题状态。
// 之前 App.vue 与 Chat.vue 各维护一份 `theme`，改一处另一处不同步。
const theme = ref(readStored())

function applyTheme(next) {
  const root = document.documentElement
  root.classList.remove('light', 'dark')
  root.classList.add(next)
}

function setTheme(next) {
  theme.value = next === 'dark' ? 'dark' : 'light'
  try {
    localStorage.setItem(STORAGE_KEY, theme.value)
  } catch (e) {
    // localStorage 可能被禁用（隐私模式），失败不影响主题切换
  }
  applyTheme(theme.value)
}

function toggleTheme() {
  setTheme(theme.value === 'light' ? 'dark' : 'light')
}

function initTheme() {
  applyTheme(theme.value)
}

export function useTheme() {
  return { theme, setTheme, toggleTheme, initTheme }
}
