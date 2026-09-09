<template>
  <div class="chat-view">
    <div class="session-panel">
      <div class="session-actions">
        <button class="btn btn-sm" @click="newSession">＋ 新建对话</button>
        <button class="btn btn-sm" @click="loadSessions">刷新</button>
      </div>
      <div class="session-title">最近对话</div>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.session_id"
          class="session-item"
          :class="{ active: s.session_id === currentSessionId }"
          @click="loadSession(s.session_id)"
        >
          <div class="session-main">
            <div class="session-preview">{{ s.title || s.last_user_question || '新对话' }}</div>
            <div class="session-meta">{{ s.message_count || 0 }} 条 · {{ s.created_at || '' }}</div>
          </div>
          <button class="session-del" title="清空" @click.stop="clearSession(s.session_id)">↺</button>
          <button class="session-del" title="删除" @click.stop="deleteSession(s.session_id)">×</button>
        </div>
        <div v-if="!sessions.length" class="empty">暂无会话</div>
      </div>
    </div>

    <div class="chat-workspace">
      <div ref="chatBody" class="chat-body">
        <div v-if="!messages.length" class="chat-empty">
          <h3>您好，我是智能客服助手</h3>
          <p>可以帮你查航班、订机票、值机选座、退改签、投诉、行程规划</p>
          <div class="suggest-list">
            <button v-for="p in suggestions" :key="p" class="suggest-pill" @click="quickAsk(p)">{{ p }}</button>
          </div>
        </div>

        <div v-for="(msg, i) in messages" :key="i" class="msg-row" :class="msg.role">
          <div class="msg-avatar">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
          <div class="msg-content">
            <div class="msg-meta">{{ msg.role === 'user' ? '我' : '智能客服' }}</div>
            <div v-if="msg.toolChips && msg.toolChips.length" class="tool-chips">
              <span
                v-for="chip in msg.toolChips"
                :key="chip.name"
                class="tool-chip"
                :class="chip.status"
              >
                <span v-if="chip.status === 'running'" class="tool-spinner"></span>
                <span v-else class="tool-done">✅</span>
                {{ chip.status === 'running' ? '正在执行【' + chip.label + '】…' : '已执行【' + chip.label + '】' }}
              </span>
            </div>
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div class="msg-body" :class="{ plain: msg.streaming, 'md-body': !msg.streaming }" v-html="renderMessageHtml(msg)"></div>
          </div>
        </div>

        <div v-if="isTyping && !hasActiveAssistant" class="msg-row assistant">
          <div class="msg-avatar">AI</div>
          <div class="msg-content">
            <div class="msg-meta">智能客服</div>
            <div class="typing">思考中<span>.</span><span>.</span><span>.</span></div>
          </div>
        </div>
      </div>

      <div v-if="pendingAction" class="confirm-card">
        <div class="confirm-title">⚡ 待确认操作</div>
        <div class="confirm-body">{{ pendingActionDesc }}</div>
        <div class="confirm-actions">
          <button class="btn" @click="cancelAction">取消</button>
          <button class="btn btn-primary" :disabled="actionLoading" @click="confirmAction">
            {{ actionLoading ? '处理中…' : actionButtonText }}
          </button>
        </div>
      </div>

      <div class="chat-input-bar">
        <textarea
          v-model="input"
          rows="1"
          class="chat-textarea"
          placeholder="请输入您的问题，Enter 发送 / Shift+Enter 换行"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button class="send-btn" :disabled="!input.trim() || isTyping" @click="send">发送</button>
      </div>
    </div>

    <!-- 值机选座弹窗 -->
    <div v-if="seatModal.show" class="modal-mask" @click.self="seatModal.show = false">
      <div class="modal-box seat-box">
        <div class="modal-title">🪑 在线值机 · 选座</div>
        <div class="muted" v-if="seatModal.error">{{ seatModal.error }}</div>
        <div v-for="(rows, cabin) in seatModal.map" :key="cabin" class="cabin-block">
          <div class="cabin-label">{{ cabin }}舱</div>
          <div v-for="row in rows" :key="row.row" class="seat-row">
            <span class="row-no">{{ row.row }}</span>
            <button
              v-for="seat in row.seats"
              :key="seat.seat_no"
              class="seat"
              :class="{ occupied: seat.status === 'occupied', mine: seat.mine, selected: seatModal.selected === seat.seat_no }"
              :disabled="seat.status === 'occupied'"
              @click="seatModal.selected = seat.seat_no"
            >{{ seat.seat_no }}</button>
          </div>
        </div>
        <div class="seat-legend">
          <span><i class="free"></i>可选</span>
          <span><i class="occupied"></i>已占</span>
          <span><i class="selected"></i>已选</span>
        </div>
        <div class="modal-actions">
          <button class="btn" @click="seatModal.show = false">关闭</button>
          <button class="btn btn-primary" :disabled="!seatModal.selected || seatModal.loading" @click="confirmSeat">
            {{ seatModal.loading ? '提交中…' : '确认值机' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick, reactive } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api, qs, getMemberToken } from '../../api.js'
