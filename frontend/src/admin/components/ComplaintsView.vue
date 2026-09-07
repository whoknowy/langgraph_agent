<template>
  <div>
    <h2 class="page-title">投诉处理</h2>
    <div class="card">
      <div class="toolbar">
        <select v-model="status" @change="load">
          <option value="">全部状态</option>
          <option value="处理中">处理中</option>
          <option value="已升级">已升级</option>
          <option value="已解决">已解决</option>
        </select>
        <input v-model="q" placeholder="搜索单号/会员号/姓名" @keyup.enter="load" />
        <button class="btn" @click="load">查询</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>单号</th><th>会员</th><th>内容</th><th>状态</th><th>创建时间</th><th>回复</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in complaints" :key="c.ticket_no">
            <td>{{ c.ticket_no }}</td>
            <td>{{ c.member_name }} ({{ c.member_id }})</td>
            <td class="wrap">{{ c.content }}</td>
            <td><span class="badge" :class="c.status === '已解决' ? 'badge-ok' : 'badge-pending'">{{ c.status }}</span></td>
            <td>{{ c.created_at }}</td>
            <td class="wrap">{{ c.reply || '-' }}</td>
            <td>
              <template v-if="c.status === '处理中' || c.status === '已升级'">
                <button class="btn btn-success btn-sm" @click="resolve(c)">解决</button>
                <button v-if="c.status === '处理中'" class="btn btn-sm" @click="escalate(c)">升级</button>
              </template>
              <button v-if="c.status === '已解决'" class="btn btn-sm" @click="reopen(c)">重新打开</button>
            </td>
          </tr>
          <tr v-if="!complaints.length"><td colspan="7" class="empty">暂无投诉</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api, qs } from '../../api.js'

const complaints = ref([])
const status = ref('')
const q = ref('')

onMounted(load)

async function load() {
  try {
    const d = await api('/admin/api/complaints' + qs({ status: status.value, q: q.value }), { admin: true })
    complaints.value = d.complaints || []
  } catch (e) { alert(e.message) }
}

async function resolve(c) {
  const reply = prompt('处理回复', c.reply || '')
  if (reply === null) return
  try {
    await api('/admin/api/complaints/resolve', { method: 'POST', body: { ticket_no: c.ticket_no, reply }, admin: true })
    await load()
  } catch (e) { alert(e.message) }
}

async function escalate(c) {
  const note = prompt('升级备注', '')
  if (note === null) return
  try {
    await api('/admin/api/complaints/escalate', { method: 'POST', body: { ticket_no: c.ticket_no, note }, admin: true })
    await load()
  } catch (e) { alert(e.message) }
}

async function reopen(c) {
  try {
    await api('/admin/api/complaints/reopen', { method: 'POST', body: { ticket_no: c.ticket_no }, admin: true })
    await load()
  } catch (e) { alert(e.message) }
}
</script>

<style scoped>
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
.toolbar select, .toolbar input { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; }
.wrap { white-space: normal; max-width: 260px; }
</style>