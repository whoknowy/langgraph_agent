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
      <el-tabs v-model="mode" stretch>
        <el-tab-pane label="会员登录" name="suffix" />
        <el-tab-pane label="账号登录" name="password" />
        <el-tab-pane label="注册" name="register" />
      </el-tabs>

      <el-form v-if="mode === 'suffix'" label-position="top" @submit.prevent="doLogin">
        <el-form-item label="会员号"><el-input v-model="memberId" placeholder="如 M1001" maxlength="10" /></el-form-item>
        <el-form-item label="手机号后4位"><el-input v-model="phoneSuffix" type="password" placeholder="手机尾号" maxlength="4" show-password /></el-form-item>
        <el-button type="primary" class="login-btn" :loading="loading" @click="doLogin">登 录</el-button>
      </el-form>

      <el-form v-else-if="mode === 'password'" label-position="top" @submit.prevent="doPasswordLogin">
        <el-form-item label="会员号 / 手机号"><el-input v-model="account" placeholder="账号" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="password" type="password" placeholder="密码" show-password /></el-form-item>
        <el-button type="primary" class="login-btn" :loading="loading" @click="doPasswordLogin">登 录</el-button>
      </el-form>

      <el-form v-else label-position="top" @submit.prevent="doRegister">
        <el-form-item label="姓名"><el-input v-model="regForm.name" placeholder="姓名" maxlength="20" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="regForm.phone" placeholder="11位手机号" maxlength="11" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="regForm.password" type="password" placeholder="至少6位" show-password /></el-form-item>
        <el-form-item label="确认密码"><el-input v-model="regForm.password2" type="password" placeholder="确认密码" show-password /></el-form-item>
        <el-button type="primary" class="login-btn" :loading="loading" @click="doRegister">注册并登录</el-button>
      </el-form>

      <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 12px" />
      <el-alert v-if="message" :title="message" type="success" :closable="false" style="margin-top: 12px" />

      <div class="demo-box" v-if="mode !== 'register'">
        <p class="muted">演示账号（点击自动填入）：</p>
        <div class="demo-items">
          <el-button v-for="acc in demoAccounts" :key="acc.member_id" class="demo-item" @click="fillDemo(acc)">
            <strong>{{ acc.member_id }}</strong>
            <span>{{ acc.name }} · {{ acc.phone_suffix }}</span>
          </el-button>
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
  } catch (e) { console.warn(e) }
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
  } catch (e) { error.value = e.message } finally { loading.value = false }
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
  } catch (e) { error.value = e.message } finally { loading.value = false }
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
  } catch (e) { error.value = e.message } finally { loading.value = false }
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
  width: 400px; background: #fff; border-radius: 18px; padding: 20px 24px 26px;
  box-shadow: 0 30px 80px rgba(0,0,0,.3);
}
.login-btn { width: 100%; margin-top: 4px; }
.demo-box { margin-top: 18px; border-top: 1px dashed var(--border); padding-top: 14px; }
.demo-items { display: flex; gap: 8px; margin-top: 8px; }
.demo-item { flex: 1; flex-direction: column; height: auto; padding: 8px; }
.demo-item strong { color: var(--primary); }
.demo-item span { font-size: 12px; color: var(--text-muted); }
@media (max-width: 760px) {
  .login-page { flex-direction: column; gap: 20px; }
  .login-hero { display: none; }
}
</style>
