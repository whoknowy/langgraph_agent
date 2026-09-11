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
            <button class="btn btn-primary btn-sm" :disabled="payingOrder === o.order_no"
                    @click="pay(o)">
              {{ payingOrder === o.order_no ? '等待支付…' : '去支付' }}
            </button>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
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

const payingOrder = ref('')
let pollTimer = null

async function pay(o) {
  // 同步阶段就把窗口打开：await 之后再 window.open 会失去用户手势上下文被浏览器拦截
  const win = window.open('', '_blank')
  try {
    const d = await api('/api/pay/create', { method: 'POST', body: { order_no: o.order_no } })
    if (d.mode === 'redirect' && d.pay_url) {
      if (win) win.location.href = d.pay_url
      else window.location.href = d.pay_url
      await waitPaid(o.order_no)
    } else {
      if (win) win.close()
      const r = await api('/api/pay/confirm', { method: 'POST', body: { pay_no: d.pay_no } })
      toastSuccess(r.message || '支付成功')
      await load()
    }
  } catch (e) {
    if (win) win.close()
    stopPoll()
    toastError(e.message)
  }
}

// 轮询等待支付结果：异步通知可能比页面跳回慢，本地收不到通知时也是靠它兜底
function waitPaid(orderNo) {
  return new Promise((resolve) => {
    payingOrder.value = orderNo
    let ticks = 0
    const maxTicks = 90          // 2 秒一拍，约 3 分钟
    stopPollTimerOnly()
    pollTimer = setInterval(async () => {
      ticks += 1
      try {
        const s = await api('/api/pay/status' + qs({ order_no: orderNo }))
        if (s.paid) {
          stopPoll()
          toastSuccess('支付成功，订单已出票')
          await load()
          resolve()
          return
        }
      } catch (e) {
        // 单次轮询失败不打断，继续等下一拍
      }
      if (ticks >= maxTicks) {
        stopPoll()
        toastError('等待支付超时，请刷新订单列表确认结果')
        resolve()
      }
    }, 2000)
  })
}

function stopPollTimerOnly() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function stopPoll() {
  stopPollTimerOnly()
  payingOrder.value = ''
}

onUnmounted(stopPoll)

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