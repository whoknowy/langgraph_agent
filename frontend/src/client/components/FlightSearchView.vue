<template>
  <div>
    <h2 class="page-title">机票预订</h2>
    <el-card shadow="never" class="search-card">
      <el-form inline @submit.prevent>
        <el-form-item label="出发城市">
          <el-input v-model="form.departure" placeholder="北京" style="width: 140px" />
        </el-form-item>
        <el-form-item label="到达城市">
          <el-input v-model="form.destination" placeholder="上海" style="width: 140px" />
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" style="width: 160px" />
        </el-form-item>
        <el-form-item label="舱位">
          <el-select v-model="form.cabin" style="width: 120px">
            <el-option value="经济" label="经济舱" />
            <el-option value="商务" label="商务舱" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="search">{{ loading ? '搜索中…' : '搜索航班' }}</el-button>
        </el-form-item>
      </el-form>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </el-card>

    <div class="flight-list">
      <el-empty v-if="!flights.length && !loading" description="暂无航班，试试其他航线/日期" />
      <el-card v-for="f in flights" :key="f.flight_no" shadow="hover" class="flight-card">
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
          <el-button type="primary" size="small" @click="openBooking(f)">订票</el-button>
        </div>
      </el-card>
    </div>

    <el-dialog v-model="orderPanel.open" title="订单确认" width="480px">
      <div v-if="orderPanel.loading" class="empty">加载报价中…</div>
      <template v-else>
        <div class="order-flight">
          <div class="order-route">{{ orderPanel.flight.airline }} {{ orderPanel.flight.flight_no }}</div>
          <div class="muted">{{ orderPanel.flight.date }} {{ orderPanel.flight.dep_time }} - {{ orderPanel.flight.arr_time }}</div>
        </div>
        <el-descriptions :column="1" border style="margin: 12px 0">
          <el-descriptions-item label="舱位 / 人数">{{ orderPanel.cabin }} × {{ orderPanel.passengers }}人</el-descriptions-item>
          <el-descriptions-item label="单价">¥{{ orderPanel.quote.unit_price }}</el-descriptions-item>
          <el-descriptions-item label="总价"><strong style="color:var(--primary)">¥{{ orderPanel.quote.total_amount }}</strong></el-descriptions-item>
        </el-descriptions>
        <div class="order-passenger">
          <label>乘机人姓名</label>
          <el-input v-model="orderPanel.passengerName" placeholder="乘机人姓名" />
        </div>
        <div class="pay-methods">
          <label>支付方式</label>
          <el-radio-group v-model="orderPanel.payMethod">
            <el-radio value="balance">会员余额</el-radio>
            <el-radio value="card">银行卡</el-radio>
          </el-radio-group>
        </div>
        <el-alert v-if="orderPanel.error" :title="orderPanel.error" type="error" :closable="false" style="margin-top:12px" />
      </template>
      <template #footer>
        <el-button :disabled="orderPanel.submitting" @click="closePanel">取消</el-button>
        <el-button type="primary" :loading="orderPanel.submitting" @click="onSubmitClick">
          {{ orderPanel.submitting ? '处理中…' : (orderPanel.created ? '去支付' : '确认下单') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api, qs } from '../../api.js'
import { toastSuccess, toastError } from '../../ui.js'
import { usePay, openPayWindow } from '../composables/usePay.js'

const form = ref({ departure: '北京', destination: '上海', date: '', cabin: '经济' })
const flights = ref([])
const loading = ref(false)
const error = ref('')
const orderPanel = reactive({
  open: false, loading: false, submitting: false, error: '', flight: null,
  cabin: '经济', passengers: 1, quote: {}, created: false, orderNo: '', payMethod: 'balance', passengerName: '',
  payWin: null   // 预开的支付窗口，见 onSubmitClick
})

// 支付成功后刷新航班列表（该舱位余票可能变化）
const { pay } = usePay({
  onPaid: async () => {
    orderPanel.open = false
    await search()
  },
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

// 点击入口：必须在**同步阶段**预开支付窗口。
// 若等到 await 之后再 window.open，已脱离用户手势上下文，浏览器会当弹窗拦截。
function onSubmitClick() {
  if (orderPanel.created && orderPanel.submitting) return   // 防连点
  orderPanel.payWin = orderPanel.created ? openPayWindow() : null
  confirmBook()
}

function closePanel() {
  if (orderPanel.payWin) { orderPanel.payWin.close(); orderPanel.payWin = null }
  orderPanel.open = false
}

async function confirmBook() {
  const f = orderPanel.flight
  orderPanel.submitting = true
  orderPanel.error = ''
  try {
    if (!orderPanel.created) {
      // 第一步：创建订单（携带报价接口签发的一次性确认凭证，服务端强制 HITL）
      const d = await api('/api/book', { method: 'POST', body: {
        flight_no: f.flight_no, flight_date: form.value.date,
        cabin: orderPanel.cabin, passengers: 1,
        confirm_token: (orderPanel.quote || {}).confirm_token
      }})
      orderPanel.created = true
      orderPanel.orderNo = d.order_no
      toastSuccess(`订单 ${d.order_no} 已创建（待支付），金额 ¥${d.total_amount}`)
    } else {
      // 第二步：真实支付（走渠道抽象，可能是跳转收银台或站内确认）
      const ok = await pay(orderPanel.orderNo, { win: orderPanel.payWin })
      orderPanel.payWin = null
      if (ok) orderPanel.open = false
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
.flight-card :deep(.el-card__body) { display: flex; align-items: center; gap: 20px; padding: 16px 20px; }
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
.order-flight { background: #f0f6ff; border-radius: 10px; padding: 12px; margin-bottom: 12px; }
.order-route { font-weight: 700; margin-bottom: 4px; }
.order-passenger { margin: 12px 0; }
.order-passenger label, .pay-methods label { display: block; font-size: 12.5px; color: var(--text-muted); margin-bottom: 5px; }
</style>
