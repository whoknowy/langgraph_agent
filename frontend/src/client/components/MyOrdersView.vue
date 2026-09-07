<template>
  <div>
    <h2 class="page-title">我的订单</h2>
    <div class="order-summary card" v-if="summary.count">
      <div>共 <strong>{{ summary.count }}</strong> 笔订单</div>
      <div>累计金额 <strong>¥{{ summary.total_amount }}</strong></div>
    </div>

    <div class="order-list">
      <div v-for="o in orders" :key="o.order_no" class="card order-card">
        <div class="order-head">
          <div class="order-flight">{{ o.flight }} <span class="flight-no">{{ o.flight_no }}</span></div>
          <span class="badge" :class="statusClass(o.status)">{{ o.status }}</span>
        </div>
        <div class="order-meta">
          <span>{{ o.route }}</span>
          <span>{{ o.flight_date }} {{ o.dep_time }}</span>
          <span>{{ o.cabin }}舱 · 金额 ¥{{ o.amount }}</span>
          <span v-if="o.checked_in" class="checkin-tag">已值机 {{ o.checkin_seat }}</span>
        </div>
        <div class="order-actions">
          <template v-if="o.status === '待支付'">
            <button class="btn btn-primary btn-sm" @click="pay(o)">去支付</button>
          </template>
          <template v-if="['已出票','已改签'].includes(o.status)">
            <button v-if="!o.checked_in" class="btn btn-sm" @click="goCheckin(o)">值机选座</button>
            <button v-else class="btn btn-sm" @click="goBoardpass(o)">登机牌</button>
            <button class="btn btn-sm" @click="changeOrder(o)">改签</button>
            <button class="btn btn-danger btn-sm" @click="refund(o)">退票</button>
          </template>
          <template v-if="o.status === '退票中'">
            <span class="muted">人工审核中</span>
          </template>
        </div>
      </div>
      <div v-if="!orders.length" class="empty card">暂无订单</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, qs } from '../../api.js'
import { toastError, toastSuccess, confirmDialog, promptDialog } from '../../ui.js'

const orders = ref([])
const router = useRouter()
const summary = computed(() => {
  const total = orders.value.reduce((s, o) => s + (o.amount || 0), 0)
  return { count: orders.value.length, total_amount: total }
})

onMounted(load)

async function load() {
  try {
    const d = await api('/api/my/orders')
    orders.value = d.orders || []
  } catch (e) {
    toastError(e.message)
  }
}

function statusClass(s) {
  if (s === '已出票' || s === '已改签') return 'badge-info'
  if (s === '待支付') return 'badge-pending'
  if (s === '已退款' || s === '已使用') return 'badge-ok'
  if (s === '退票中') return 'badge-pending'
  if (s === '已取消') return 'badge-danger'
  return 'badge-info'
}

async function pay(o) {
  try {
    const d = await api('/api/pay', { method: 'POST', body: { order_no: o.order_no } })
    toastSuccess(d.message || '支付成功')
    await load()
  } catch (e) {
    toastError(e.message)
  }
}

function goCheckin(o) {
  router.push({ path: '/checkin', query: { order_no: o.order_no } })
}
function goBoardpass(o) {
  router.push({ path: '/boardpass', query: { order_no: o.order_no } })
}

async function changeOrder(o) {
  const newFlight = await promptDialog('请输入新的航班号（同航线、未来日期）', '', '改签')
  if (!newFlight) return
  const newDate = await promptDialog('请输入新日期 YYYY-MM-DD', o.flight_date, '改签日期')
  if (!newDate) return
  try {
    const quote = await api('/api/change_quote' + qs({
      order_no: o.order_no, new_flight_no: newFlight, new_date: newDate, new_cabin: o.cabin
    }))
    const diffText = quote.fare_diff > 0
      ? `需补差价 ¥${quote.fare_diff}`
      : (quote.fare_diff < 0 ? `退回差价 ¥${-quote.fare_diff}` : '无差价')
    const ok = await confirmDialog(`改签报价：${diffText}`, '确认改签')
    if (!ok) return
    const d = await api('/api/change', { method: 'POST', body: {
      order_no: o.order_no, new_flight_no: newFlight, new_date: newDate, new_cabin: o.cabin
    }})
    toastSuccess(d.message || '改签成功')
    await load()
  } catch (e) {
    toastError(e.message)
  }
}

async function refund(o) {
  const voluntary = await confirmDialog('选择"确定"走自愿退票；选择"取消"走特殊退票（人工审核）。', '退票方式')
  try {
    if (voluntary) {
      const quote = await api('/api/refund_quote' + qs({ order_no: o.order_no }))
      const ok1 = await confirmDialog(`票面金额 ¥${quote.amount}，手续费 ¥${quote.fee}，预计到账 ¥${quote.predict_amount}`, '确认退票')
      if (!ok1) return
    } else {
      const ok2 = await confirmDialog('确认提交特殊退票？将进入人工审核队列。', '特殊退票')
      if (!ok2) return
    }
    const d = await api('/api/refund', { method: 'POST', body: {
      order_no: o.order_no, refund_type: voluntary ? 'voluntary' : 'special'
    }})
    toastSuccess(d.message || '退票已提交')
    await load()
  } catch (e) {
    toastError(e.message)
  }
}
</script>

<style scoped>
.order-summary { display: flex; gap: 30px; margin-bottom: 16px; font-size: 14px; }
.order-summary strong { color: var(--primary); font-size: 18px; }
.order-list { display: flex; flex-direction: column; gap: 12px; }
.order-card { padding: 14px 16px; }
.order-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.order-flight { font-weight: 700; }
.flight-no { color: var(--text-muted); font-weight: 400; margin-left: 6px; font-size: 13px; }
.order-meta { display: flex; flex-wrap: wrap; gap: 16px; color: var(--text-muted); font-size: 13px; margin-bottom: 12px; }
.checkin-tag { color: var(--warning); }
.order-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>