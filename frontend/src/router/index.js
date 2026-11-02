import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue')
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('../views/Chat.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/work',
    name: 'Work',
    component: () => import('../views/WorkView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/create',
    name: 'Create',
    component: () => import('../views/CreateView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/models',
    name: 'ModelSettings',
    component: () => import('../views/ModelSettings.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/teams',
    name: 'Teams',
    component: () => import('../views/TeamsView.vue'),
    meta: { requiresAuth: true }
  },
  {
    path: '/community',
    name: 'Community',
    component: () => import('../views/CommunityView.vue'),
    meta: { requiresAuth: true }
  },
  {
    // 邀请链接落地页：被邀请人此时还没有账号，必须是公开路由
    path: '/invite/:token',
    name: 'Invite',
    component: () => import('../views/InviteView.vue'),
    meta: { public: true }
  },
  {
    path: '/shared/:token',
    name: 'Shared',
    component: () => import('../views/SharedView.vue'),
    meta: { public: true }
  },
  {
    path: '/',
    redirect: '/login'
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

function isTokenValid() {
  const token = localStorage.getItem('token')
  const expireTime = localStorage.getItem('tokenExpire')
  if (!token) return false
  if (!expireTime) return false
  return Date.now() < parseInt(expireTime)
}

router.beforeEach((to, from, next) => {
  if (to.meta.public) {
    next()
    return
  }
  const isValid = isTokenValid()

  if (to.meta.requiresAuth && !isValid) {
    localStorage.removeItem('token')
    localStorage.removeItem('tokenExpire')
    next('/login')
  } else if (to.path === '/login' && isValid) {
    next('/chat')
  } else {
    next()
  }
})

export default router
