<template>
  <div>
    <h2 class="page-title">我的订单</h2>
    <div class="order-summary card" v-if="summary.count">
      <div class="summary-item">
        <span class="summary-num">{{ summary.count }}</span>
        <span class="summary-label">全部订单</span>
      </div>
      <div class="summary-divider"></div>
      <div class="summary-item">
        <span class="summary-num">¥{{ summary.total_amount }}</span>
        <span class="summary-label">累计金额</span>
      </div>
    </div>

    <div class="order-list">
      <div v-for="o in orders" :key="o.order_no" class="card order-card">
        <div class="order-head">
          <div class="order-flight">{{ o.flight }} <span class="flight-no">{{ o.flight_no }}</span></div>
          <span class="badge" :class="statusClass(o.status)">{{ o.status }}</span>
        </div>
        <div class="order-meta">
          <span>🛫 {{ o.route }}</span>
          <span>📅 {{ o.flight_date }} {{ o.dep_time }}</span>
          <span>{{ o.cabin }}舱 · 金额 ¥{{ o.amount }}</span>
          <span v-if="o.checked_in" class="checkin-tag">已值机 {{ o.checkin_seat }}</span>
          <span v-if="o.change_pending" class="change-tag">
            待补差价 ¥{{ o.change_diff }}（改签至 {{ o.change_target }}，付款后才生效）
          </span>
        </div>
        <div class="order-actions">
          <template v-if="o.change_pending">
            <button class="btn btn-primary btn-sm" :disabled="payingOrder === o.order_no"
                    @click="onResumeChangePay(o)">
              {{ payingOrder === o.order_no ? '等待支付…' : `继续支付差价 ¥${o.change_diff}` }}
            </button>
          </template>
          <template v-if="o.status === '待支付'">
            <button class="btn btn-primary btn-sm" :disabled="payingOrder === o.order_no"
                    @click="onPayClick(o)">
              {{ payingOrder === o.order_no ? '等待支付…' : '去支付' }}
            </button>
          </template>
          <template v-if="['已出票','已改签'].includes(o.status)">
            <button v-if="!o.checked_in" class="btn btn-sm" @click="goCheckin(o)">值机选座</button>
            <button v-else class="btn btn-sm" @click="goBoardpass(o)">登机牌</button>
            <button class="btn btn-sm" @click="openChange(o)">改签</button>
            <button class="btn btn-danger btn-sm" @click="refund(o)">退票</button>
          </template>
          <template v-if="o.status === '退票中'">
            <span class="muted">人工审核中</span>
          </template>
        </div>
      </div>
      <div v-if="!orders.length" class="empty card">暂无订单</div>
    </div>

    <!-- 改签：日期用选择器、航班从同航线在售列表里挑，差价走支付宝（多退少补） -->
    <el-dialog v-model="chg.open" title="改签" width="560px" :close-on-click-modal="false"
               @closed="resetChange">
      <div v-if="chg.order" class="chg-current">
        原航班：<strong>{{ chg.order.flight }} {{ chg.order.flight_no }}</strong>
        <span class="muted"> · {{ chg.order.flight_date }} {{ chg.order.dep_time }} ·
          {{ chg.order.cabin }}舱 · ¥{{ chg.order.amount }}</span>
      </div>

      <el-form label-width="74px" class="chg-form">
        <el-form-item label="新日期">
          <el-date-picker v-model="chg.date" type="date" value-format="YYYY-MM-DD"
                          placeholder="选择日期" style="width: 160px" :clearable="false"
                          :disabled-date="disabledChangeDate" @change="onDateChange" />
          <span class="muted chg-hint">只能改到明天及以后</span>
        </el-form-item>
        <el-form-item label="新舱位">
          <el-select v-model="chg.cabin" style="width: 120px" @change="onCabinChange">
            <el-option value="经济" label="经济舱" />
            <el-option value="商务" label="商务舱" />
          </el-select>
        </el-form-item>
        <el-form-item label="新航班">
          <div v-loading="chg.loading" class="chg-flights">
            <div v-if="chg.loading" class="muted chg-loading">正在查询该日期可改签的航班…</div>
            <template v-else>
              <div v-if="!chg.flights.length" class="muted">该日期没有 {{ chg.cabin }}舱可改签的航班，换个日期看看</div>
              <button v-for="f in chg.flights" :key="f.flight_no" type="button"
                      class="chg-flight" :class="{ active: f.flight_no === chg.flight_no }"
                      @click="pickFlight(f)">
                <span class="chg-no">{{ f.airline }} {{ f.flight_no }}</span>
                <span class="chg-time">{{ f.dep_time }} → {{ f.arr_time }}</span>
                <span class="chg-price">¥{{ f.prices[chg.cabin] }}</span>
              </button>
            </template>
          </div>
        </el-form-item>
      </el-form>

      <div v-if="chg.quote" class="chg-quote" :class="diffClass">
        <div class="chg-quote-main">{{ chg.quote.diff_desc }}</div>
        <div class="muted">
          {{ chg.quote.new.flight_no }} · ¥{{ chg.quote.new.amount }}
          <template v-if="chg.quote.fare_diff > 0">（去支付宝补齐差价后改签自动生效）</template>
          <template v-else-if="chg.quote.fare_diff < 0">（差价将原路退回支付宝）</template>
        </div>
      </div>
      <div v-if="!chg.quote && chg.flight_no && !chg.loading && !chg.error" class="muted chg-quote">
        点“查询报价”查看差价
      </div>
      <div v-if="chg.error" class="chg-error">{{ chg.error }}</div>

      <template #footer>
        <button class="btn btn-sm" :disabled="chg.submitting" @click="chg.open = false">取消</button>
        <button class="btn btn-sm" :disabled="!chg.flight_no || chg.loading || chg.quoting || chg.submitting"
                @click="loadQuote">{{ chg.quoting ? '查询中…' : (chg.quote ? '重新报价' : '查询报价') }}</button>
        <button class="btn btn-primary btn-sm" :disabled="!canSubmit" @click="onChangeClick">
          {{ chg.submitting ? '处理中…' : submitText }}
        </button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, qs, newRequestId } from '@/shared/api.js'
