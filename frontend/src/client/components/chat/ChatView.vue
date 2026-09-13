<template>
  <div class="chat-view">
    <SessionPanel
      :sessions="chat.sessions.value"
      :current-session-id="chat.currentSessionId.value"
      :open="sessionDrawer"
      @new="chat.newSession(); sessionDrawer = false"
      @refresh="chat.loadSessions"
      @select="chat.loadSession($event); sessionDrawer = false"
      @clear="chat.clearSession"
      @remove="chat.deleteSession"
      @close="sessionDrawer = false"
    />

    <div class="chat-workspace">
      <!-- 移动端会话抽屉入口 -->
      <button class="drawer-toggle" title="会话列表" @click="sessionDrawer = true">☰ 会话</button>

      <ChatMessageList
        :messages="chat.messages.value"
        :is-typing="chat.isTyping.value"
        :has-active-assistant="chat.hasActiveAssistant.value"
        :scroll-el="chat.scrollEl"
        @quick-ask="chat.quickAsk"
      />

      <PendingActionCard
        v-if="chat.pendingAction.value"
        :desc="actions.pendingActionDesc.value"
        :button-text="actions.actionButtonText.value"
        :loading="actions.actionLoading.value"
        @confirm="actions.confirmAction"
        @cancel="actions.cancelAction"
      />

      <ChatInput v-model="chat.input.value" :disabled="chat.isTyping.value" @send="chat.send" />
    </div>

    <SeatPickerModal
      :show="actions.seatModal.value.show"
      :map="actions.seatModal.value.map"
      :selected="actions.seatModal.value.selected"
      :error="actions.seatModal.value.error"
      :loading="actions.seatModal.value.loading"
      @update:selected="actions.seatModal.value.selected = $event"
      @confirm="actions.confirmSeat"
      @close="actions.closeSeatModal"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useChat } from '@/client/composables/useChat.js'
import { useChatActions } from '@/client/composables/useChatActions.js'
import SessionPanel from './SessionPanel.vue'
import ChatMessageList from './ChatMessageList.vue'
import ChatInput from './ChatInput.vue'
import PendingActionCard from './PendingActionCard.vue'
import SeatPickerModal from './SeatPickerModal.vue'

const chat = useChat()
const actions = useChatActions(chat)
const sessionDrawer = ref(false)
</script>

<style scoped>
.chat-view { display: flex; gap: 16px; height: 100%; min-height: 0; }

.chat-workspace {
  flex: 1; min-width: 0; position: relative;
  background: rgba(255, 255, 255, .82); backdrop-filter: blur(8px);
  border: 1px solid var(--border); border-radius: var(--radius);
  display: flex; flex-direction: column; overflow: hidden;
  box-shadow: var(--shadow-sm);
}

.drawer-toggle {
  display: none;
  position: absolute; top: 10px; left: 12px; z-index: 20;
  padding: 6px 12px; border: 1px solid var(--border); border-radius: 999px;
  background: #fff; color: var(--text-muted); font-size: 12px;
  box-shadow: var(--shadow-sm);
}
@media (max-width: 900px) {
  .drawer-toggle { display: block; }
}
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
