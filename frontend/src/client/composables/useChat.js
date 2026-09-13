// 聊天状态机：会话管理 + SSE 流式接收。
//
// 流式契约（与后端 /api/chat/stream 逐条对应，改动前先看 memory 里的流式排坑记录）：
// - data.content 是**增量**分片，直接拼接下发，不要做 startswith diff；
// - data.tool    工具事件：running 转圈 → done 打勾（工具调用动画的来源）；
// - data.pending_action 待确认卡片；data.done 收尾（含 session_id / 兜底 response / tools 清单）；
// - [DONE] / data.error / 断流 都有明确收尾；断流回退 /api/chat 一次性取回。
import { ref, reactive, computed, nextTick, onMounted } from 'vue'
import { api, getMemberToken } from '@/shared/api.js'
import { toastError, confirmDialog } from '@/shared/ui.js'

export const TOOL_LABELS = {
  search_flights: '航班搜索', get_price_trend: '价格趋势', get_delay_prediction: '延误预测',
  get_weather: '天气查询', get_flight_price_detail: '票价构成', get_order_bill: '账单查询',
  query_complaint: '投诉查询', create_complaint: '投诉登记', submit_booking_request: '订票确认',
  refund_request: '退票确认', open_seat_map: '值机选座'
}

export const SUGGESTIONS = [
  '帮我查一下明天北京到上海的经济舱机票',
  '查一下我的订单账单',
  '帮我订一张明天CA1061经济舱',
  '明天东方航空北京到上海的航班容易延误吗',
  '上海明天的天气怎么样？适合出行吗',
  '我的机票可以退票吗？退票流程是什么'
]

export function useChat() {
  const messages = ref([])
  const sessions = ref([])
  const currentSessionId = ref('')
  const input = ref('')
  const isTyping = ref(false)
  const pendingAction = ref(null)

  /** 由 ChatView 绑定：消息滚动容器元素 */
  const scrollEl = ref(null)

  const hasActiveAssistant = computed(() =>
    messages.value.some(m => m.role === 'assistant' && (m.content || (m.toolChips && m.toolChips.length)))
  )

  function scrollBottom() {
    nextTick(() => {
      if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    })
  }

  /* ---------------- 会话管理 ---------------- */

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

  /* ---------------- 发送与流式接收 ---------------- */

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

  /** 断流兜底：一次性取回完整回复（流式通道不可用时保证功能不丢） */
  async function syncChat(message) {
    const d = await api('/api/chat', {
      method: 'POST',
      body: { message, session_id: currentSessionId.value }
    })
    if (d.session_id) currentSessionId.value = d.session_id
    messages.value.push({ role: 'assistant', content: d.response || '', toolChips: [] })
    if (d.pending_action) pendingAction.value = d.pending_action
  }

  onMounted(() => {
    newSession()
    loadSessions()
  })

  return {
    // 状态
    messages, sessions, currentSessionId, input, isTyping, pendingAction,
    scrollEl, hasActiveAssistant,
    // 会话
    newSession, loadSessions, loadSession, clearSession, deleteSession,
    // 发送
    send, quickAsk, scrollBottom
  }
}