import { toastError, toastSuccess, confirmDialog } from '../../ui.js'

const props = defineProps({ member: Object })

const messages = ref([])
const sessions = ref([])
const currentSessionId = ref('')
const input = ref('')
const isTyping = ref(false)
const pendingAction = ref(null)
const actionLoading = ref(false)
const chatBody = ref(null)
const seatModal = ref({ show: false, map: {}, selected: '', error: '', loading: false, action: null })

const suggestions = [
  '帮我查一下明天北京到上海的经济舱机票',
  '查一下我的订单账单',
  '帮我订一张明天CA1061经济舱',
  '明天东方航空北京到上海的航班容易延误吗',
  '上海明天的天气怎么样？适合出行吗',
  '我的机票可以退票吗？退票流程是什么'
]

const TOOL_LABELS = {
  search_flights: '航班搜索', get_price_trend: '价格趋势', get_delay_prediction: '延误预测',
  get_weather: '天气查询', get_flight_price_detail: '票价构成', get_order_bill: '账单查询',
  query_complaint: '投诉查询', create_complaint: '投诉登记', submit_booking_request: '订票确认',
  refund_request: '退票确认', open_seat_map: '值机选座'
}

onMounted(() => {
  newSession()
  loadSessions()
})

const hasActiveAssistant = computed(() =>
  messages.value.some(m => m.role === 'assistant' && (m.content || (m.toolChips && m.toolChips.length)))
)

const pendingActionDesc = computed(() => {
  const a = pendingAction.value
  if (!a) return ''
  if (a.type === 'book_flight') return `预订 ${a.flight_no} ${a.flight_date} ${a.cabin}舱 ${a.passengers || 1}人`
  if (a.type === 'refund') return `订单 ${a.order_no} 申请${a.refund_type === 'special' ? '特殊' : '自愿'}退票`
  if (a.type === 'change_flight') return `订单 ${a.order_no} 改签至 ${a.new_flight_no} ${a.new_date} ${a.new_cabin}舱`
  if (a.type === 'seat_map') return `订单 ${a.order_no} 值机选座`
  return JSON.stringify(a)
})

const actionButtonText = computed(() => {
  const a = pendingAction.value
  if (!a) return ''
  if (a.type === 'book_flight') return '确认预订'
  if (a.type === 'refund') return '确认退票'
  if (a.type === 'change_flight') return '确认改签'
  if (a.type === 'seat_map') return '选择座位'
  return '确认'
})

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderMarkdown(text) {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(String(text)))
  } catch (e) {
    return escapeHtml(text)
  }
}

function renderMessageHtml(msg) {
  // 流式过程中只渲染纯文本，避免每次 token 都跑 Markdown 解析导致卡顿；
  // 流结束再渲染 Markdown（表格/列表可读性）。
  if (msg.streaming) return escapeHtml(msg.content || '')
  return renderMarkdown(msg.content || '')
}

function scrollBottom() {
  nextTick(() => {
    if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
  })
}

function newSession() {
  currentSessionId.value = 'web_' + Date.now()
  messages.value = []
  pendingAction.value = null
}

async function loadSessions() {
  try {
    const d = await api('/api/sessions')
    sessions.value = d.sessions || []
    const isNewLocal = !currentSessionId.value || currentSessionId.value.startsWith('web_')
    if (isNewLocal && sessions.value.length) {
      loadSession(sessions.value[0].session_id)
    }
  } catch (e) {
    console.warn('加载会话失败', e)
  }
}

async function loadSession(id) {
  currentSessionId.value = id
  try {
    const d = await api(`/api/sessions/${encodeURIComponent(id)}`)
    const s = d.session || {}
    messages.value = (s.conversation_history || []).map(m => ({
      role: m.is_user ? 'user' : 'assistant',
      content: m.content || '',
      toolChips: []
    }))
    pendingAction.value = null
    scrollBottom()
  } catch (e) {
    console.warn(e)
  }
}

async function clearSession(id) {
  if (!(await confirmDialog('确定清空此对话？', '清空会话'))) return
  try {
    const d = await api(`/api/sessions/${encodeURIComponent(id)}/clear`, { method: 'POST' })
    currentSessionId.value = d.new_thread_id || ('web_' + Date.now())
    messages.value = []
    pendingAction.value = null
    await loadSessions()
  } catch (e) {
    toastError(e.message)
  }
}

