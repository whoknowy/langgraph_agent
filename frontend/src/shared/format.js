// 展示层格式化工具

/**
 * 会话时间：后端列表接口优先返回可读字符串，同时兼容旧前端的秒级时间戳 / ISO 字符串。
 */
export function formatSessionTime(value) {
  if (!value) return ''
  if (typeof value === 'string') {
    if (/^\d{4}-\d{2}-\d{2}/.test(value)) return value
    const t = Date.parse(value.replace('Z', '+00:00'))
    if (!isNaN(t)) return new Date(t).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    return value
  }
  const d = new Date(Number(value) * 1000)
  if (isNaN(d.getTime())) return String(value)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** "08:30" + "10:45" → "2h15"（跨天自动 +24h） */
export function flightDuration(dep, arr) {
  const [dh, dm] = String(dep).split(':').map(Number)
  const [ah, am] = String(arr).split(':').map(Number)
  let mins = ah * 60 + am - dh * 60 - dm
  if (mins < 0) mins += 24 * 60
  return Math.floor(mins / 60) + 'h' + (mins % 60)
}

/** 座位号 31A → 列字母 A */
export function seatLetter(seatNo) {
  const m = String(seatNo || '').match(/([A-Za-z]+)$/)
  return m ? m[1].toUpperCase() : seatNo
}

/** 明天 YYYY-MM-DD（默认搜索日期） */
export function tomorrow() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  return d.toISOString().slice(0, 10)
}
