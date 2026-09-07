<template>
  <div>
    <h2 class="page-title">退款处理（退票中队列）</h2>
    <div class="card">
      <table class="data-table">
        <thead>
          <tr><th>订单号</th><th>会员</th><th>航班</th><th>航线</th><th>金额</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in refunds" :key="r.order_no">
            <td>{{ r.order_no }}</td>
            <td>{{ r.member_name }} ({{ r.member_id }})</td>
            <td>{{ r.airline }} {{ r.flight_no }}</td>
            <td>{{ r.dep_city }}-{{ r.arr_city }} {{ r.flight_date }}</td>
            <td>¥{{ r.amount }}</td>
            <td><span class="badge badge-pending">{{ r.status }}</span></td>
            <td>
              <button class="btn btn-success btn-sm" @click="approve(r)">同意退款</button>
              <button class="btn btn-danger btn-sm" @click="reject(r)">驳回</button>
            </td>
          </tr>
          <tr v-if="!refunds.length"><td colspan="7" class="empty">暂无待处理退款</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const refunds = ref([])

onMounted(load)

async function load() {
  try {
    const d = await api('/admin/api/refunds', { admin: true })
    refunds.value = d.refunds || []
  } catch (e) { alert(e.message) }
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