async function deleteSession(id) {
  if (!(await confirmDialog('确定删除此会话？', '删除会话'))) return
  try {
    await api(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (id === currentSessionId.value) newSession()
    await loadSessions()
  } catch (e) {
    toastError(e.message)
  }
}

function quickAsk(p) {
  input.value = p
  send()
}

async function send() {
  const message = input.value.trim()
  if (!message || isTyping.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: message, toolChips: [] })
  isTyping.value = true
  scrollBottom()
  try {
    const ok = await streamChat(message)
    if (!ok) await syncChat(message)
  } catch (e) {
    messages.value.push({ role: 'assistant', content: '网络错误：' + e.message, toolChips: [] })
  } finally {
    isTyping.value = false
    scrollBottom()
    loadSessions()
  }
}

async function streamChat(message) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getMemberToken()
  if (token) headers['Authorization'] = 'Bearer ' + token
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, session_id: currentSessionId.value })
  })
  if (!res.ok || !res.body) return false
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let streamText = ''
  let assistantMsg = null
  let toolChips = []

  const toolLabel = (name) => TOOL_LABELS[name] || name

  const addToolChip = (name, status = 'running') => {
    ensureAssistant()
    let chip = toolChips.find(c => c.name === name)
    if (!chip) {
      chip = { name, label: toolLabel(name), status }
      toolChips.push(chip)
    } else {
      chip.status = status
    }
    return chip
  }

  const syncToolChips = () => {
    if (assistantMsg) assistantMsg.toolChips = toolChips.map(c => ({ ...c }))
  }

  const finishToolChips = (names) => {
    const finished = new Set(names || [])
    toolChips.forEach(c => {
      if (finished.size === 0 || finished.has(c.name)) c.status = 'done'
    })
    ;(names || []).forEach(name => {
      if (!toolChips.find(c => c.name === name)) {
        toolChips.push({ name, label: toolLabel(name), status: 'done' })
      }
    })
    syncToolChips()
  }

  const ensureAssistant = () => {
    if (!assistantMsg) {
      assistantMsg = reactive({ role: 'assistant', content: '', toolChips: [], streaming: true })
      messages.value.push(assistantMsg)
    }
    return assistantMsg
  }

  const finishAssistant = () => {
    if (assistantMsg) assistantMsg.streaming = false
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        const t = line.trim()
        if (!t.startsWith('data:')) continue
        const payload = t.slice(5).trim()
        if (payload === '[DONE]') {
          finishAssistant()
          finishToolChips()
          return true
        }
        let data
        try { data = JSON.parse(payload) } catch (e) { continue }
        if (data.error) {
          ensureAssistant().content = data.error
          finishAssistant()
          finishToolChips()
          return true
        }
        if (data.tool && data.tool.name) {
          addToolChip(data.tool.name, data.tool.status === 'done' ? 'done' : 'running')
          syncToolChips()
        }
        if (data.content) {
          streamText += data.content
          ensureAssistant().content = streamText
          scrollBottom()
        }
        if (data.pending_action) {
          pendingAction.value = data.pending_action
        }
        if (data.done) {
          if (data.session_id) currentSessionId.value = data.session_id
          if (data.response && !streamText) {
            ensureAssistant().content = data.response
          }
          finishAssistant()
          finishToolChips(data.tools)
          return true
        }
      }
    }
    finishAssistant()
    finishToolChips()
    return !!assistantMsg
  } catch (e) {
    finishAssistant()
    finishToolChips()
    return false
  }
}

async function syncChat(message) {
  const d = await api('/api/chat', {
    method: 'POST',
    body: { message, session_id: currentSessionId.value }
  })
  if (d.session_id) currentSessionId.value = d.session_id
  messages.value.push({ role: 'assistant', content: d.response || '', toolChips: [] })
  if (d.pending_action) pendingAction.value = d.pending_action
}

