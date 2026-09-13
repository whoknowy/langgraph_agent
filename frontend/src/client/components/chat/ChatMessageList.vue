<template>
  <div ref="bodyEl" class="chat-body">
    <!-- 空状态：欢迎语 + 推荐问题 -->
    <div v-if="!messages.length" class="chat-empty">
      <div class="empty-logo">✈</div>
      <h3>您好，我是智能客服助手</h3>
      <p>可以帮你查航班、订机票、值机选座、退改签、投诉、行程规划</p>
      <div class="suggest-list">
        <button v-for="p in suggestions" :key="p" class="suggest-pill" @click="$emit('quickAsk', p)">{{ p }}</button>
      </div>
    </div>

    <ChatMessage v-for="(msg, i) in messages" :key="i" :msg="msg" />

    <!-- 思考中（首个分片/工具事件到达前） -->
    <div v-if="isTyping && !hasActiveAssistant" class="msg-row assistant">
      <div class="msg-avatar">✈</div>
      <div class="msg-content">
        <div class="msg-meta">智能客服</div>
        <div class="typing"><span></span><span></span><span></span></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import ChatMessage from './ChatMessage.vue'
import { SUGGESTIONS } from '@/client/composables/useChat.js'

const props = defineProps({
  messages: { type: Array, default: () => [] },
  isTyping: Boolean,
  hasActiveAssistant: Boolean,
  scrollEl: Object   // useChat 的 scrollEl ref，交给父级绑定
})
defineEmits(['quickAsk'])

const suggestions = SUGGESTIONS
const bodyEl = ref(null)

onMounted(() => {
  if (props.scrollEl) props.scrollEl.value = bodyEl.value
})
</script>

<style scoped>
.chat-body {
  flex: 1; overflow: auto; padding: 24px 22px 12px;
  scroll-behavior: smooth;
}
@media (max-width: 900px) {
  /* 给移动端「☰ 会话」悬浮按钮让位 */
  .chat-body { padding-top: 54px; }
}

.chat-empty { text-align: center; padding: 56px 20px; animation: msg-in .3s var(--ease); }
@keyframes msg-in { from { opacity: 0; transform: translateY(8px); } }
.empty-logo {
  width: 64px; height: 64px; margin: 0 auto 16px; border-radius: 20px;
  background: var(--brand-gradient); color: #fff; font-size: 30px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 10px 24px -6px rgba(37, 99, 235, .45);
}
.chat-empty h3 { font-size: 20px; letter-spacing: -.01em; margin-bottom: 8px; }
.chat-empty p { color: var(--text-muted); margin-bottom: 22px; }
.suggest-list { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; max-width: 600px; margin: 0 auto; }
.suggest-pill {
  padding: 9px 15px; border-radius: 999px; border: 1px solid var(--border);
  background: #fff; font-size: 13px; color: var(--text);
  transition: all .16s var(--ease); box-shadow: var(--shadow-sm);
}
.suggest-pill:hover {
  border-color: var(--primary); color: var(--primary);
  transform: translateY(-1px); box-shadow: 0 6px 14px -4px rgba(37, 99, 235, .25);
}

/* 思考中：三个跳动圆点 */
.msg-row { display: flex; gap: 10px; margin-bottom: 18px; }
.msg-avatar {
  width: 34px; height: 34px; border-radius: 50%; flex: none;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: #fff; background: var(--brand-gradient);
}
.msg-meta { font-size: 11px; color: var(--text-faint); margin-bottom: 5px; }
.typing {
  display: inline-flex; gap: 5px; align-items: center;
  padding: 13px 16px; background: #fff; border: 1px solid var(--border);
  border-radius: 4px 16px 16px 16px; box-shadow: var(--shadow-sm);
}
.typing span {
  width: 7px; height: 7px; border-radius: 50%; background: #b6c6f5;
  animation: typing-bounce 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: .15s; }
.typing span:nth-child(3) { animation-delay: .3s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: .6; }
  30% { transform: translateY(-5px); opacity: 1; background: var(--primary); }
}
</style>
