<template>
  <div>
    <h2 class="page-title">值机选座</h2>
    <div class="card" style="margin-bottom:16px">
      <div class="section-title">选择订单</div>
      <select v-model="selectedOrderNo" class="order-select" @change="loadSeatMap">
        <option value="">请选择可值机订单</option>
        <option v-for="o in checkableOrders" :key="o.order_no" :value="o.order_no">
          {{ o.flight }} · {{ o.flight_date }} {{ o.dep_time }} · {{ o.status }}{{ o.checked_in ? ' · 已值机 ' + o.checkin_seat : '' }}
        </option>
      </select>
    </div>

    <div class="card seat-card">
      <div class="seat-head">
        <div>
          <div class="section-title">{{ currentOrder ? currentOrder.flight : '座位图' }}</div>
          <div class="muted" v-if="currentOrder">{{ currentOrder.flight_date }} {{ currentOrder.dep_time }} · {{ currentOrder.cabin }}舱</div>
        </div>
        <div class="seat-summary" v-if="seatData">
          可选 {{ seatData.free }} / 共 {{ seatData.total }}
        </div>
      </div>
      <div v-if="seatLoading" class="empty">座位图加载中…</div>
      <div v-else-if="seatError" class="error-text">{{ seatError }}</div>
      <template v-else-if="seatData && currentOrder">
        <div v-for="(rows, cabin) in filteredCabins" :key="cabin" class="cabin-block">
          <div class="cabin-label">{{ cabin }}舱</div>
          <div v-for="row in rows" :key="row.row" class="seat-row">
            <span class="row-no">{{ row.row }}</span>
            <button
              v-for="seat in row.seats"
              :key="seat.seat_no"
              class="seat"
              :class="{ occupied: seat.status === 'occupied', mine: seat.mine, selected: selectedSeat === seat.seat_no }"
              :disabled="seat.status === 'occupied'"
              @click="selectedSeat = seat.seat_no"
            >{{ seat.seat_no }}</button>
          </div>
        </div>
        <div class="seat-legend">
          <span><i class="free"></i>可选</span>
          <span><i class="mine"></i>当前座位</span>
          <span><i class="occupied"></i>已占</span>
          <span><i class="selected"></i>已选</span>
        </div>
        <div class="seat-actions">
          <div class="muted" v-if="selectedSeat">已选座位：{{ selectedSeat }}</div>
          <button class="btn btn-primary" :disabled="!selectedSeat || submitting" @click="confirmCheckin">
            {{ submitting ? '提交中…' : (currentOrder && currentOrder.checked_in ? '确认改座' : '确认值机') }}
          </button>
        </div>
      </template>
    </div>

    <div v-if="boardPass" class="card boardpass-result">
      <h3>🎫 值机成功，登机牌已生成</h3>
      <p>{{ boardPass.airline }} {{ boardPass.flight_no }} · {{ boardPass.route }}</p>
      <p>座位 {{ boardPass.seat_no }} · 登机口 {{ boardPass.gate }} / {{ boardPass.boarding_time }}</p>
      <router-link to="/boardpass" class="btn btn-primary">查看电子登机牌</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, qs } from '../../api.js'

const orders = ref([])
const selectedOrderNo = ref('')
const seatData = ref(null)
const seatLoading = ref(false)
const seatError = ref('')
const selectedSeat = ref('')
const submitting = ref(false)
const boardPass = ref(null)

const checkableOrders = computed(() =>
  orders.value.filter(o => ['已出票', '已改签'].includes(o.status))
)

const currentOrder = computed(() => orders.value.find(o => o.order_no === selectedOrderNo.value) || null)

const filteredCabins = computed(() => {
  if (!seatData.value || !currentOrder.value) return {}
  const cabins = seatData.value.cabins || {}
  // 按订单舱位优先展示；如果没有对应舱位则展示全部
  if (cabins[currentOrder.value.cabin]) {
    return { [currentOrder.value.cabin]: cabins[currentOrder.value.cabin] }
  }
  return cabins
})

onMounted(async () => {
  try {
    const d = await api('/api/my/orders')
    orders.value = d.orders || []
    if (checkableOrders.value.length) {
      selectedOrderNo.value = checkableOrders.value[0].order_no
      await loadSeatMap()
    }
  } catch (e) {
    seatError.value = e.message
  }
})

async function loadSeatMap() {
  selectedSeat.value = ''
  seatData.value = null
  boardPass.value = null
  const o = currentOrder.value
  if (!o) return
  seatLoading.value = true
  seatError.value = ''
  try {
    const d = await api('/api/checkin/seats' + qs({
      flight_no: o.flight_no, flight_date: o.flight_date, order_no: o.order_no
    }))
    seatData.value = d
    if (o.checked_in && o.checkin_seat) selectedSeat.value = o.checkin_seat
  } catch (e) {
    seatError.value = e.message
  } finally {
    seatLoading.value = false
  }
}

async function confirmCheckin() {
  if (!selectedOrderNo.value || !selectedSeat.value) return
  submitting.value = true
  try {
    const d = await api('/api/checkin', { method: 'POST', body: {
      order_no: selectedOrderNo.value, seat_no: selectedSeat.value
    }})
    boardPass.value = d
    await loadSeatMap()
    // refresh orders
    const od = await api('/api/my/orders')
    orders.value = od.orders || []
  } catch (e) {
    seatError.value = e.message
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.order-select { width: 100%; max-width: 560px; padding: 9px 10px; border: 1px solid var(--border); border-radius: 9px; }
.seat-card { min-height: 280px; }
.seat-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.seat-summary { font-size: 13px; color: var(--text-muted); }
.cabin-block { margin-bottom: 14px; }
.cabin-label { font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.seat-row { display: flex; gap: 4px; margin-bottom: 4px; align-items: center; }
.row-no { width: 26px; font-size: 12px; color: var(--text-muted); }
.seat {
  width: 38px; height: 32px; border-radius: 6px; border: 1px solid var(--border); background: #fff; font-size: 11px; color: #333;
}
.seat.occupied { background: #e5e7eb; color: #9ca3af; cursor: not-allowed; }
.seat.mine { background: #fef3c7; border-color: #f59e0b; }
.seat.selected { background: var(--primary); color: #fff; border-color: var(--primary); }
.seat-legend { display: flex; gap: 16px; font-size: 12px; color: var(--text-muted); margin: 12px 0; }
.seat-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 4px; vertical-align: -1px; }
.seat-legend .free { background: #fff; border: 1px solid var(--border); }
.seat-legend .mine { background: #fef3c7; border: 1px solid #f59e0b; }
.seat-legend .occupied { background: #e5e7eb; }
.seat-legend .selected { background: var(--primary); }
.seat-actions { display: flex; justify-content: flex-end; align-items: center; gap: 14px; }
.boardpass-result { margin-top: 16px; background: #f0f9ff; border-color: #bae6fd; }
.boardpass-result p { margin: 6px 0; }
</style>
