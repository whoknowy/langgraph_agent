<template>
  <div class="chat-input-bar">
    <div class="composer">
      <textarea
        ref="ta"
        :value="modelValue"
        rows="1"
        class="chat-textarea"
        placeholder="请输入您的问题，Enter 发送 / Shift+Enter 换行"
        :disabled="disabled"
        @input="onInput"
        @keydown.enter.exact.prevent="$emit('send')"
      ></textarea>
      <button class="send-btn" :disabled="!modelValue.trim() || disabled" title="发送" @click="$emit('send')">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true">
          <path d="M3.4 20.4 L21.8 12 L3.4 3.6 L3.3 9.7 L15.2 12 L3.3 14.3 Z" fill="currentColor"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  disabled: Boolean
})
const emit = defineEmits(['update:modelValue', 'send'])

const ta = ref(null)

function onInput(e) {
  emit('update:modelValue', e.target.value)
}

/* 自动高度：随内容增高，封顶 120px */
watch(() => props.modelValue, async () => {
  await nextTick()
  const el = ta.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
})
</script>

<style scoped>
.chat-input-bar {
  flex: none; padding: 12px 18px 16px;
  border-top: 1px solid var(--border);
  background: rgba(255, 255, 255, .7); backdrop-filter: blur(6px);
}
.composer {
  display: flex; align-items: flex-end; gap: 10px;
  border: 1px solid var(--border); border-radius: 18px;
  background: #fff; padding: 8px 8px 8px 16px;
  transition: border-color .15s, box-shadow .15s;
}
.composer:focus-within {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, .12);
}
.chat-textarea {
  flex: 1; resize: none; border: none; outline: none; background: transparent;
  min-height: 26px; max-height: 120px; line-height: 1.6; padding: 5px 0;
}
.send-btn {
  width: 40px; height: 40px; flex: none; border: none; border-radius: 13px;
  background: var(--brand-gradient); color: #fff;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 10px -2px rgba(37, 99, 235, .4);
  transition: filter .15s, transform .15s;
}
.send-btn:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
.send-btn:disabled { background: #dbe3ef; box-shadow: none; color: #fff; cursor: not-allowed; }
</style>
