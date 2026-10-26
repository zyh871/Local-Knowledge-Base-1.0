import net from 'node:net'
import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

/** 后端默认端口。改后端端口时只需在 frontend/.env 里设 VITE_API_TARGET */
const DEFAULT_API_TARGET = 'http://127.0.0.1:8000'

/** TCP 探活：只判断端口是否有人监听，不发 HTTP 请求（不打扰后端、不受鉴权影响） */
function isReachable(target, timeout = 400) {
  return new Promise((resolve) => {
    let url
    try {
      url = new URL(target)
    } catch (e) {
      resolve(false)
      return
    }
    const port = Number(url.port) || (url.protocol === 'https:' ? 443 : 80)
    const socket = net.connect({ host: url.hostname, port })
    const done = (ok) => {
      socket.removeAllListeners()
      socket.destroy()
      resolve(ok)
    }
    socket.setTimeout(timeout)
    socket.once('connect', () => done(true))
    socket.once('timeout', () => done(false))
    socket.once('error', () => done(false))
  })
}

function portOf(target) {
  try {
    const url = new URL(target)
    return url.port || (url.protocol === 'https:' ? '443' : '80')
  } catch (e) {
    return '8000'
  }
}

/**
 * 解析真正要用的代理目标。
 *
 * 为什么要有「不可达就回落默认端口」这一步：
 * 曾经出现过一种很难自查的情况 —— `frontend/.env` 里留着
 * `VITE_API_TARGET=http://127.0.0.1:8001`（当时 8000 被另一个项目占着），
 * 后来后端回到 8000，前端却仍然把请求打到 8001，页面只报
 * `ECONNREFUSED 127.0.0.1:8001`，看不出是"配置过期"还是"后端没启动"。
 *
 * 规则：显式配置优先，但**配置指向的端口不可达时回落到默认端口**，
 * 并打印一条醒目的警告说明原因 —— 配置文件写错不该让整个前端静默失联。
 */
async function resolveApiTarget(configured) {
  if (!configured) {
    return { target: DEFAULT_API_TARGET, warning: '' }
  }
  if (await isReachable(configured)) {
    return { target: configured, warning: '' }
  }
  const fallbackOk = await isReachable(DEFAULT_API_TARGET)
  return {
    target: DEFAULT_API_TARGET,
    warning:
      `VITE_API_TARGET=${configured} 不可达，已自动改用 ${DEFAULT_API_TARGET}` +
      (fallbackOk ? '（该端口有服务在监听）' : '（该端口当前也没有服务）') +
      `。\n          如果后端确实在 ${portOf(configured)}，请先启动它；` +
      '否则删掉 frontend/.env 里这行配置。',
  }
}

export default defineConfig(async ({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const { target: apiTarget, warning } = await resolveApiTarget(env.VITE_API_TARGET)

  if (warning) {
    console.warn(`\n  ⚠️  [vite] 代理目标已修正：${warning}\n`)
  }
  console.log(`  ➜  [vite] API 代理: /api → ${apiTarget}`)

  return {
    plugins: [
      vue(),
      vueJsx(),
      vueDevTools(),
      tailwindcss(),
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          secure: false,
          ws: false,
          headers: {
            'Connection': 'keep-alive'
          },
          configure: (proxy) => {
            // 把一长串 ECONNREFUSED 堆栈换成一句能直接照做的提示。
            // 每 10 秒最多提示一次，避免轮询接口把终端刷满。
            let warnedAt = 0
            proxy.on('error', (err) => {
              const now = Date.now()
              if (now - warnedAt < 10000) return
              warnedAt = now
              const code = err.code || err.message
              console.error(
                `\n  ✖ [vite] 无法连接后端 ${apiTarget}（${code}）` +
                  `\n     启动后端：cd backend && venv/Scripts/python.exe -m uvicorn app.main:app --port ${portOf(apiTarget)}` +
                  `\n     后端不在这个端口？改 frontend/.env 的 VITE_API_TARGET（或删掉它用默认 8000）\n`
              )
            })
          },
        },
      },
    },
  }
})
