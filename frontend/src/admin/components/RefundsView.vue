<template>
  <div>
    <h2 class="page-title">退款处理（退票中队列）</h2>
    <el-card shadow="never">
      <el-table :data="refunds" v-loading="loading" style="width: 100%">
        <el-table-column prop="order_no" label="订单号" min-width="140" />
        <el-table-column label="会员" min-width="180">
          <template #default="{ row }">{{ row.member_name }} ({{ row.member_id }})</template>
        </el-table-column>
        <el-table-column label="航班" min-width="170">
          <template #default="{ row }">{{ row.airline }} {{ row.flight_no }}</template>
        </el-table-column>
        <el-table-column label="航线" min-width="180">
          <template #default="{ row }">{{ row.dep_city }}-{{ row.arr_city }} {{ row.flight_date }}</template>
        </el-table-column>
        <el-table-column label="金额" width="100">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><el-tag type="warning">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button type="success" size="small" @click="approve(row)">同意退款</el-button>
            <el-button type="danger" size="small" plain @click="reject(row)">驳回</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无待处理退款" /></template>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const refunds = ref([])
const loading = ref(false)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const d = await api('/admin/api/refunds', { admin: true })
    refunds.value = d.refunds || []
  } catch (e) {
    alert(e.message)
  } finally {
    loading.value = false
  }
}

async function approve(r) {
  const amount = prompt('退款金额（默认全额 ' + r.amount + '）', String(r.amount))
  if (amount === null) return
  try {
    const d = await api('/admin/api/refunds/approve', {
      method: 'POST', body: { order_no: r.order_no, refund_amount: Number(amount) || undefined }, admin: true
    })
    alert(d.message || '已退款')
    await load()
  } catch (e) { alert(e.message) }
}

async function reject(r) {
  const note = prompt('驳回原因（可留空）', '')
  if (note === null) return
  try {
    const d = await api('/admin/api/refunds/reject', {
      method: 'POST', body: { order_no: r.order_no, admin_note: note }, admin: true
    })
    alert(d.message || '已驳回')
    await load()
  } catch (e) { alert(e.message) }
}
</script>
