<template>
  <div v-if="!state.ready" class="app-loading">
    <div class="loading-logo">✈</div>
    <span>加载中…</span>
  </div>
  <LoginView v-else-if="!state.member" @login="signIn" />
  <router-view v-else />
</template>

<script setup>
import { onMounted } from 'vue'
import { useAuth } from '@/client/stores/auth.js'
import LoginView from './components/LoginView.vue'

const { state, checkAuth, signIn } = useAuth()

onMounted(checkAuth)
</script>

<style scoped>
.app-loading {
  height: 100%;
  display: flex; flex-direction: column; gap: 14px;
  align-items: center; justify-content: center;
  color: var(--text-muted);
}
.loading-logo {
  width: 56px; height: 56px; border-radius: 18px;
  background: var(--brand-gradient); color: #fff; font-size: 26px;
  display: flex; align-items: center; justify-content: center;
  animation: float 1.6s ease-in-out infinite;
}
@keyframes float { 50% { transform: translateY(-6px); } }
</style>
