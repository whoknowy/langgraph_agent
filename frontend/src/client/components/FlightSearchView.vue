<template>
  <div>
    <h2 class="page-title">机票预订</h2>
    <div class="card search-card">
      <div class="form-row">
        <div class="form-field">
          <label>出发城市</label>
          <input v-model="form.departure" placeholder="北京" />
        </div>
        <div class="form-field">
          <label>到达城市</label>
          <input v-model="form.destination" placeholder="上海" />
        </div>
        <div class="form-field">
          <label>日期</label>
          <input v-model="form.date" type="date" />
        </div>
        <div class="form-field">
          <label>舱位</label>
          <select v-model="form.cabin">
            <option value="经济">经济舱</option>
            <option value="商务">商务舱</option>
          </select>
        </div>
        <button class="btn btn-primary" :disabled="loading" @click="search">{{ loading ? '搜索中…' : '搜索航班' }}</button>
      </div>
      <p v-if="error" class="error-text" style="margin-top:8px">{{ error }}</p>
    </div>

    <div class="flight-list">
      <div v-if="!flights.length && !loading" class="empty card">暂无航班，试试其他航线/日期</div>
      <div v-for="f in flights" :key="f.flight_no" class="card flight-card">
        <div class="flight-airline">{{ f.airline }} <span class="muted">{{ f.flight_no }}</span></div>
        <div class="flight-route">
          <div class="time">
            <div class="dep">{{ f.dep_time }}</div>
            <div class="city">{{ form.departure }}</div>
          </div>
          <div class="path">
            <div class="line"></div>
            <div class="plane">✈</div>
            <div class="duration">{{ duration(f.dep_time, f.arr_time) }}</div>
          </div>
          <div class="time">
            <div class="dep">{{ f.arr_time }}</div>
            <div class="city">{{ form.destination }}</div>
          </div>
        </div>
        <div class="flight-price">
          <div class="price">¥{{ (f.prices && f.prices[form.cabin]) || '--' }}</div>
          <div class="cabin">{{ form.cabin }}舱</div>
          <button class="btn btn-primary btn-sm" @click="openBooking(f)">订票</button>
        </div>
      </div>
    </div>

    <!-- 订单确认/支付 -->
    <div v-if="orderPanel.open" class="modal-mask" @click.self="orderPanel.open = false">
      <div class="modal-box order-box">
        <div class="modal-title">订单确认</div>
        <div v-if="orderPanel.loading" class="empty">加载报价中…</div>
        <template v-else>
          <div class="order-flight">
            <div class="order-route">{{ orderPanel.flight.airline }} {{ orderPanel.flight.flight_no }}</div>
            <div class="muted">{{ orderPanel.flight.date }} {{ orderPanel.flight.dep_time }} - {{ orderPanel.flight.arr_time }}</div>
          </div>
          <div class="order-prices">
            <div class="row"><span>舱位 / 人数</span><span>{{ orderPanel.cabin }} × {{ orderPanel.passengers }}人</span></div>
            <div class="row"><span>单价</span><span>¥{{ orderPanel.quote.unit_price }}</span></div>
            <div class="row total"><span>总价</span><span>¥{{ orderPanel.quote.total_amount }}</span></div>
          </div>
          <div class="order-passenger">
            <label>乘机人姓名</label>
            <input v-model="orderPanel.passengerName" placeholder="乘机人姓名" />
          </div>
          <div class="pay-methods">
            <label>支付方式</label>
            <div class="pay-methods-grid">
              <label class="pay-method" :class="{ active: orderPanel.payMethod === 'balance' }">
                <input type="radio" value="balance" v-model="orderPanel.payMethod" /> 会员余额
              </label>
              <label class="pay-method" :class="{ active: orderPanel.payMethod === 'card' }">
                <input type="radio" value="card" v-model="orderPanel.payMethod" /> 银行卡
              </label>
            </div>
          </div>
          <div v-if="orderPanel.error" class="error-text">{{ orderPanel.error }}</div>
          <div class="modal-actions">
            <button class="btn" @click="orderPanel.open = false">取消</button>
            <button class="btn btn-primary" :disabled="orderPanel.submitting" @click="confirmBook">
              {{ orderPanel.submitting ? '处理中…' : (orderPanel.created ? '去支付' : '确认下单') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api, qs } from '../../api.js'

const form = ref({ departure: '北京', destination: '上海', date: '', cabin: '经济' })
const flights = ref([])
const loading = ref(false)
const error = ref('')
const orderPanel = reactive({
  open: false, loading: false, submitting: false, error: '', flight: null,
  cabin: '经济', passengers: 1, quote: {}, created: false, orderNo: '', payMethod: 'balance', passengerName: ''
})

function tomorrow() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  return d.toISOString().slice(0, 10)
}

