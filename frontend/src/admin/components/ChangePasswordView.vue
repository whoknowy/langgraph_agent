<template>
  <div class="pwd-page">
    <div class="card pwd-card">
      <h2>设置新密码</h2>
      <p class="muted">首次登录或仍需修改默认密码，请先设置安全密码（至少8位，不能是常见弱口令）</p>
      <div class="field">
        <label>原密码</label>
        <input v-model="oldPassword" type="password" />
      </div>
      <div class="field">
        <label>新密码</label>
        <input v-model="newPassword" type="password" />
      </div>
      <div class="field">
        <label>确认新密码</label>
        <input v-model="confirmPassword" type="password" />
      </div>
      <button class="btn btn-primary" :disabled="loading" @click="submit">保存新密码</button>
      <p v-if="error" class="error-text">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '@/shared/api.js'

const emit = defineEmits(['changed'])
defineProps({ admin: Object })
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  if (!newPassword.value || !confirmPassword.value || !oldPassword.value) {
    error.value = '请填写完整'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次新密码不一致'
    return
  }
  loading.value = true
  try {
    await api('/admin/api/change_password', {
      method: 'POST',
      body: { old_password: oldPassword.value, new_password: newPassword.value },
      admin: true
    })
    emit('changed')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.pwd-page { height: 100%; display: flex; align-items: center; justify-content: center; background: var(--bg); }
.pwd-card { width: 380px; padding: 26px; }
.pwd-card h2 { margin-bottom: 6px; }
.pwd-card .muted { font-size: 13px; margin-bottom: 16px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
.field input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: #fafafa; outline: none; }
.field input:focus { border-color: var(--primary); background: #fff; }
.pwd-card .btn { width: 100%; margin-top: 6px; }
</style>