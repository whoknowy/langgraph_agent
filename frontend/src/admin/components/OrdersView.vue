<template>
  <div>
    <h2 class="page-title">订单查询</h2>
    <div class="card">
      <div class="toolbar">
        <select v-model="status" @change="load">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
        <input v-model="q" placeholder="搜索订单号/会员号/会员名" @keyup.enter="load" />
        <button class="btn" @click="load">查询</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>订单号</th><th>会员</th><th>航班</th><th>航线</th><th>日期</th><th>舱位</th><th>金额</th><th>状态</th><th>创建时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="o in orders" :key="o.order_no">
            <td>{{ o.order_no }}</td>
            <td>{{ o.member_name }} ({{ o.member_id }})</td>
            <td>{{ o.airline }} {{ o.flight_no }}</td>
            <td>{{ o.dep_city }}-{{ o.arr_city }}</td>
            <td>{{ o.flight_date }} {{ o.dep_time }}</td>
            <td>{{ o.cabin }}</td>
            <td>¥{{ o.amount }}</td>
            <td><span class="badge" :class="statusClass(o.status)">{{ o.status }}</span></td>
            <td>{{ o.created_at }}</td>
          </tr>
          <tr v-if="!orders.length"><td colspan="9" class="empty">暂无订单</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api, qs } from '../../api.js'

const orders = ref([])
const status = ref('')
const q = ref('')
const statuses = ['待支付', '已出票', '已改签', '退票中', '已退款', '已取消', '已使用']

onMounted(load)

async function load() {
  try {
    const d = await api('/admin/api/orders' + qs({ status: status.value, q: q.value }), { admin: true })
    orders.value = d.orders || []
  } catch (e) { alert(e.message) }
}

function statusClass(s) {
  if (['已出票','已改签','已使用'].includes(s)) return 'badge-info'
  if (['待支付','退票中'].includes(s)) return 'badge-pending'
  if (s === '已退款') return 'badge-ok'
  if (s === '已取消') return 'badge-danger'
  return 'badge-info'
}
</script>

<style scoped>
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.toolbar select, .toolbar input { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; }
</style>