<template>
  <div class="login-page">
    <div class="login-hero">
      <div class="brand">
        <div class="brand-icon">✈</div>
        <div>
          <h1>智能航空客服系统</h1>
          <p>让出行更简单 · 多智能体 × 真实航班数据</p>
        </div>
      </div>
      <div class="hero-planes">
        <div class="plane plane-1">✈</div>
        <div class="plane plane-2">✈</div>
      </div>
      <ul class="hero-points">
        <li><span>AI</span> 智能对话预订机票</li>
        <li><span>✔</span> 值机选座 · 电子登机牌</li>
        <li><span>⚡</span> 退改签 · 投诉 · 行程规划</li>
      </ul>
    </div>

    <div class="login-card">
      <div class="tabs">
        <button :class="{ active: mode === 'suffix' }" @click="mode = 'suffix'">会员登录</button>
        <button :class="{ active: mode === 'password' }" @click="mode = 'password'">账号登录</button>
        <button :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>

      <template v-if="mode === 'suffix'">
        <div class="field">
          <label>会员号</label>
          <input v-model="memberId" placeholder="如 M1001" maxlength="10" />
        </div>
        <div class="field">
          <label>手机号后4位</label>
          <input v-model="phoneSuffix" type="password" placeholder="手机尾号" maxlength="4" />
        </div>
        <button class="login-btn" :disabled="loading" @click="doLogin">登 录</button>
      </template>

      <template v-else-if="mode === 'password'">
        <div class="field">
          <label>会员号 / 手机号</label>
          <input v-model="account" placeholder="账号" />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="password" type="password" placeholder="密码" />
        </div>
        <button class="login-btn" :disabled="loading" @click="doPasswordLogin">登 录</button>
      </template>

      <template v-else>
        <div class="field">
          <label>姓名</label>
          <input v-model="regForm.name" placeholder="姓名" maxlength="20" />
        </div>
        <div class="field">
          <label>手机号</label>
          <input v-model="regForm.phone" placeholder="11位手机号" maxlength="11" />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="regForm.password" type="password" placeholder="至少6位" />
        </div>
        <div class="field">
          <label>确认密码</label>
          <input v-model="regForm.password2" type="password" placeholder="确认密码" />
        </div>
        <button class="login-btn" :disabled="loading" @click="doRegister">注册并登录</button>
      </template>

      <p v-if="error" class="error-text">{{ error }}</p>
      <p v-if="message" class="success-text">{{ message }}</p>

      <div class="demo-box" v-if="mode !== 'register'">
        <p class="muted">演示账号（点击自动填入）：</p>
        <div class="demo-items">
          <button v-for="acc in demoAccounts" :key="acc.member_id" class="demo-item" @click="fillDemo(acc)">
            <strong>{{ acc.member_id }}</strong>
            <span>{{ acc.name }} · {{ acc.phone_suffix }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const emit = defineEmits(['login'])
const mode = ref('suffix')
const memberId = ref('')
const phoneSuffix = ref('')
const account = ref('')
const password = ref('')
const regForm = ref({ name: '', phone: '', password: '', password2: '' })
const demoAccounts = ref([])
const loading = ref(false)
const error = ref('')
const message = ref('')

onMounted(async () => {
  try {
    const d = await api('/api/demo_accounts')
    demoAccounts.value = d.accounts || []
  } catch (e) {
    console.warn(e)
  }
})

function fillDemo(acc) {
  memberId.value = acc.member_id
  phoneSuffix.value = acc.phone_suffix
  mode.value = 'suffix'
}

async function doLogin() {
  error.value = ''
  message.value = ''
  if (!memberId.value || !phoneSuffix.value) {
    error.value = '请输入会员号和手机号后4位'
    return
  }
  loading.value = true
  try {
    const d = await api('/api/login', {
      method: 'POST',
      body: { member_id: memberId.value, phone_suffix: phoneSuffix.value }
    })
    error.value = ''
    message.value = d.message || '登录成功'
    emit('login', d)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function doPasswordLogin() {
  error.value = ''
  message.value = ''
  if (!account.value || !password.value) {
    error.value = '请输入账号和密码'
    return
  }
  loading.value = true
  try {
    const d = await api('/api/login', {
      method: 'POST',
      body: { account: account.value, password: password.value }
    })
    error.value = ''
    message.value = d.message || '登录成功'
    emit('login', d)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  error.value = ''
  message.value = ''
  const r = regForm.value
  if (!r.name || !r.phone || !r.password || !r.password2) {
    error.value = '请完整填写注册信息'
    return
  }
  if (r.password !== r.password2) {
    error.value = '两次密码不一致'
    return
  }
  loading.value = true
  try {
    const d = await api('/api/register', {
      method: 'POST',
      body: { name: r.name, phone: r.phone, password: r.password }
    })
    error.value = ''
    message.value = d.message || '注册成功'
    emit('login', d)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0b3a82 0%, #2563eb 55%, #60a5fa 100%);
  padding: 24px;
  gap: 48px;
}
.login-hero {
  color: #fff;
  max-width: 420px;
}
.brand { display: flex; gap: 14px; align-items: center; }
.brand-icon {
  width: 58px; height: 58px; display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,.16); border-radius: 16px; font-size: 30px;
}
.brand h1 { font-size: 30px; margin-bottom: 6px; }
.brand p { opacity: .85; font-size: 14px; }
.hero-planes { position: relative; height: 160px; margin: 20px 0; }
.plane { position: absolute; font-size: 80px; opacity: .35; }
.plane-1 { left: 0; top: 30px; transform: rotate(-12deg); }
.plane-2 { right: 20px; top: 0; transform: rotate(12deg); font-size: 50px; opacity: .2; }
.hero-points { list-style: none; display: flex; flex-direction: column; gap: 14px; font-size: 16px; }
.hero-points span {
  display: inline-block; width: 28px; height: 28px; margin-right: 10px; text-align: center; line-height: 28px;
  background: rgba(255,255,255,.18); border-radius: 8px;
}
.login-card {
  width: 380px; background: #fff; border-radius: 18px; padding: 26px 24px;
  box-shadow: 0 30px 80px rgba(0,0,0,.3);
}
.tabs { display: flex; gap: 6px; margin-bottom: 20px; }
.tabs button {
  flex: 1; padding: 9px; border: none; background: #f3f4f6; border-radius: 9px;
  color: var(--text-muted); font-size: 14px; font-weight: 500;
}
.tabs button.active { background: var(--primary); color: #fff; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
.field input {
  width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: #fafafa; outline: none;
}
.field input:focus { border-color: var(--primary); background: #fff; }
.login-btn {
  width: 100%; padding: 11px; border: none; border-radius: 10px; background: var(--primary);
  color: #fff; font-size: 15px; font-weight: 600; margin-top: 4px;
}
.login-btn:disabled { opacity: .6; }
.demo-box { margin-top: 18px; border-top: 1px dashed var(--border); padding-top: 14px; }
.demo-items { display: flex; gap: 8px; margin-top: 8px; }
.demo-item {
  flex: 1; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: #fff;
  display: flex; flex-direction: column; gap: 2px; align-items: center;
}
.demo-item:hover { border-color: var(--primary); background: var(--primary-light); }
.demo-item strong { color: var(--primary); }
.demo-item span { font-size: 12px; color: var(--text-muted); }
@media (max-width: 760px) {
  .login-page { flex-direction: column; gap: 20px; }
  .login-hero { display: none; }
}
</style>
