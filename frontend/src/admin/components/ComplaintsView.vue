<template>
  <div>
    <h2 class="page-title">投诉处理</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="status" placeholder="全部状态" style="width: 140px" @change="load">
          <el-option value="">全部状态</el-option>
          <el-option value="处理中">处理中</el-option>
          <el-option value="已升级">已升级</el-option>
          <el-option value="已解决">已解决</el-option>
        </el-select>
        <el-input v-model="q" placeholder="搜索单号/会员号/姓名" clearable style="width: 240px" @keyup.enter="load" />
        <el-button @click="load">查询</el-button>
      </div>
      <el-table :data="complaints" v-loading="loading" style="width: 100%">
        <el-table-column prop="ticket_no" label="单号" width="110" />
        <el-table-column label="会员" min-width="160">
          <template #default="{ row }">{{ row.member_name }} ({{ row.member_id }})</template>
        </el-table-column>
        <el-table-column prop="content" label="内容" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === '已解决' ? 'success' : 'warning'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="170" />
        <el-table-column prop="reply" label="回复" min-width="160" show-overflow-tooltip />
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === '处理中' || row.status === '已升级'">
              <el-button type="success" size="small" @click="resolve(row)">解决</el-button>
              <el-button v-if="row.status === '处理中'" size="small" @click="escalate(row)">升级</el-button>
            </template>
            <el-button v-if="row.status === '已解决'" size="small" @click="reopen(row)">重新打开</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无投诉" /></template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api, qs } from '../../api.js'

const complaints = ref([])
const status = ref('')
const q = ref('')
const loading = ref(false)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const d = await api('/admin/api/complaints' + qs({ status: status.value, q: q.value }), { admin: true })
    complaints.value = d.complaints || []
  } catch (e) { alert(e.message) } finally { loading.value = false }
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
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
</style>
