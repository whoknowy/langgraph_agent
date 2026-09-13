<template>
  <div v-if="loading" class="app-loading">加载中…</div>
  <AdminLoginView v-else-if="!admin" @login="onLogin" />
  <ChangePasswordView v-else-if="admin.must_change_password" :admin="admin" @changed="onPasswordChanged" />
  <router-view v-else :admin="admin" @logout="logout" />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api, setAdminToken, getAdminToken } from '@/shared/api.js'
import AdminLoginView from './components/AdminLoginView.vue'
import ChangePasswordView from './components/ChangePasswordView.vue'

const admin = ref(null)
const loading = ref(true)

async function checkAuth() {
  try {
    const d = await api('/admin/api/me', { admin: true })
    admin.value = d.admin || null
  } catch (e) {
    admin.value = null
  } finally {
    loading.value = false
  }
}

function onLogin(data) {
  if (data.token) setAdminToken(data.token)
  admin.value = data.admin || null
}

function onPasswordChanged() {
  if (admin.value) admin.value.must_change_password = false
}

async function logout() {
  try { await api('/admin/api/logout', { method: 'POST', admin: true }) } catch (e) {}
  setAdminToken('')
  admin.value = null
}

onMounted(checkAuth)
</script>

<style scoped>
.app-loading { height: 100%; display: flex; align-items: center; justify-content: center; color: var(--text-muted); }
</style>