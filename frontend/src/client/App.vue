<template>
  <div v-if="loading" class="app-loading">加载中…</div>
  <LoginView v-else-if="!member" @login="onLogin" />
  <router-view v-else :member="member" @logout="logout" />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api, setMemberToken, getMemberToken, clearTokens } from '../api.js'
import LoginView from './components/LoginView.vue'

const member = ref(null)
const loading = ref(true)

async function checkAuth() {
  try {
    const data = await api('/api/me')
    member.value = data.member || null
    // Web Cookie 已登录但本地没有 token 时，后端仍会通过 cookie 放行。
    // 这里保留 cookie 通道，因此不强制要求 token。
  } catch (e) {
    member.value = null
  } finally {
    loading.value = false
  }
}

function onLogin(data) {
  if (data.token) setMemberToken(data.token)
  member.value = data.member
}

async function logout() {
  try { await api('/api/logout', { method: 'POST' }) } catch (e) {}
  setMemberToken('')
  member.value = null
}

onMounted(checkAuth)
</script>

<style scoped>
.app-loading {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
</style>
