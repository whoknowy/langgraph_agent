<template>
  <div class="admin-login">
    <div class="login-card">
      <h1>🛫 运营管理平台</h1>
      <p class="muted">多智能体航空客服系统 · 管理端</p>
      <div class="field">
        <label>管理员用户名</label>
        <input v-model="username" placeholder="admin" autocomplete="off" @keyup.enter="doLogin" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="password" type="password" placeholder="密码" @keyup.enter="doLogin" />
      </div>
      <button class="login-btn" :disabled="loading" @click="doLogin">{{ loading ? '登录中…' : '登 录' }}</button>
      <p v-if="error" class="error-text">{{ error }}</p>
      <p class="hint">演示账号：<code>admin</code> / <code>admin123</code></p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../../api.js'

const emit = defineEmits(['login'])
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function doLogin() {
  if (!username.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const d = await api('/admin/api/login', {
      method: 'POST',
      body: { username: username.value, password: password.value },
      admin: true
    })
    emit('login', d)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.admin-login {
  height: 100%; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #1e293b, #334155);
}
.login-card { width: 360px; background: #fff; border-radius: 14px; padding: 30px 28px; box-shadow: 0 20px 60px rgba(0,0,0,.35); }
.login-card h1 { font-size: 20px; margin-bottom: 4px; }
.login-card .muted { margin-bottom: 18px; font-size: 12.5px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
.field input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: #fafafa; outline: none; }
.field input:focus { border-color: var(--primary); background: #fff; }
.login-btn { width: 100%; padding: 10px; border: none; border-radius: 9px; background: var(--primary); color: #fff; font-size: 15px; font-weight: 600; }
.hint { margin-top: 12px; font-size: 12px; color: var(--text-muted); border-top: 1px dashed var(--border); padding-top: 10px; }
</style>