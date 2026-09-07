// 统一 API 请求封装：JWT Bearer 通道与 Web Cookie 并存时，优先使用本地 token。
const MEMBER_TOKEN_KEY = 'flight_member_token'
const ADMIN_TOKEN_KEY = 'flight_admin_token'

export function getMemberToken() {
  return localStorage.getItem(MEMBER_TOKEN_KEY) || ''
}
export function setMemberToken(token) {
  if (token) localStorage.setItem(MEMBER_TOKEN_KEY, token)
  else localStorage.removeItem(MEMBER_TOKEN_KEY)
}
export function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || ''
}
export function setAdminToken(token) {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token)
  else localStorage.removeItem(ADMIN_TOKEN_KEY)
}
export function clearTokens() {
  setMemberToken('')
  setAdminToken('')
}

/**
 * 发起 API 请求。
 * @param {string} path 接口路径，如 /api/my/orders
 * @param {object} options { method, body, admin }
 */
export async function api(path, options = {}) {
  const { method = 'GET', body, admin = false } = options
  const headers = { 'Content-Type': 'application/json' }
  const token = admin ? getAdminToken() : getMemberToken()
  if (token) headers['Authorization'] = 'Bearer ' + token

  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined
  })

  let data = {}
  try {
    data = await res.json()
  } catch (e) {
    data = {}
  }

  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`)
    err.status = res.status
    err.data = data
    throw err
  }
  return data
}

export function qs(params = {}) {
  const usp = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') usp.set(k, v)
  })
  const s = usp.toString()
  return s ? '?' + s : ''
}
