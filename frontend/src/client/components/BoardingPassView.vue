<template>
  <div>
    <h2 class="page-title">电子登机牌</h2>
    <div class="card" style="margin-bottom:16px">
      <div class="section-title">选择已值机订单</div>
      <select v-model="selectedOrderNo" class="order-select" @change="loadBoardPass">
        <option value="">请选择已值机订单</option>
        <option v-for="o in boardedOrders" :key="o.order_no" :value="o.order_no">
          {{ o.flight }} · {{ o.flight_date }} {{ o.dep_time }} · 座位 {{ o.checkin_seat }}
        </option>
      </select>
    </div>

    <div v-if="loading" class="empty card">加载中…</div>
    <div v-else-if="error" class="card error-text">{{ error }}</div>

    <div v-else-if="bp" class="boarding-pass">
      <div class="bp-top">
        <div class="bp-airline">
          <div class="airline-logo">✈</div>
          <div>
            <div class="airline-name">{{ bp.airline }}</div>
            <div class="flight-name">{{ bp.flight_no }}</div>
          </div>
        </div>
        <div class="bp-route">
          <div class="bp-city">
            <div class="city-code">{{ bp.route ? bp.route.split(' ')[0] : '' }}</div>
            <div class="city-name">{{ bp.route ? bp.route.replace(' → ', ' 至 ') : '' }}</div>
          </div>
          <div class="bp-arrow">✈</div>
          <div class="bp-city">
            <div class="city-code">{{ bp.route ? bp.route.split(' ')[2] : '' }}</div>
          </div>
        </div>
      </div>
      <div class="bp-body">
        <div class="bp-info">
          <div class="bp-field"><span>乘机人</span><strong>{{ bp.passenger }}</strong></div>
          <div class="bp-field"><span>日期</span><strong>{{ bp.flight_date }}</strong></div>
          <div class="bp-field"><span>起飞</span><strong>{{ bp.dep_time }}</strong></div>
          <div class="bp-field"><span>登机口</span><strong class="highlight">{{ bp.gate }}</strong></div>
          <div class="bp-field"><span>座位</span><strong class="highlight">{{ bp.seat_no }}</strong></div>
          <div class="bp-field"><span>登机时间</span><strong>{{ bp.boarding_time }}</strong></div>
        </div>
        <div class="bp-qr">
          <div class="qr-grid">
            <div v-for="n in 100" :key="n" :class="{ dark: (n * 7) % 3 === 0 || (n * 13) % 5 === 0 }"></div>
          </div>
          <div class="muted">扫码登机</div>
        </div>
      </div>
      <div class="bp-bottom">
        <span>祝您旅途愉快 ✈</span>
        <span class="bp-note">请于起飞前 45 分钟到达登机口</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api, qs } from '@/shared/api.js'

const orders = ref([])
const selectedOrderNo = ref('')
const bp = ref(null)
const loading = ref(false)
const error = ref('')

const boardedOrders = computed(() => orders.value.filter(o => o.checked_in && o.checkin_seat))

const route = useRoute()

onMounted(async () => {
  try {
    const d = await api('/api/my/orders')
    orders.value = d.orders || []
    const routeNo = route.query.order_no
    const preferred = routeNo
      ? boardedOrders.value.find(o => o.order_no === routeNo)
      : null
    const target = preferred || boardedOrders.value[0]
    if (target) {
      selectedOrderNo.value = target.order_no
      await loadBoardPass()
    } else {
      error.value = '暂无已值机订单，请先值机选座'
    }
  } catch (e) {
    error.value = e.message
  }
})

async function loadBoardPass() {
  if (!selectedOrderNo.value) return
  loading.value = true
  error.value = ''
  try {
    const d = await api('/api/checkin/boardpass' + qs({ order_no: selectedOrderNo.value }))
    bp.value = d
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.order-select {
  width: 100%; max-width: 560px; padding: 10px 12px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: #fff;
  outline: none; transition: border-color .15s, box-shadow .15s;
}
.order-select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }

.boarding-pass {
  max-width: 760px; margin: 0 auto;
  background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 55%, #38bdf8 130%);
  border-radius: 22px; color: #fff; overflow: hidden;
  box-shadow: 0 20px 48px -12px rgba(29, 78, 216, .45);
}
.bp-top { padding: 24px 26px 14px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.bp-airline { display: flex; gap: 12px; align-items: center; }
.airline-logo {
  width: 44px; height: 44px; background: rgba(255, 255, 255, .18); border-radius: 13px;
  display: flex; align-items: center; justify-content: center; font-size: 22px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.25);
}
.airline-name { font-weight: 700; font-size: 16px; }
.flight-name { font-size: 13px; opacity: .85; }
.bp-route { display: flex; align-items: center; gap: 14px; }
.bp-city { text-align: center; }
.city-code { font-size: 26px; font-weight: 800; letter-spacing: -.02em; }
.city-name { font-size: 11px; opacity: .8; }
.bp-arrow { font-size: 20px; opacity: .8; }
.bp-body {
  background: #fff; color: var(--text); margin: 0 14px; border-radius: 14px; padding: 22px;
  display: flex; gap: 20px; align-items: center;
}
.bp-info { flex: 1; display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.bp-field { display: flex; flex-direction: column; gap: 3px; }
.bp-field span { font-size: 11px; color: var(--text-muted); }
.bp-field strong { font-size: 15px; }
.highlight { color: var(--primary); font-size: 18px; }
.bp-qr { text-align: center; }
.qr-grid {
  width: 100px; height: 100px; background: #fff; border: 1px solid #e2e8f0; border-radius: 10px;
  display: grid; grid-template-columns: repeat(10, 1fr); gap: 2px; padding: 6px; margin: 0 auto 6px;
}
.qr-grid div { background: #fff; }
.qr-grid div.dark { background: #1e293b; }
.bp-bottom { padding: 18px 26px; display: flex; justify-content: space-between; font-size: 13px; flex-wrap: wrap; gap: 6px; }
.bp-note { opacity: .85; }

@media (max-width: 640px) {
  .bp-body { flex-direction: column; }
  .bp-info { grid-template-columns: repeat(2, 1fr); width: 100%; }
}
</style>