import { toastError, toastSuccess, confirmDialog } from '@/shared/ui.js'
import { tomorrow } from '@/shared/format.js'
import { usePay, openPayWindow } from '../composables/usePay.js'

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

// 支付流程统一走 usePay（与「机票预订」「聊天订票」共用同一实现）
const { payingOrder, pay } = usePay({
  onPaid: load,
})

function onPayClick(o) {
  // 同步阶段预开窗口，避免 await 之后被浏览器当弹窗拦截
  pay(o.order_no, { win: openPayWindow() })
}

// 改签差价中途放弃后继续支付：订单状态还是「已出票」，没有别的入口能回到收银台
function onResumeChangePay(o) {
  pay(o.order_no, { win: openPayWindow(), paidText: '差价支付成功，改签已生效' })
}

function goCheckin(o) {
  router.push({ path: '/checkin', query: { order_no: o.order_no } })
}
function goBoardpass(o) {
  router.push({ path: '/boardpass', query: { order_no: o.order_no } })
}

// ---------------- 改签面板状态 ----------------
const chg = reactive({
  open: false, order: null, date: '', cabin: '经济',
  raw: [], flights: [], flight_no: '',
  quote: null, error: '', loading: false, quoting: false, submitting: false,
  requestId: '', payWin: null
})

// 改签日期必须**晚于今天**（后端 change_quote 会拒今天及以前），所以今天也要禁用
function disabledChangeDate(d) {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  start.setDate(start.getDate() + 1)
  return d.getTime() < start.getTime()
}

function filterFlights() {
  chg.flights = (chg.raw || []).filter(f => f.prices && f.prices[chg.cabin])
}

function resetChange() {
  if (chg.payWin) { try { chg.payWin.close() } catch (e) { /* 忽略 */ } }
  Object.assign(chg, {
    order: null, date: '', cabin: '经济', raw: [], flights: [], flight_no: '',
    quote: null, error: '', loading: false, quoting: false, submitting: false,
    requestId: '', payWin: null
  })
}