onMounted(() => {
  form.value.date = tomorrow()
  search()
})

async function search() {
  if (!form.value.departure || !form.value.destination || !form.value.date) {
    error.value = '请填写出发、到达和日期'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const d = await api('/api/flights/search' + qs({
      departure: form.value.departure,
      destination: form.value.destination,
      date: form.value.date
    }))
    flights.value = d.flights || []
    if (d.error) error.value = d.error
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function duration(dep, arr) {
  const [dh, dm] = dep.split(':').map(Number)
  const [ah, am] = arr.split(':').map(Number)
  let mins = ah * 60 + am - dh * 60 - dm
  if (mins < 0) mins += 24 * 60
  return Math.floor(mins / 60) + 'h' + (mins % 60)
}

async function openBooking(f) {
  orderPanel.open = true
  orderPanel.loading = true
  orderPanel.error = ''
  orderPanel.created = false
  orderPanel.flight = f
  orderPanel.cabin = form.value.cabin
  try {
    const d = await api('/api/booking_quote' + qs({
      flight_no: f.flight_no, flight_date: form.value.date,
      cabin: form.value.cabin, passengers: 1
    }))
    orderPanel.quote = d
  } catch (e) {
    orderPanel.error = e.message
  } finally {
    orderPanel.loading = false
  }
}

async function confirmBook() {
  const f = orderPanel.flight
  orderPanel.submitting = true
  orderPanel.error = ''
  try {
    if (!orderPanel.created) {
      const d = await api('/api/book', { method: 'POST', body: {
        flight_no: f.flight_no, flight_date: form.value.date,
        cabin: orderPanel.cabin, passengers: 1
      }})
      orderPanel.created = true
      orderPanel.orderNo = d.order_no
      alert(`订单 ${d.order_no} 已创建（待支付），金额 ¥${d.total_amount}`)
    } else {
      const d = await api('/api/pay', { method: 'POST', body: { order_no: orderPanel.orderNo } })
      alert(d.message || '支付成功，已出票')
      orderPanel.open = false
      // 刷新订单页数据
    }
  } catch (e) {
    orderPanel.error = e.message
  } finally {
    orderPanel.submitting = false
  }
}
</script>

<style scoped>
.search-card { margin-bottom: 16px; }
.flight-list { display: flex; flex-direction: column; gap: 12px; }
.flight-card { display: flex; align-items: center; gap: 20px; }
.flight-airline { width: 180px; font-weight: 600; }
.flight-route { flex: 1; display: flex; align-items: center; justify-content: center; gap: 30px; }
.time { text-align: center; }
.time .dep { font-size: 22px; font-weight: 700; }
.time .city { font-size: 12px; color: var(--text-muted); }
.path { display: flex; flex-direction: column; align-items: center; gap: 2px; width: 140px; }
.path .line { width: 100%; height: 1px; background: #cbd5e1; }
.path .plane { margin-top: -10px; color: var(--primary); }
.path .duration { font-size: 11px; color: var(--text-muted); }
.flight-price { text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.price { font-size: 20px; color: var(--danger); font-weight: 700; }
.cabin { font-size: 12px; color: var(--text-muted); }

.order-box { width: 460px; }
.order-flight { background: #f0f6ff; border-radius: 10px; padding: 12px; margin-bottom: 12px; }
.order-route { font-weight: 700; margin-bottom: 4px; }
.order-prices { margin-bottom: 14px; }
.order-prices .row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px dashed var(--border); font-size: 14px; }
.order-prices .row.total { font-weight: 700; font-size: 16px; color: var(--primary); }
.order-passenger { margin-bottom: 14px; }
.order-passenger label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
.order-passenger input { width: 100%; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; }
.pay-methods label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
.pay-methods-grid { display: flex; gap: 10px; }
.pay-method {
  flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center; font-size: 13px;
  color: var(--text); display: flex; align-items: center; justify-content: center; gap: 6px;
}
.pay-method.active { border-color: var(--primary); background: var(--primary-light); color: var(--primary); }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
</style>
