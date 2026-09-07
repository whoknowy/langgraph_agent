<template>
  <div>
    <h2 class="page-title">会员</h2>
    <div class="card">
      <div class="toolbar">
        <input v-model="q" placeholder="搜索会员号/姓名/手机号" @keyup.enter="load" />
        <button class="btn" @click="load">查询</button>
      </div>
      <table class="data-table">
        <thead><tr><th>会员号</th><th>姓名</th><th>手机号</th><th>邮箱</th><th>等级</th></tr></thead>
        <tbody>
          <tr v-for="m in members" :key="m.member_id">
            <td>{{ m.member_id }}</td><td>{{ m.name }}</td><td>{{ m.phone }}</td><td>{{ m.email || '-' }}</td><td><span class="badge badge-info">{{ m.level }}</span></td>
          </tr>
          <tr v-if="!members.length"><td colspan="5" class="empty">暂无会员</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const members = ref([])
const q = ref('')

onMounted(load)

async function load() {
  try {
    const d = await api('/admin/api/customers' + (q.value ? '?q=' + encodeURIComponent(q.value) : ''), { admin: true })
    members.value = d.customers || []
  } catch (e) { alert(e.message) }
}
</script>

<style scoped>
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.toolbar input { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; }
</style>