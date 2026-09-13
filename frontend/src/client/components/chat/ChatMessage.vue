<template>
  <div class="msg-row" :class="msg.role">
    <div class="msg-avatar" :class="msg.role">
      <span v-if="msg.role === 'user'">我</span>
      <span v-else>✈</span>
    </div>
    <div class="msg-content">
      <div class="msg-meta">{{ msg.role === 'user' ? '我' : '智能客服' }}</div>

      <!-- 工具调用 chip：running 转圈 / done 打勾（工具调用动画） -->
      <div v-if="msg.toolChips && msg.toolChips.length" class="tool-chips">
        <span
          v-for="chip in msg.toolChips"
          :key="chip.name"
          class="tool-chip"
          :class="chip.status"
        >
          <span v-if="chip.status === 'running'" class="tool-spinner"></span>
          <svg v-else class="tool-check" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
            <path d="M2 6.5 L4.8 9 L10 3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          {{ chip.status === 'running' ? '正在执行【' + chip.label + '】…' : '已执行【' + chip.label + '】' }}
        </span>
      </div>

      <!-- eslint-disable-next-line vue/no-v-html -->
      <div
        class="msg-body"
        :class="{ plain: msg.streaming, 'md-body': !msg.streaming }"
        v-html="renderMessageHtml(msg)"
      ></div>
      <span v-if="msg.streaming" class="stream-caret"></span>
    </div>
  </div>
</template>

<script setup>
import { renderMessageHtml } from '@/shared/markdown.js'

defineProps({ msg: { type: Object, required: true } })
</script>

<style scoped>
.msg-row {
  display: flex; gap: 10px; margin-bottom: 18px;
  animation: msg-in .25s var(--ease);
}
@keyframes msg-in { from { opacity: 0; transform: translateY(8px); } }
.msg-row.user { flex-direction: row-reverse; }

.msg-avatar {
  width: 34px; height: 34px; border-radius: 50%; flex: none;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 600; color: #fff;
  background: var(--brand-gradient);
  box-shadow: 0 3px 8px -2px rgba(37, 99, 235, .4);
}
.msg-avatar.user { background: linear-gradient(135deg, #475569, #1e293b); box-shadow: 0 3px 8px -2px rgba(30, 41, 59, .35); }

.msg-content { max-width: min(72%, 640px); display: flex; flex-direction: column; }
.msg-row.user .msg-content { align-items: flex-end; }
.msg-meta { font-size: 11px; color: var(--text-faint); margin-bottom: 5px; }

.msg-body {
  background: #fff; border: 1px solid var(--border);
  border-radius: 4px 16px 16px 16px;
  padding: 11px 15px; font-size: 14px; line-height: 1.75;
  word-break: break-word; box-shadow: var(--shadow-sm);
}
.msg-body.plain { white-space: pre-wrap; }
.msg-row.user .msg-body {
  background: var(--brand-gradient); border: none; color: #fff;
  border-radius: 16px 4px 16px 16px;
  box-shadow: 0 4px 12px -2px rgba(37, 99, 235, .35);
}

/* 流式光标：提示还在输出 */
.stream-caret {
  display: inline-block; width: 7px; height: 15px; margin-top: 4px;
  background: var(--primary); border-radius: 2px;
  animation: caret-blink .9s steps(2) infinite;
}
@keyframes caret-blink { 50% { opacity: 0; } }

/* 工具调用 chip */
.tool-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 7px; }
.tool-chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11.5px; font-weight: 500;
  padding: 4px 11px; border-radius: 999px;
  background: #eef2ff; color: #4338ca; border: 1px solid #e0e7ff;
  transition: background .25s, color .25s, border-color .25s;
}
.tool-chip.done { background: #ecfdf5; color: #047857; border-color: #d1fae5; }
.tool-spinner {
  width: 10px; height: 10px; border-radius: 50%; flex: none;
  border: 2px solid #c7d2fe; border-top-color: #4338ca;
  animation: tool-spin .8s linear infinite;
}
.tool-check { flex: none; }
@keyframes tool-spin { to { transform: rotate(360deg); } }
</style>
