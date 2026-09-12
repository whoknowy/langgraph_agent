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
          可选 {{ displayStats.free }} / 共 {{ displayStats.total }}（{{ currentOrder.cabin }}舱）
        </div>
      </div>
      <div v-if="seatLoading" class="empty">座位图加载中…</div>
      <div v-else-if="seatError" class="error-text">{{ seatError }}</div>
      <template v-else-if="seatData && currentOrder">
        <div class="cabin-scroll">
          <div class="aircraft">
            <!-- 机头 -->
            <div class="nose">
              <svg viewBox="0 0 220 96" class="nose-svg" aria-hidden="true">
                <path d="M4 92 C 4 30, 62 4, 110 4 C 158 4, 216 30, 216 92 Z"
                      fill="var(--panel)" stroke="var(--border)" stroke-width="1.5"/>
                <path d="M40 92 C 46 46, 78 26, 110 26 C 142 26, 174 46, 180 92"
                      fill="none" stroke="#e8edf5" stroke-width="1"/>
                <text x="110" y="62" text-anchor="middle" class="nose-text">机头</text>
              </svg>
              <div class="wing wing-left"><span>机翼</span></div>
              <div class="wing wing-right"><span>机翼</span></div>
            </div>

            <div class="cabin-body">
              <div v-for="(rows, cabin) in cabinRows" :key="cabin">
                <div class="cabin-divider"><span>{{ cabin }}舱</span></div>
                <div v-for="row in rows" :key="row.row" class="seat-row">
                  <div class="seat-cluster">
                    <button
                      v-for="seat in row.left"
                      :key="seat.seat_no"
                      class="seat"
                      :class="{ occupied: seat.status === 'occupied', mine: seat.mine, selected: selectedSeat === seat.seat_no }"
                      :disabled="seat.status === 'occupied'"
                      :title="seat.seat_no"
                      @click="selectedSeat = seat.seat_no"
                    >{{ seatLetter(seat.seat_no) }}</button>
                  </div>
                  <div class="aisle"><span class="aisle-no">{{ row.row }}</span></div>
                  <div class="seat-cluster">
                    <button
                      v-for="seat in row.right"
                      :key="seat.seat_no"
                      class="seat"
                      :class="{ occupied: seat.status === 'occupied', mine: seat.mine, selected: selectedSeat === seat.seat_no }"
                      :disabled="seat.status === 'occupied'"
                      :title="seat.seat_no"
                      @click="selectedSeat = seat.seat_no"
                    >{{ seatLetter(seat.seat_no) }}</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- 机尾 -->
            <div class="tail">
              <svg viewBox="0 0 220 110" class="tail-svg" aria-hidden="true">
                <path d="M4 6 C 4 62, 62 106, 110 106 C 158 106, 216 62, 216 6 Z"
                      fill="var(--panel)" stroke="var(--border)" stroke-width="1.5"/>
                <path d="M46 6 C 52 48, 80 84, 110 84 C 140 84, 168 48, 174 6"
                      fill="none" stroke="#e8edf5" stroke-width="1"/>
                <text x="110" y="60" text-anchor="middle" class="nose-text">机尾</text>
              </svg>
              <div class="tailfin"><span>尾翼</span></div>
            </div>
          </div>
        </div>

        <div class="cabin-axis">
          <span v-for="(c, i) in cabinLetters" :key="i" class="axis-cell">{{ c }}</span>
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
import { useRoute } from 'vue-router'
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

// 座位号形如 31A → 字母 A；用于机舱内只显示列字母（排号在过道中间竖向标出）
function seatLetter(seatNo) {
  const m = String(seatNo || '').match(/([A-Za-z]+)$/)
  return m ? m[1].toUpperCase() : seatNo
}

// 把一排拆成「过道左 / 过道右」两簇。真实单通道客机的过道在
// 字母序列的中点：3-3（ABCDEF）分 ABC|DEF，2-2（ACDF）分 AC|DF。
// 判断依据是字母是否连续跳跃（A→C 有跳过 B），据此定位过道位置。
const aisleIndex = computed(() => {
  const cabins = filteredCabins.value
  for (const rows of Object.values(cabins)) {
    if (!rows.length) continue
    const letters = rows[0].seats.map(s => seatLetter(s.seat_no))
    for (let i = 1; i < letters.length; i++) {
      if (letters[i].charCodeAt(0) - letters[i - 1].charCodeAt(0) > 1) return i
    }
    return Math.ceil(letters.length / 2)
  }
  return 3
})

// 行数据：左侧簇 / 右侧簇 / 排号
const cabinRows = computed(() => {
  const out = {}
  const idx = aisleIndex.value
  Object.entries(filteredCabins.value).forEach(([cabin, rows]) => {
    out[cabin] = rows.map(row => ({
      row: row.row,
      left: row.seats.slice(0, idx),
      right: row.seats.slice(idx)
    }))
  })
  return out
})

// 顶部列字母轴（按左右簇拼出，含过道占位）
const cabinLetters = computed(() => {
  const idx = aisleIndex.value
  for (const rows of Object.values(filteredCabins.value)) {
    if (!rows.length) continue
    const letters = rows[0].seats.map(s => seatLetter(s.seat_no))
    return [...letters.slice(0, idx), '', ...letters.slice(idx)]
  }
  return []
})

