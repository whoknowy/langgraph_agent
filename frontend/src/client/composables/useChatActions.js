// 待确认操作（HITL）：确认卡片 + 值机选座弹窗。
//
// 写操作必须携带一次性确认凭证 confirm_token（服务端强制 HITL）：
// 正常由智能体在卡片里签发；卡片没有时（旧服务端 / 凭证过期）回准备接口现取一张。
import { ref, computed, watch } from 'vue'
import { api, qs, newRequestId } from '@/shared/api.js'
import { toastError, confirmDialog } from '@/shared/ui.js'
import { usePay } from './usePay.js'

/**
 * @param {object} chat useChat() 的返回（共享 messages / pendingAction）
 */
export function useChatActions(chat) {
  const { messages, pendingAction } = chat

  const actionLoading = ref(false)
  const seatModal = ref({ show: false, map: {}, selected: '', error: '', loading: false, action: null })

  // 聊天里订票后的支付：与「我的订单」「机票预订」共用同一实现。
  // 这里不传 win —— 跳转方式与那两处不同，原因见 confirmAction 里的注释。
  const { pay } = usePay()

  // 确认卡片要展示真实金额，而 pending_action 里只有航班号/日期/舱位：
  // 卡片一出现就补一次报价查询（纯展示用；确认凭证仍走 ensureToken）。
  // - 订票卡片 → 航司/航线/起降/单价/总价
  // - 改签卡片 → 原航班、新航班、**差价**（补差价要用户在点确认之前就看到，
  //   而不是跳到收银台才知道——收银台只显示金额，不解释来由）
  const cardQuote = ref({ loading: false, error: '', data: null })
  watch(pendingAction, (a) => { loadCardQuote(a) }, { immediate: true })

  async function loadCardQuote(a) {
    if (!a || (a.type !== 'book_flight' && a.type !== 'change_flight')) {
      cardQuote.value = { loading: false, error: '', data: null }
      return
    }
    cardQuote.value = { loading: true, error: '', data: null }
    try {
      const q = a.type === 'book_flight'
        ? await api('/api/booking_quote' + qs({
            flight_no: a.flight_no, flight_date: a.flight_date,
            cabin: a.cabin, passengers: Number(a.passengers || 1)
          }))
        : await api('/api/change_quote' + qs({
            order_no: a.order_no, new_flight_no: a.new_flight_no,
            new_date: a.new_date, new_cabin: a.new_cabin
          }))
      cardQuote.value = q && q.error
        ? { loading: false, error: q.error, data: null }
        : { loading: false, error: '', data: q }
    } catch (e) {
      // 报价拿不到不阻塞确认：卡片退回文字描述，确认时 ensureToken 仍会再试一次
      cardQuote.value = { loading: false, error: e.message, data: null }
    }
  }

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

  /** 取写操作所需的一次性确认凭证（卡片没带时回准备接口现取） */
  async function ensureToken(a) {
    if (a.confirm_token) return a.confirm_token
    try {
      if (a.type === 'book_flight') {
        const q = await api('/api/booking_quote' + qs({
          flight_no: a.flight_no, flight_date: a.flight_date,
          cabin: a.cabin, passengers: Number(a.passengers || 1)
        }))
        return q.confirm_token
      }
      if (a.type === 'refund') {
        const q = await api('/api/refund_quote' + qs({
          order_no: a.order_no, refund_type: a.refund_type || 'voluntary'
        }))
        return q.confirm_token
      }
      if (a.type === 'change_flight') {
        const q = await api('/api/change_quote' + qs({
          order_no: a.order_no, new_flight_no: a.new_flight_no,
          new_date: a.new_date, new_cabin: a.new_cabin
        }))
        return q.confirm_token
      }
      if (a.type === 'seat_map') {
        const q = await api('/api/checkin/seats' + qs({
          flight_no: a.flight_no, flight_date: a.flight_date, order_no: a.order_no
        }))
        return q.confirm_token
      }
    } catch (e) {
      // 取不到就让写接口按「缺少确认凭证」报错，前端提示用户重新发起
    }
    return ''
  }

  function pushAssistant(content) {
    messages.value.push({ role: 'assistant', content, toolChips: [] })
  }

  async function confirmAction() {
    const a = pendingAction.value
    if (!a) return
    actionLoading.value = true
    try {
      if (a.type === 'book_flight') {
        const d = await api('/api/book', { method: 'POST', body: {
          flight_no: a.flight_no, flight_date: a.flight_date,
          cabin: a.cabin, passengers: Number(a.passengers || 1),
          confirm_token: await ensureToken(a)
        }})
        pushAssistant(`✅ 订单 ${d.order_no} 已创建（待支付），金额 ¥${d.total_amount}`)
        pendingAction.value = null
        if (await confirmDialog('订单已创建，是否立即支付？', '支付')) {
          // 注意：这里不能用 window.open 跳收银台——confirmDialog 是 await 过来的，
          // 用户手势上下文已失效，新窗口会被浏览器拦掉。改用当前页跳转，
          // 支付结果页（/#/pay/result）会自动把用户带回订单列表。
          const ok = await pay(d.order_no)
          pushAssistant(ok
            ? `✅ 支付成功，订单 ${d.order_no} 已出票`
            : `订单 ${d.order_no} 尚未支付完成，可到「我的订单」继续支付。`)
        }
      } else if (a.type === 'refund') {
        const d = await api('/api/refund', { method: 'POST', body: {
          order_no: a.order_no, refund_type: a.refund_type || 'voluntary',
          requestId: newRequestId('refund'), confirm_token: await ensureToken(a)
        }})
        pushAssistant(`✅ ${d.message || '退票已提交'}`)
        pendingAction.value = null
      } else if (a.type === 'change_flight') {
        const d = await api('/api/change', { method: 'POST', body: {
          order_no: a.order_no, new_flight_no: a.new_flight_no,
          new_date: a.new_date, new_cabin: a.new_cabin,
          requestId: newRequestId('change'),   // 幂等键：聊天里重发不会改两次
          confirm_token: await ensureToken(a)
        }})
        if (d.need_pay) {
          // 差价未付 → 订单还没改，必须补齐才生效。
          // 这里不能用 window.open（confirmDialog 之后手势已失效），只能当前页跳收银台；
          // 付款成功后后端会自动落成改签，支付结果页会把用户带回订单列表。
          pushAssistant(`需要补差价 ¥${d.fare_diff}，正在前往支付宝收银台…`)
          const ok = await pay(a.order_no, { paidText: '差价支付成功，改签已生效' })
          pushAssistant(ok
            ? `✅ 差价已支付，订单 ${a.order_no} 改签已生效`
            : `订单 ${a.order_no} 的差价尚未支付完成，可到「我的订单」继续支付。`)
        } else {
          // 无差价（或已原路退回差价）→ 改签已生效，message 里已带上差价说明
          pushAssistant(`✅ ${d.message || '改签成功'}`)
        }
        pendingAction.value = null
      } else if (a.type === 'seat_map') {
        const d = await api('/api/checkin/seats' + qs({ flight_no: a.flight_no, flight_date: a.flight_date, order_no: a.order_no }))
        // 座位图接口会签发该订单的值机确认凭证，选座确认时回传
        seatModal.value = { show: true, map: d.cabins || {}, selected: '', error: '', loading: false,
                            action: { ...a, confirm_token: d.confirm_token || a.confirm_token } }
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

  function closeSeatModal() {
    seatModal.value.show = false
  }

  async function confirmSeat() {
    const a = seatModal.value.action
    const seat = seatModal.value.selected
    if (!a || !seat) return
    seatModal.value.loading = true
    try {
      const d = await api('/api/checkin', { method: 'POST', body: {
        order_no: a.order_no, seat_no: seat, confirm_token: a.confirm_token } })
      seatModal.value.show = false
      pushAssistant(`✅ 值机成功：${d.airline} ${d.flight_no} 座位 ${d.seat_no} 登机口 ${d.gate}`)
      pendingAction.value = null
    } catch (e) {
      seatModal.value.error = e.message
    } finally {
      seatModal.value.loading = false
    }
  }

  return {
    actionLoading, seatModal, cardQuote,
    pendingActionDesc, actionButtonText,
    confirmAction, cancelAction, confirmSeat, closeSeatModal
  }
}
