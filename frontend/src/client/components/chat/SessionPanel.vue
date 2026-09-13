<template>
  <aside class="session-panel" :class="{ open }">
    <div class="panel-head">
      <button class="new-chat-btn" @click="$emit('new')">
        <span class="plus">＋</span> 新建对话
      </button>
      <button class="icon-btn" title="刷新会话列表" @click="$emit('refresh')">⟳</button>
    </div>
    <div class="panel-label">最近对话</div>
    <div class="session-list">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-item"
        :class="{ active: s.session_id === currentSessionId }"
        @click="$emit('select', s.session_id)"
      >
        <div class="session-main">
          <div class="session-preview">{{ s.title || s.last_user_question || '新对话' }}</div>
          <div class="session-meta">{{ s.message_count || 0 }} 条 · {{ formatSessionTime(s.created_at) }}</div>
        </div>
        <div class="session-ops">
          <button class="op-btn" title="清空对话" @click.stop="$emit('clear', s.session_id)">↺</button>
          <button class="op-btn danger" title="删除会话" @click.stop="$emit('remove', s.session_id)">×</button>
        </div>
      </div>
      <div v-if="!sessions.length" class="empty">暂无会话</div>
    </div>
  </aside>
  <div v-if="open" class="panel-mask" @click="$emit('close')"></div>
</template>

<script setup>
import { formatSessionTime } from '@/shared/format.js'

defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
  open: { type: Boolean, default: false }
})
defineEmits(['new', 'refresh', 'select', 'clear', 'remove', 'close'])
</script>

<style scoped>
.session-panel {
  width: 264px; flex: none;
  background: rgba(255, 255, 255, .82); backdrop-filter: blur(8px);
  border: 1px solid var(--border); border-radius: var(--radius);
  padding: 14px; display: flex; flex-direction: column; overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.panel-head { display: flex; gap: 8px; margin-bottom: 12px; }
.new-chat-btn {
  flex: 1; display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px 0; border: none; border-radius: var(--radius-sm);
  background: var(--brand-gradient); color: #fff; font-weight: 600; font-size: 13px;
  box-shadow: 0 4px 12px -2px rgba(37, 99, 235, .35);
  transition: filter .15s, transform .15s;
}
.new-chat-btn:hover { filter: brightness(1.07); transform: translateY(-1px); }
.new-chat-btn .plus { font-size: 15px; }
.icon-btn {
  width: 38px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: #fff; color: var(--text-muted); font-size: 15px;
  transition: all .15s;
}
.icon-btn:hover { color: var(--primary); border-color: var(--primary); }

.panel-label {
  font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  color: var(--text-faint); margin-bottom: 8px; padding: 0 4px;
}
.session-list { flex: 1; overflow: auto; margin: 0 -4px; padding: 0 4px; }

.session-item {
  display: flex; align-items: center; gap: 4px;
  padding: 10px 10px; border-radius: 12px; cursor: pointer;
  border: 1px solid transparent;
  transition: background .15s, border-color .15s;
}
.session-item:hover { background: #f3f6fc; }
.session-item:hover .session-ops { opacity: 1; }
.session-item.active {
  background: var(--primary-light); border-color: #c7dbfc;
}
.session-item.active .session-preview { color: var(--primary-dark); }
.session-main { flex: 1; min-width: 0; }
.session-preview {
  font-size: 13px; font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.session-meta { font-size: 11px; color: var(--text-faint); margin-top: 3px; }
.session-ops { display: flex; opacity: 0; transition: opacity .15s; }
.op-btn {
  border: none; background: none; color: var(--text-faint);
  font-size: 15px; width: 24px; height: 24px; border-radius: 6px;
}
.op-btn:hover { background: #e8edf6; color: var(--text); }
.op-btn.danger:hover { color: var(--danger); background: #fef2f2; }

.panel-mask { display: none; }

/* 移动端：抽屉式会话面板 */
@media (max-width: 900px) {
  .session-panel {
    position: fixed; left: 0; top: 0; bottom: 0; z-index: 120;
    width: 280px; border-radius: 0;
    transform: translateX(-100%); transition: transform .25s var(--ease);
    box-shadow: var(--shadow-lg);
  }
  .session-panel.open { transform: translateX(0); }
  .panel-mask {
    display: block; position: fixed; inset: 0; z-index: 110;
    background: rgba(15, 23, 42, .4); backdrop-filter: blur(2px);
  }
}
</style>
