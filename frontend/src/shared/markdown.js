// Markdown 渲染与 HTML 转义（聊天消息专用）
import { marked } from 'marked'
import DOMPurify from 'dompurify'

export function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(String(text)))
  } catch (e) {
    return escapeHtml(text)
  }
}

/**
 * 流式过程中只渲染纯文本，避免每个 token 都跑 Markdown 解析导致卡顿；
 * 流结束再渲染 Markdown（表格/列表可读性）。
 */
export function renderMessageHtml(msg) {
  if (msg.streaming) return escapeHtml(msg.content || '')
  return renderMarkdown(msg.content || '')
}