async function confirmAction() {
  const a = pendingAction.value
  if (!a) return
  actionLoading.value = true
  try {
    if (a.type === 'book_flight') {
      const d = await api('/api/book', { method: 'POST', body: {
        flight_no: a.flight_no, flight_date: a.flight_date,
        cabin: a.cabin, passengers: Number(a.passengers || 1)
      }})
      messages.value.push({ role: 'assistant', content: `✅ 订单 ${d.order_no} 已创建（待支付），金额 ¥${d.total_amount}`, toolChips: [] })
      pendingAction.value = null
      if (await confirmDialog('订单已创建，是否立即支付？', '支付')) {
        await api('/api/pay', { method: 'POST', body: { order_no: d.order_no } })
        messages.value.push({ role: 'assistant', content: `✅ 支付成功，订单 ${d.order_no} 已出票`, toolChips: [] })
      }
    } else if (a.type === 'refund') {
      const d = await api('/api/refund', { method: 'POST', body: {
        order_no: a.order_no, refund_type: a.refund_type || 'voluntary'
      }})
      messages.value.push({ role: 'assistant', content: `✅ ${d.message || '退票已提交'}`, toolChips: [] })
      pendingAction.value = null
    } else if (a.type === 'change_flight') {
      const d = await api('/api/change', { method: 'POST', body: {
        order_no: a.order_no, new_flight_no: a.new_flight_no,
        new_date: a.new_date, new_cabin: a.new_cabin
      }})
      messages.value.push({ role: 'assistant', content: `✅ ${d.message || '改签成功'}`, toolChips: [] })
      pendingAction.value = null
    } else if (a.type === 'seat_map') {
      const d = await api('/api/checkin/seats' + qs({ flight_no: a.flight_no, flight_date: a.flight_date, order_no: a.order_no }))
      seatModal.value = { show: true, map: d.cabins || {}, selected: '', error: '', loading: false, action: a }
    }
  } catch (e) {
    toastError(e.message)
  } finally {
    actionLoading.value = false
  }
}

function cancelAction() {
  pendingAction.value = null
}

async function confirmSeat() {
  const a = seatModal.value.action
  const seat = seatModal.value.selected
  if (!a || !seat) return
  seatModal.value.loading = true
  try {
    const d = await api('/api/checkin', { method: 'POST', body: { order_no: a.order_no, seat_no: seat } })
    seatModal.value.show = false
    messages.value.push({ role: 'assistant', content: `✅ 值机成功：${d.airline} ${d.flight_no} 座位 ${d.seat_no} 登机口 ${d.gate}`, toolChips: [] })
    pendingAction.value = null
  } catch (e) {
    seatModal.value.error = e.message
  } finally {
    seatModal.value.loading = false
  }
}
</script>

