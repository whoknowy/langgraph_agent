// 会员登录态：轻量响应式 store（Vue 官方推荐的小应用模式，免额外依赖）。
// 替代原先 App → router-view → Layout → router-view → View 的逐层 props 透传。
import { reactive, readonly } from 'vue'
import { api, setMemberToken } from '@/shared/api.js'

const state = reactive({
  member: null,   // 当前登录会员，null=未登录
  ready: false    // 首次 /api/me 探测是否已结束
})

/** 应用启动时探测登录态（Cookie 或本地 token 任一有效即放行） */
async function checkAuth() {
  try {
    const d = await api('/api/me')
    state.member = d.member || null
  } catch (e) {
    state.member = null
  } finally {
    state.ready = true
  }
}

/** 登录/注册成功后写入（LoginView 的 login 事件载荷原样传入） */
function signIn(data) {
  if (data && data.token) setMemberToken(data.token)
  state.member = data ? data.member : null
}

async function logout() {
  try { await api('/api/logout', { method: 'POST' }) } catch (e) { /* 忽略，本地态照常清 */ }
  setMemberToken('')
  state.member = null
}

export function useAuth() {
  return { state: readonly(state), checkAuth, signIn, logout }
}
