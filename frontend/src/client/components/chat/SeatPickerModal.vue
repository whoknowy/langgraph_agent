<template>
  <div v-if="show" class="modal-mask" @click.self="$emit('close')">
    <div class="modal-box seat-box">
      <div class="modal-title">🪑 在线值机 · 选座</div>
      <div class="error-text" v-if="error">{{ error }}</div>
      <div v-for="(rows, cabin) in map" :key="cabin" class="cabin-block">
        <div class="cabin-label">{{ cabin }}舱</div>
        <div v-for="row in rows" :key="row.row" class="seat-row">
          <span class="row-no">{{ row.row }}</span>
          <button
            v-for="seat in row.seats"
            :key="seat.seat_no"
            class="seat"
            :class="{ occupied: seat.status === 'occupied', mine: seat.mine, selected: selected === seat.seat_no }"
            :disabled="seat.status === 'occupied'"
            @click="$emit('update:selected', seat.seat_no)"
          >{{ seat.seat_no }}</button>
        </div>
      </div>
      <div class="seat-legend">
        <span><i class="free"></i>可选</span>
        <span><i class="occupied"></i>已占</span>
        <span><i class="selected"></i>已选</span>
      </div>
      <div class="modal-actions">
        <button class="btn" @click="$emit('close')">关闭</button>
        <button class="btn btn-primary" :disabled="!selected || loading" @click="$emit('confirm')">
          {{ loading ? '提交中…' : '确认值机' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  show: Boolean,
  map: { type: Object, default: () => ({}) },
  selected: { type: String, default: '' },
  error: { type: String, default: '' },
  loading: Boolean
})
defineEmits(['update:selected', 'confirm', 'close'])
</script>

<style scoped>
.seat-box { width: 540px; max-height: 82vh; overflow: auto; }
.cabin-block { margin-bottom: 14px; }
.cabin-label { font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.seat-row { display: flex; gap: 4px; margin-bottom: 4px; align-items: center; }
.row-no { width: 26px; font-size: 12px; color: var(--text-muted); }
.seat {
  width: 36px; height: 31px; border-radius: 8px; border: 1px solid var(--border); background: #fff;
  font-size: 11px; color: #333;
  transition: all .12s var(--ease);
}
.seat:hover:not(:disabled) { border-color: var(--primary); transform: scale(1.06); }
.seat.occupied { background: #e5e7eb; color: #9ca3af; cursor: not-allowed; border-color: #e5e7eb; }
.seat.mine { background: #fef3c7; border-color: #f59e0b; color: #92400e; }
.seat.selected {
  background: var(--brand-gradient); color: #fff; border-color: transparent;
  box-shadow: 0 3px 8px -2px rgba(37, 99, 235, .45);
}
.seat-legend { display: flex; gap: 14px; font-size: 12px; color: var(--text-muted); margin: 12px 0; }
.seat-legend i { display: inline-block; width: 12px; height: 12px; border-radius: 4px; margin-right: 4px; vertical-align: -1px; }
.seat-legend .free { background: #fff; border: 1px solid var(--border); }
.seat-legend .occupied { background: #e5e7eb; }
.seat-legend .selected { background: var(--primary); }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