const displayStats = computed(() => {
  const cabins = filteredCabins.value
  let free = 0
  let total = 0
  Object.values(cabins).forEach(rows => {
    rows.forEach(row => {
      row.seats.forEach(seat => {
        total += 1
        if (seat.status === 'free') free += 1
      })
    })
  })
  return { free, total }
})

const route = useRoute()

onMounted(async () => {
  try {
    const d = await api('/api/my/orders')
    orders.value = d.orders || []
    const routeNo = route.query.order_no
    const preferred = routeNo
      ? checkableOrders.value.find(o => o.order_no === routeNo)
      : null
    const target = preferred || checkableOrders.value[0]
    if (target) {
      selectedOrderNo.value = target.order_no
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
      order_no: selectedOrderNo.value, seat_no: selectedSeat.value,
      confirm_token: (seatData.value || {}).confirm_token
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

/* ---- 飞机机身 ---- */
.cabin-scroll { overflow-x: auto; padding: 4px 0 8px; }
.aircraft {
  width: max-content; min-width: 300px; margin: 0 auto;
  display: flex; flex-direction: column; align-items: center;
}
.nose-svg, .tail-svg { display: block; width: 220px; height: auto; }
.nose-text {
  font-size: 13px; fill: var(--text-faint); font-family: inherit;
}
.nose { position: relative; }
.tail { position: relative; }
/* 机翼：从机身两侧伸出，纯装饰，标明"这是飞机" */
.wing {
  position: absolute; top: 54%; width: 92px; height: 30px;
  background: #eef2f8; border: 1.5px solid var(--border);
  display: flex; align-items: center; justify-content: center;
}
.wing span { font-size: 11px; color: var(--text-faint); }
.wing-left {
  right: 100%; margin-right: -6px;
  border-radius: 8px 0 0 8px;
  clip-path: polygon(0 22%, 100% 0, 100% 100%, 0 78%);
}
.wing-right {
  left: 100%; margin-left: -6px;
  border-radius: 0 8px 8px 0;
  clip-path: polygon(0 0, 100% 22%, 100% 78%, 0 100%);
}
.tailfin {
  position: absolute; left: 50%; top: 6px; transform: translateX(-50%);
  width: 34px; height: 46px; background: #eef2f8;
  border: 1.5px solid var(--border); border-radius: 6px 6px 0 0;
  display: flex; align-items: center; justify-content: center;
}
.tailfin span { font-size: 10px; color: var(--text-faint); }

/* 客舱：机身中段，左右两条竖边模拟舱壁 */
.cabin-body {
  width: max-content;
  padding: 10px 26px;
  border-left: 2px solid var(--border);
  border-right: 2px solid var(--border);
  background: linear-gradient(90deg, #fbfcfe 0, #fff 18%, #fff 82%, #fbfcfe 100%);
}

.cabin-divider {
  display: flex; align-items: center; gap: 10px;
  margin: 10px 0 8px; font-size: 12px; color: var(--text-faint);
}
.cabin-divider::before, .cabin-divider::after {
  content: ''; flex: 1; height: 1px; background: var(--border);
}

.seat-row { display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
.row-no { width: 0; }
.seat-cluster { display: flex; gap: 4px; }
/* 过道：比座椅间距宽，中间竖排标排号 */
.aisle {
  width: 40px; display: flex; align-items: center; justify-content: center;
}
.aisle-no { font-size: 11px; color: var(--text-faint); }

.seat {
  width: 34px; height: 30px; border-radius: 6px;
  border: 1px solid var(--border); background: #fff;
  font-size: 11px; color: #333; cursor: pointer;
  transition: background .12s, border-color .12s;
}
.seat:hover:not(:disabled) { border-color: var(--primary); }
.seat.occupied { background: #e5e7eb; color: #9ca3af; cursor: not-allowed; border-color: #e5e7eb; }
.seat.mine { background: #fef3c7; border-color: #f59e0b; color: #92400e; }
.seat.selected { background: var(--primary); color: #fff; border-color: var(--primary); }

/* 顶部列字母轴：与座椅列对齐 */
.cabin-axis {
  display: flex; justify-content: center; align-items: center;
  gap: 4px; margin: 14px 0 2px; padding: 0 26px;
}
.axis-cell {
  width: 34px; text-align: center;
  font-size: 11px; color: var(--text-faint);
}
.axis-cell:empty { width: 40px; }

.seat-legend { display: flex; gap: 16px; justify-content: center; font-size: 12px; color: var(--text-muted); margin: 14px 0; }
.seat-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 4px; vertical-align: -1px; }
.seat-legend .free { background: #fff; border: 1px solid var(--border); }
.seat-legend .mine { background: #fef3c7; border: 1px solid #f59e0b; }
.seat-legend .occupied { background: #e5e7eb; }
.seat-legend .selected { background: var(--primary); }
.seat-actions { display: flex; justify-content: flex-end; align-items: center; gap: 14px; }
.boardpass-result { margin-top: 16px; background: #f0f9ff; border-color: #bae6fd; }
.boardpass-result p { margin: 6px 0; }
</style>
