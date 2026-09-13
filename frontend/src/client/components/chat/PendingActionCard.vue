<template>
  <div class="confirm-card">
    <div class="confirm-icon">⚡</div>
    <div class="confirm-main">
      <div class="confirm-title">待确认操作</div>
      <div class="confirm-body">{{ desc }}</div>
    </div>
    <div class="confirm-actions">
      <button class="btn btn-sm" @click="$emit('cancel')">取消</button>
      <button class="btn btn-primary btn-sm" :disabled="loading" @click="$emit('confirm')">
        {{ loading ? '处理中…' : buttonText }}
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  desc: { type: String, default: '' },
  buttonText: { type: String, default: '确认' },
  loading: Boolean
})
defineEmits(['confirm', 'cancel'])
</script>

<style scoped>
.confirm-card {
  margin: 0 18px 10px;
  display: flex; align-items: center; gap: 12px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border: 1px solid #fde68a; border-radius: 14px;
  box-shadow: 0 6px 16px -6px rgba(217, 119, 6, .25);
  animation: card-in .25s var(--ease);
}
@keyframes card-in { from { opacity: 0; transform: translateY(8px); } }
.confirm-icon {
  width: 34px; height: 34px; flex: none; border-radius: 10px;
  background: #f59e0b; color: #fff; font-size: 16px;
  display: flex; align-items: center; justify-content: center;
}
.confirm-main { flex: 1; min-width: 0; }
.confirm-title { font-weight: 700; font-size: 13px; margin-bottom: 2px; color: #78350f; }
.confirm-body { font-size: 13px; color: #92400e; }
.confirm-actions { display: flex; gap: 8px; flex: none; }
@media (max-width: 560px) {
  .confirm-card { flex-wrap: wrap; }
  .confirm-actions { width: 100%; justify-content: flex-end; }
}
</style>