async function openChange(o) {
  resetChange()
  chg.order = o
  chg.cabin = o.cabin === '商务' ? '商务' : '经济'
  chg.date = tomorrow()
  chg.requestId = newRequestId('change')
  chg.open = true
  await loadChangeFlights()
}

async function loadChangeFlights() {
  const [dep, arr] = String((chg.order && chg.order.route) || '').split('-')
  chg.flight_no = ''
  chg.quote = null
  chg.error = ''
  if (!dep || !arr || !chg.date) { chg.raw = []; filterFlights(); return }
  chg.loading = true
  try {
    // 复用机票预订页的搜索接口：同航线、指定日期的真实在售航班（只返回未起飞的）
    const d = await api('/api/flights/search' + qs({
      departure: dep, destination: arr, date: chg.date
    }))
    chg.raw = d.flights || []
    if (d.error) chg.error = d.error
  } catch (e) {
    chg.raw = []
    chg.error = e.message
  } finally {
    chg.loading = false
    filterFlights()
  }
}

function onDateChange() {
  chg.requestId = newRequestId('change')   // 目标变了＝另一笔改签意图，换幂等键
  loadChangeFlights()
}

function onCabinChange() {
  chg.requestId = newRequestId('change')
  chg.quote = null
  filterFlights()
  if (chg.flight_no && !chg.flights.some(f => f.flight_no === chg.flight_no)) chg.flight_no = ''
}

function pickFlight(f) {
  chg.flight_no = f.flight_no
  chg.quote = null
  chg.error = ''
  chg.requestId = newRequestId('change')
  loadQuote()          // 选完直接报价，用户马上看到要补还是要退
}

async function loadQuote() {
  if (!chg.flight_no || !chg.date) return
  chg.quoting = true
  chg.error = ''
  try {
    // 报价接口同时签发一次性确认凭证（HITL：不带它 /api/change 会被拒）
    const d = await api('/api/change_quote' + qs({
      order_no: chg.order.order_no, new_flight_no: chg.flight_no,
      new_date: chg.date, new_cabin: chg.cabin
    }))
    if (d.error) { chg.quote = null; chg.error = d.error } else { chg.quote = d }
  } catch (e) {
    chg.quote = null
    chg.error = e.message
  } finally {
    chg.quoting = false
  }
}

const canSubmit = computed(() =>
  !!chg.quote && !chg.quote.error && !chg.submitting && !chg.quoting && !chg.loading)

const submitText = computed(() => {
  const q = chg.quote
  if (!q) return '确认改签'
  if (q.fare_diff > 0) return `去支付差价 ¥${q.fare_diff}`
  if (q.fare_diff < 0) return `确认改签（退 ¥${-q.fare_diff}）`
  return '确认改签'
})

const diffClass = computed(() => {
  const q = chg.quote
  if (!q) return ''
  if (q.fare_diff > 0) return 'chg-need-pay'
  if (q.fare_diff < 0) return 'chg-refund'
  return ''
})

// 点击入口：必须在**同步阶段**预开支付窗口。补差价要跳支付宝收银台，
// 等 await 之后再 window.open 会脱离用户手势上下文，被浏览器当弹窗拦掉。
function onChangeClick() {
  if (chg.submitting) return
  chg.payWin = (chg.quote && chg.quote.fare_diff > 0) ? openPayWindow() : null
  confirmChange()
}

async function confirmChange() {
  chg.submitting = true
  chg.error = ''
  try {
    const d = await api('/api/change', { method: 'POST', body: {
      order_no: chg.order.order_no, new_flight_no: chg.flight_no, new_date: chg.date,
      new_cabin: chg.cabin, requestId: chg.requestId,   // 幂等键：双击/重发不会改两次
      confirm_token: chg.quote.confirm_token
    }})
    if (d.need_pay) {
      // 差价未付 → 订单还没改。去收银台补齐，付款成功后后端自动落成改签。
      const ok = await pay(chg.order.order_no, { win: chg.payWin, paidText: '差价支付成功，改签已生效' })
      chg.payWin = null
      if (ok) { chg.open = false; await load() }
      else chg.error = '差价尚未支付完成，可稍后在「我的订单」里继续支付'
    } else {
      if (chg.payWin) { chg.payWin.close(); chg.payWin = null }
      toastSuccess(d.message || '改签成功')
      chg.open = false
      await load()
    }
  } catch (e) {
    if (chg.payWin) { chg.payWin.close(); chg.payWin = null }
    chg.error = e.message
  } finally {
    chg.submitting = false
  }
}