<style scoped>
.chat-view { display: flex; gap: 16px; height: calc(100vh - 58px - 40px); }
.session-panel {
  width: 250px; flex: none; background: #fff; border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px; display: flex; flex-direction: column; overflow: hidden;
}
.session-actions { display: flex; gap: 8px; margin-bottom: 10px; }
.session-title { font-size: 12px; color: var(--text-faint); margin-bottom: 8px; }
.session-list { flex: 1; overflow: auto; }
.session-item {
  display: flex; align-items: center; gap: 6px; padding: 9px 10px; border-radius: 9px; cursor: pointer;
  border: 1px solid transparent;
}
.session-item:hover { background: #f5f7fb; }
.session-item.active { background: var(--primary-light); border-color: #bfdbfe; }
.session-main { flex: 1; min-width: 0; }
.session-preview { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.session-del { border: none; background: none; color: var(--text-faint); font-size: 16px; }
.session-del:hover { color: var(--danger); }

.chat-workspace {
  flex: 1; min-width: 0; background: #fff; border: 1px solid var(--border); border-radius: var(--radius);
  display: flex; flex-direction: column; overflow: hidden;
}
.chat-body { flex: 1; overflow: auto; padding: 20px; }
.chat-empty { text-align: center; padding: 60px 20px; }
.chat-empty h3 { font-size: 20px; margin-bottom: 8px; }
.chat-empty p { color: var(--text-muted); margin-bottom: 20px; }
.suggest-list { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; max-width: 560px; margin: 0 auto; }
.suggest-pill { padding: 8px 14px; border-radius: 999px; border: 1px solid var(--border); background: #f9fafb; font-size: 13px; }
.suggest-pill:hover { border-color: var(--primary); color: var(--primary); }

.msg-row { display: flex; gap: 10px; margin-bottom: 16px; }
.msg-row.user { flex-direction: row-reverse; }
.msg-avatar {
  width: 32px; height: 32px; border-radius: 50%; flex: none; background: #e0e7ff; color: #3730a3;
  display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600;
}
.msg-row.user .msg-avatar { background: var(--primary); color: #fff; }
.msg-content { max-width: 70%; }
.msg-meta { font-size: 11px; color: var(--text-faint); margin-bottom: 4px; }
.msg-body {
  background: #f5f7fb; border-radius: 12px; padding: 10px 14px; font-size: 14px; line-height: 1.7;
  word-break: break-word;
}
.msg-body.plain { white-space: pre-wrap; }

.msg-row.user .msg-body { background: var(--primary); color: #fff; }
.tool-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.tool-chip {
  display: inline-flex; align-items: center; gap: 5px; font-size: 11px;
  padding: 3px 9px; border-radius: 999px; background: #eef2ff; color: #4338ca;
  transition: background .2s, color .2s;
}
.tool-chip.running { background: #eef2ff; color: #4338ca; }
.tool-chip.done { background: #ecfdf5; color: #047857; }
.tool-spinner {
  width: 10px; height: 10px; border-radius: 50%; flex: none;
  border: 2px solid #c7d2fe; border-top-color: #4338ca;
  animation: tool-spin .8s linear infinite;
}
.tool-done { font-size: 11px; }
@keyframes tool-spin { to { transform: rotate(360deg); } }
.typing { color: var(--text-muted); padding: 10px 14px; background: #f5f7fb; border-radius: 12px; }
.typing span { animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }

.confirm-card {
  margin: 0 20px 10px; padding: 12px 14px; background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 12px;
}
.confirm-title { font-weight: 700; margin-bottom: 6px; }
.confirm-body { font-size: 13px; color: #78350f; margin-bottom: 10px; }
.confirm-actions { display: flex; gap: 8px; }

.chat-input-bar {
  flex: none; display: flex; gap: 10px; padding: 12px 16px; border-top: 1px solid var(--border);
}
.chat-textarea {
  flex: 1; resize: none; border: 1px solid var(--border); border-radius: 10px; padding: 9px 12px;
  outline: none; min-height: 42px; max-height: 120px;
}
.chat-textarea:focus { border-color: var(--primary); }
.send-btn {
  width: 72px; border: none; border-radius: 10px; background: var(--primary); color: #fff; font-weight: 600;
}
.send-btn:disabled { background: #cbd5e1; }

.seat-box { width: 520px; max-height: 80vh; overflow: auto; }
.cabin-block { margin-bottom: 14px; }
.cabin-label { font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.seat-row { display: flex; gap: 4px; margin-bottom: 4px; align-items: center; }
.row-no { width: 24px; font-size: 12px; color: var(--text-muted); }
.seat {
  width: 34px; height: 30px; border-radius: 6px; border: 1px solid var(--border); background: #fff;
  font-size: 11px; color: #333;
}
.seat.occupied { background: #e5e7eb; color: #9ca3af; cursor: not-allowed; }
.seat.mine { background: #fef3c7; border-color: #f59e0b; }
.seat.selected { background: var(--primary); color: #fff; border-color: var(--primary); }
.seat-legend { display: flex; gap: 14px; font-size: 12px; color: var(--text-muted); margin: 10px 0; }
.seat-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 4px; vertical-align: -1px; }
.seat-legend .free { background: #fff; border: 1px solid var(--border); }
.seat-legend .occupied { background: #e5e7eb; }
.seat-legend .selected { background: var(--primary); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
<style>
/* Markdown 表格 / 常用排版（全局样式，作用于 v-html 注入的表格元素） */
.chat-view .msg-body.md-body table {
  width: 100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 13px;
}
.chat-view .msg-body.md-body th,
.chat-view .msg-body.md-body td {
  padding: 7px 10px; border-bottom: 1px solid var(--border); border-right: 1px solid var(--border);
  text-align: left; white-space: nowrap;
}
.chat-view .msg-body.md-body th:last-child,
.chat-view .msg-body.md-body td:last-child { border-right: none; }
.chat-view .msg-body.md-body th {
  background: #f0f4ff; color: #1e40af; font-weight: 600;
}
.chat-view .msg-body.md-body tbody tr:nth-child(even) { background: #fafbff; }
.chat-view .msg-body.md-body tbody tr:hover { background: #eef2ff; }
.chat-view .msg-body.md-body p { margin: 0.5em 0; }
.chat-view .msg-body.md-body ul,
.chat-view .msg-body.md-body ol { margin: 0.5em 0; padding-left: 1.4em; }
.chat-view .msg-body.md-body li { margin: 0.2em 0; }
.chat-view .msg-body.md-body code { background: #eef2ff; color: #4338ca; padding: 1px 5px; border-radius: 4px; }
.chat-view .msg-body.md-body pre { background: #0f172a; color: #e2e8f0; border-radius: 10px; padding: 12px; overflow-x: auto; }
.chat-view .msg-body.md-body pre code { background: transparent; color: inherit; padding: 0; }
.chat-view .msg-body.md-body blockquote {
  border-left: 3px solid #bfdbfe; background: #eff6ff; border-radius: 0 8px 8px 0;
  margin: 0.6em 0; padding: 4px 12px; color: #1e3a8a;
}
</style>
