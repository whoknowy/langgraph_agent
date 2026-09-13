<template>
  <div>
    <h2 class="page-title">会员</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q" placeholder="搜索会员号/姓名/手机号" clearable style="width: 280px" @keyup.enter="load" />
        <el-button @click="load">查询</el-button>
      </div>
      <el-table :data="members" v-loading="loading" style="width: 100%">
        <el-table-column prop="member_id" label="会员号" width="120" />
        <el-table-column prop="name" label="姓名" width="140" />
        <el-table-column prop="phone" label="手机号" width="150" />
        <el-table-column prop="email" label="邮箱" min-width="180" />
        <el-table-column label="等级" width="120">
          <template #default="{ row }"><el-tag type="primary">{{ row.level }}</el-tag></template>
        </el-table-column>
        <template #empty><el-empty description="暂无会员" /></template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '@/shared/api.js'
import { toastError } from '@/shared/ui.js'

const members = ref([])
const q = ref('')
const loading = ref(false)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const d = await api('/admin/api/customers' + (q.value ? '?q=' + encodeURIComponent(q.value) : ''), { admin: true })
    members.value = d.customers || []
  } catch (e) { toastError(e.message) } finally { loading.value = false }
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
</style>