async function refund(o) {
  const voluntary = await confirmDialog('选择"确定"走自愿退票；选择"取消"走特殊退票（人工审核）。', '退票方式')
  try {
    const refundType = voluntary ? 'voluntary' : 'special'
    // 两种退票都先取报价：它同时签发确认凭证（自愿退票还会展示手续费明细）
    const quote = await api('/api/refund_quote' + qs({ order_no: o.order_no, refund_type: refundType }))
    if (quote.error) { toastError(quote.error); return }
    if (voluntary) {
      const ok1 = await confirmDialog(`票面金额 ¥${quote.amount}，手续费 ¥${quote.fee}，预计到账 ¥${quote.predict_amount}`, '确认退票')
      if (!ok1) return
    } else {
      const ok2 = await confirmDialog('确认提交特殊退票？将进入人工审核队列。', '特殊退票')
      if (!ok2) return
    }
    // requestId 是幂等键：同一笔只退一次（双击/重试不会重复退款）
    const d = await api('/api/refund', { method: 'POST', body: {
      order_no: o.order_no, refund_type: refundType,
      requestId: newRequestId('refund'), confirm_token: quote.confirm_token
    }})
    toastSuccess(d.message || '退票已提交')
    await load()
  } catch (e) {
    toastError(e.message)
  }
}
</script>

<style scoped>
.order-summary {
  display: flex; gap: 26px; align-items: center; margin-bottom: 16px;
  padding: 16px 22px;
}
.summary-item { display: flex; flex-direction: column; gap: 2px; }
.summary-num { color: var(--primary); font-size: 20px; font-weight: 800; letter-spacing: -.02em; }
.summary-label { font-size: 12px; color: var(--text-muted); }
.summary-divider { width: 1px; height: 34px; background: var(--border); }

.order-list { display: flex; flex-direction: column; gap: 12px; }
.order-card { padding: 16px 18px; }
.order-card:hover { box-shadow: var(--shadow); }
.order-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.order-flight { font-weight: 700; }
.flight-no { color: var(--text-muted); font-weight: 400; margin-left: 6px; font-size: 13px; }
.order-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: var(--text-muted); font-size: 13px; margin-bottom: 12px; }
.checkin-tag { color: var(--warning); }
.change-tag { color: var(--warning); }
.order-actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }

/* ---------- 改签面板 ---------- */
.chg-current { font-size: 13px; margin-bottom: 10px; }
.chg-form .el-form-item { margin-bottom: 12px; }
.chg-hint { margin-left: 8px; font-size: 12px; }
.chg-flights {
  display: flex; flex-direction: column; gap: 6px;
  max-height: 232px; overflow: auto; width: 100%; padding: 2px;
}
.chg-loading { padding: 8px 0; }
.chg-flight {
  display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
  padding: 9px 12px; border: 1px solid var(--border); border-radius: 10px;
  background: #fff; color: var(--text); font-size: 13px; cursor: pointer;
  transition: border-color .15s, background .15s;
}
.chg-flight:hover { border-color: var(--primary); }
.chg-flight.active { border-color: var(--primary); background: rgba(37, 99, 235, .06); }
.chg-no { flex: 1; }
.chg-time { color: var(--text-muted); }
.chg-price { color: var(--primary); font-weight: 600; min-width: 62px; text-align: right; }
.chg-quote {
  margin-top: 4px; padding: 10px 12px; border-radius: 10px;
  background: var(--bg); font-size: 13px;
}
.chg-quote-main { font-weight: 600; margin-bottom: 2px; }
.chg-need-pay .chg-quote-main { color: var(--warning); }
.chg-refund .chg-quote-main { color: var(--success); }
.chg-error { margin-top: 8px; color: var(--danger); font-size: 13px; }
</style>
