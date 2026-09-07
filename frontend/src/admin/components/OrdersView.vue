<template>
  <div>
    <h2 class="page-title">订单查询</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="status" placeholder="全部状态" style="width: 140px" @change="load">
          <el-option value="">全部状态</el-option>
          <el-option v-for="s in statuses" :key="s" :value="s">{{ s }}</el-option>
        </el-select>
        <el-input v-model="q" placeholder="搜索订单号/会员号/会员名" clearable style="width: 260px" @keyup.enter="load" />
        <el-button @click="load">查询</el-button>
      </div>
      <el-table :data="orders" v-loading="loading" style="width: 100%">
        <el-table-column prop="order_no" label="订单号" min-width="140" />
        <el-table-column label="会员" min-width="160">
          <template #default="{ row }">{{ row.member_name }} ({{ row.member_id }})</template>
        </el-table-column>
        <el-table-column label="航班" min-width="150">
          <template #default="{ row }">{{ row.airline }} {{ row.flight_no }}</template>
        </el-table-column>
        <el-table-column label="航线" min-width="120">
          <template #default="{ row }">{{ row.dep_city }}-{{ row.arr_city }}</template>
        </el-table-column>
        <el-table-column label="日期" min-width="150">
          <template #default="{ row }">{{ row.flight_date }} {{ row.dep_time }}</template>
        </el-table-column>
        <el-table-column prop="cabin" label="舱位" width="80" />
        <el-table-column label="金额" width="90">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="160" />
        <template #empty><el-empty description="暂无订单" /></template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { toastError } from '../../ui.js'
import { api, qs } from '../../api.js'

const orders = ref([])
const status = ref('')
const q = ref('')
const loading = ref(false)
const statuses = ['待支付', '已出票', '已改签', '退票中', '已退款', '已取消', '已使用']

onMounted(load)

async function load() {
  loading.value = true
  try {
    const d = await api('/admin/api/orders' + qs({ status: status.value, q: q.value }), { admin: true })
    orders.value = d.orders || []
  } catch (e) { toastError(e.message) } finally { loading.value = false }
}

function statusType(s) {
  if (['已出票','已改签','已使用'].includes(s)) return 'primary'
  if (['待支付','退票中'].includes(s)) return 'warning'
  if (s === '已退款') return 'success'
  if (s === '已取消') return 'danger'
  return 'info'
}
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
</style>