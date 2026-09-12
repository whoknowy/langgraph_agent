// 支付流程复用层：发起支付 → 跳收银台 → 轮询结果。
//
// 为什么要抽出来：支付入口在「我的订单」「机票预订」「聊天订票」三处都有，
// 逻辑完全一样（且分支容易写漏）。集中在这里，改一处三处生效。
//
// 两条后端契约（见 docs/PAYMENT_HANDOFF.md）：
// 1. mode 决定交互：redirect=去外部收银台后轮询；direct=站内直接确认。
//    不能只写 redirect 分支——后端切 mock 渠道时是 direct。
// 2. 支付是否成功只看 /api/pay/status 的 paid 字段，同步回跳不可信。
import { ref, onUnmounted } from 'vue'
import { api, qs } from '../../api.js'
import { toastError, toastSuccess } from '../../ui.js'

// 注意：api.js / ui.js 在 src/ 下（不是 src/client/），所以是 ../../ 而非 ../

/** 轮询间隔与上限：2 秒 × 90 ≈ 3 分钟（异步通知可能迟到，别设太短） */
const POLL_INTERVAL_MS = 2000
const POLL_MAX_TICKS = 90

/**
 * @param {object} opts
 * @param {() => (void|Promise<void>)} opts.onPaid 支付成功后的回调（刷新列表等）
 * @param {(orderNo: string) => void} [opts.onStateChange] 支付中状态变化通知（用于按钮 disabled）
 */
export function usePay({ onPaid, onStateChange } = {}) {
  /** 正在支付中的订单号，空串表示空闲 */
  const payingOrder = ref('')
  let pollTimer = null

  function setPaying(orderNo) {
    payingOrder.value = orderNo
    if (onStateChange) onStateChange(orderNo)
  }

  function stopPollTimer() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  }

  /** 停止轮询并清空支付中状态 */
  function stopPoll() {
    stopPollTimer()
    setPaying('')
  }

  onUnmounted(stopPoll)

  /**
   * 轮询支付结果。单次请求失败不中断（网络抖动/后端重启都可能），
   * 到上限后提示用户手动刷新，并返回 false。
   */
  function waitPaid(orderNo) {
    return new Promise((resolve) => {
      setPaying(orderNo)
      let ticks = 0
      stopPollTimer()
      pollTimer = setInterval(async () => {
        ticks += 1
        try {
          const s = await api('/api/pay/status' + qs({ order_no: orderNo }))
          if (s.paid) {
            stopPoll()
            toastSuccess('支付成功，订单已出票')
            if (onPaid) await onPaid(orderNo)
            resolve(true)
            return
          }
        } catch (e) {
          // 忽略，继续下一拍
        }
        if (ticks >= POLL_MAX_TICKS) {
          stopPoll()
          toastError('等待支付超时，请刷新确认支付结果（若已付款请勿重复支付）')
          if (onPaid) await onPaid(orderNo)
          resolve(false)
        }
      }, POLL_INTERVAL_MS)
    })
  }

  /**
   * 发起支付（完整流程）。
   * @param {string} orderNo 订单号
   * @param {object} [opts]
   * @param {Window} [opts.win] 预先打开的窗口。
   *   **必须在用户点击的同步阶段先 window.open('') 拿到它**，
   *   否则 await 之后再开窗口会脱离用户手势上下文，被浏览器当弹窗拦截。
   * @returns {Promise<boolean>} 是否已支付成功
   */
  async function pay(orderNo, { win } = {}) {
    try {
      const d = await api('/api/pay/create', { method: 'POST', body: { order_no: orderNo } })

      if (d.mode === 'redirect' && d.pay_url) {
        if (win) win.location.href = d.pay_url
        else window.location.href = d.pay_url
        return await waitPaid(orderNo)
      }

      // direct 模式：站内一步确认（mock 渠道）
      if (win) win.close()
      // confirm_token 由 /api/pay/create 签发：服务端要求"用户确认过支付"才允许落账
      const r = await api('/api/pay/confirm', { method: 'POST', body: { pay_no: d.pay_no, confirm_token: d.confirm_token } })
      toastSuccess(r.message || '支付成功，已出票')
      if (onPaid) await onPaid(orderNo)
      return true
    } catch (e) {
      if (win) win.close()
      stopPoll()
      toastError(e.message)
      return false
    }
  }

  return { payingOrder, pay, waitPaid, stopPoll }
}

/**
 * 同步阶段预开窗口，规避浏览器弹窗拦截。
 * 用法：const win = openPayWindow(); 然后 await pay(orderNo, { win })
 */
export function openPayWindow() {
  try {
    return window.open('', '_blank')
  } catch (e) {
    return null
  }
}
