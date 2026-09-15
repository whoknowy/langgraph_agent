<template>
  <div class="confirm-card">
    <div class="cc-head">
      <div class="cc-icon">{{ icon }}</div>
      <div class="cc-head-main">
        <div class="cc-title">{{ title }}</div>
        <div v-if="subtitle" class="cc-sub">{{ subtitle }}</div>
      </div>
      <span class="cc-badge">待确认</span>
    </div>

    <!-- 订票：展示核对所需的关键信息（航司/航线/起降/舱位/人数/票价） -->
    <template v-if="isFlight">
      <div class="cc-flight">
        <div class="cc-flight-top">
          <span class="cc-airline">{{ data.airline }}</span>
          <span class="cc-flight-no">{{ data.flight_no }}</span>
          <span class="cc-date">{{ data.flight_date }}</span>
        </div>
        <div class="cc-route">
          <div class="cc-end">
            <b>{{ data.dep_time }}</b>
            <span>{{ data.dep_city }}</span>
          </div>
          <div class="cc-line"><i>✈</i></div>
          <div class="cc-end arr">
            <b>{{ data.arr_time }}</b>
            <span>{{ data.arr_city }}</span>
          </div>
        </div>
      </div>
      <div class="cc-grid">
        <div><span>舱位</span><b>{{ data.cabin }}舱</b></div>
        <div><span>人数</span><b>{{ data.passengers }} 人</b></div>
        <div><span>单价</span><b>¥{{ data.unit_price }}</b></div>
      </div>
    </template>

    <div v-else class="cc-desc">
      {{ desc }}
      <span v-if="dataLoading" class="cc-desc-tip">（正在获取票价…）</span>
    </div>

    <div class="cc-foot">
      <div v-if="isFlight" class="cc-pay">
        <span class="cc-pay-label">支付方式</span>
        <span class="cc-pay-chip">支付宝</span>
        <span class="cc-total">应付 <b>¥{{ money }}</b></span>
      </div>
      <div class="cc-actions">
        <button class="btn btn-sm" @click="$emit('cancel')">取消</button>
        <button class="btn btn-primary btn-sm" :disabled="loading" @click="$emit('confirm')">
          {{ loading ? '处理中…' : buttonText }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** 待确认动作类型：book_flight / refund / change_flight / seat_map */
  type: { type: String, default: '' },
  /** 文字描述：非订票类卡片直接展示，也是报价取不到时的兜底 */
  desc: { type: String, default: '' },
  /** 报价数据（订票卡片的结构化展示来源） */
  data: { type: Object, default: null },
  dataLoading: Boolean,
  buttonText: { type: String, default: '确认' },
  loading: Boolean
})
defineEmits(['confirm', 'cancel'])

const TITLES = {
  book_flight: '确认预订',
  refund: '确认退票',
  change_flight: '确认改签',
  seat_map: '值机选座'
}
const ICONS = {
  book_flight: '✈',
  refund: '↩',
  change_flight: '⇄',
  seat_map: '⌗'
}

const title = computed(() => TITLES[props.type] || '待确认操作')
const icon = computed(() => ICONS[props.type] || '⚡')
/** 报价到位且无错才算「可结构化展示」 */
const isFlight = computed(() => props.type === 'book_flight'
  && !!props.data && !props.data.error)
const subtitle = computed(() => props.type === 'book_flight'
  ? '请核对航班与票价，确认后跳转支付宝付款'
  : '')
const money = computed(() => Number((props.data || {}).total_amount || 0).toFixed(2))
</script>

<style scoped>
.confirm-card {
  margin: 0 18px 10px;
  padding: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  animation: card-in .25s var(--ease);
}
@keyframes card-in { from { opacity: 0; transform: translateY(8px); } }

/* ---------- 头部 ---------- */
.cc-head { display: flex; align-items: center; gap: 10px; }
.cc-icon {
  width: 34px; height: 34px; flex: none; border-radius: 10px;
  background: var(--brand-gradient); color: #fff; font-size: 16px;
  display: flex; align-items: center; justify-content: center;
}
.cc-head-main { min-width: 0; }
.cc-title { font-size: 14px; font-weight: 600; color: var(--text); }
.cc-sub { margin-top: 2px; font-size: 12px; color: var(--text-muted); }
.cc-badge {
  margin-left: auto; flex: none; font-size: 11px; padding: 2px 8px;
  border-radius: 999px; background: #fef3c7; color: #b45309;
}

/* ---------- 航班块 ---------- */
.cc-flight { margin-top: 12px; padding: 12px 14px; border-radius: 12px; background: var(--primary-light); }
.cc-flight-top { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.cc-airline { color: var(--primary); font-weight: 600; }
.cc-flight-no { color: var(--text); font-weight: 600; }
.cc-date { margin-left: auto; color: var(--text-muted); }
.cc-route { display: flex; align-items: center; gap: 14px; margin-top: 6px; }
.cc-end { display: flex; flex-direction: column; min-width: 64px; }
.cc-end.arr { align-items: flex-end; text-align: right; }
.cc-end b { font-size: 19px; font-weight: 700; letter-spacing: -.02em; color: var(--text); }
.cc-end span { font-size: 12px; color: var(--text-muted); margin-top: 1px; }
.cc-line { position: relative; flex: 1; height: 1px; background: var(--border-strong); }
.cc-line i {
  position: absolute; top: -9px; left: 50%; transform: translateX(-50%);
  font-style: normal; font-size: 13px; color: var(--primary);
  background: var(--primary-light); padding: 0 4px;
}

/* ---------- 信息栅格 ---------- */
.cc-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  margin-top: 10px; border: 1px solid var(--border); border-radius: 12px; overflow: hidden;
}
.cc-grid > div {
  display: flex; flex-direction: column; gap: 2px;
  padding: 9px 12px; border-right: 1px solid var(--border);
}
.cc-grid > div:last-child { border-right: none; }
.cc-grid span { font-size: 11.5px; color: var(--text-muted); }
.cc-grid b { font-size: 13.5px; font-weight: 600; color: var(--text); }

/* ---------- 描述兜底 ---------- */
.cc-desc { margin-top: 12px; font-size: 13px; color: var(--text); line-height: 1.6; }
.cc-desc-tip { color: var(--text-muted); }

/* ---------- 底部 ---------- */
.cc-foot {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border);
}
.cc-pay { display: flex; align-items: center; gap: 8px; min-width: 0; }
.cc-pay-label { font-size: 12px; color: var(--text-muted); }
.cc-pay-chip {
  font-size: 12px; font-weight: 500; color: var(--primary);
  background: var(--primary-light); border: 1px solid #c7dbfd;
  border-radius: 999px; padding: 2px 9px;
}
.cc-total { font-size: 12px; color: var(--text-muted); }
.cc-total b { font-size: 17px; font-weight: 700; letter-spacing: -.02em; color: #e11d48; }
.cc-actions { margin-left: auto; display: flex; gap: 8px; flex: none; }

@media (max-width: 560px) {
  .cc-actions { width: 100%; justify-content: flex-end; }
}
</style>
