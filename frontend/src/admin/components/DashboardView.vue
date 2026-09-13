<template>
  <div>
    <h2 class="page-title">工作台</h2>
    <div class="stat-grid">
      <el-card shadow="never" class="stat-card">
        <div class="num warn">{{ stats.pending_refunds ?? '-' }}</div>
        <div class="lbl">待处理退款</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="num warn">{{ stats.pending_complaints ?? '-' }}</div>
        <div class="lbl">待处理投诉</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="num">{{ stats.flights_on_sale ?? '-' }}</div>
        <div class="lbl">在售航班</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="num">{{ stats.today_orders ?? '-' }}</div>
        <div class="lbl">今日新增订单</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="num">{{ stats.today_checkins ?? '-' }}</div>
        <div class="lbl">今日值机</div>
      </el-card>
    </div>

    <el-card shadow="never" style="margin-top:16px">
      <template #header>近 7 天订单 / 退款趋势</template>
      <div class="trend-chart">
        <div v-for="(d, i) in trend.days" :key="d" class="trend-col">
          <div class="trend-bars">
            <div class="bar bar-order" :style="{ height: barHeight(trend.orders[i]) }" :title="'订单 ' + trend.orders[i]"></div>
            <div class="bar bar-refund" :style="{ height: barHeight(trend.refunds[i]) }" :title="'退款 ' + trend.refunds[i]"></div>
          </div>
          <div class="trend-day">{{ d.slice(5) }}</div>
        </div>
      </div>
      <div class="chart-legend"><span><i class="lg lg-order"></i>订单量</span><span><i class="lg lg-refund"></i>退款量</span></div>
    </el-card>

    <el-card shadow="never" style="margin-top:16px">
      <template #header>热门航线 Top5</template>
      <div v-if="trend.top_routes && trend.top_routes.length" class="route-list">
        <div v-for="r in trend.top_routes" :key="r.route" class="route-row">
          <span class="route-name">{{ r.route }}</span>
          <div class="route-track"><div class="route-fill" :style="{ width: routeWidth(r.count) }"></div></div>
          <span class="route-count">{{ r.count }}</span>
        </div>
      </div>
      <div v-else class="empty">暂无热门航线数据</div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '@/shared/api.js'

const stats = ref({})
const trend = ref({ days: [], orders: [], refunds: [], top_routes: [] })

onMounted(async () => {
  try { stats.value = await api('/admin/api/stats', { admin: true }) } catch (e) { console.warn(e) }
  try { trend.value = await api('/admin/api/stats/trend', { admin: true }) } catch (e) { console.warn(e) }
})

function barHeight(v) {
  const max = Math.max(1, ...(trend.value.orders || []), ...(trend.value.refunds || []))
  return Math.max(4, Math.round((v / max) * 100)) + '%'
}
function routeWidth(v) {
  const max = Math.max(1, ...(trend.value.top_routes || []).map(r => r.count))
  return Math.max(6, Math.round((v / max) * 100)) + '%'
}
</script>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 16px; }
.stat-card :deep(.el-card__body) { padding: 18px; }
.stat-card .num { font-size: 28px; font-weight: 700; }
.stat-card .num.warn { color: var(--danger); }
.stat-card .lbl { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
.trend-chart { display: flex; align-items: stretch; gap: 8px; height: 170px; padding: 18px 4px 0; }
.trend-col { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center; }
.trend-bars { flex: 1; display: flex; align-items: flex-end; gap: 3px; width: 100%; justify-content: center; }
.bar { width: 14px; border-radius: 4px 4px 0 0; min-height: 2px; }
.bar-order { background: var(--primary); }
.bar-refund { background: var(--danger); }
.trend-day { margin-top: 6px; font-size: 11px; color: var(--text-muted); }
.chart-legend { margin-top: 10px; font-size: 12px; color: var(--text-muted); display: flex; gap: 16px; }
.lg { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 4px; vertical-align: -1px; }
.lg-order { background: var(--primary); }
.lg-refund { background: var(--danger); }
.route-list { display: flex; flex-direction: column; gap: 10px; }
.route-row { display: flex; align-items: center; gap: 10px; font-size: 13px; }
.route-name { width: 140px; text-align: right; white-space: nowrap; }
.route-track { flex: 1; background: #eef2f7; border-radius: 6px; height: 18px; overflow: hidden; }
.route-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #60a5fa); border-radius: 6px; }
.route-count { width: 36px; font-size: 12px; color: var(--text-muted); }
</style>
