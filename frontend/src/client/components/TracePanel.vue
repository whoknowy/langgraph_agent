<template>
  <div class="trace-panel">
    <div class="trace-head">
      <span class="trace-title">⛓ 执行链路</span>
      <button class="trace-close" title="收起" @click="$emit('close')">×</button>
    </div>

    <div v-if="!hasTrace" class="trace-empty">
      发送消息后，这里将实时点亮本轮对话经过的<br/>图节点与工具调用（敏感词守卫 → 意图分类 → 业务专家 → 工具）。
    </div>

    <template v-else>
      <div v-if="trace.guard_blocked" class="trace-banner blocked">⛔ 输入已被敏感词守卫拦截，未进入业务节点</div>
      <div v-else-if="trace.target_agent" class="trace-banner routed">
        路由 → {{ nodeLabel(trace.target_agent) }}
      </div>

      <div class="trace-flow">
        <div v-for="(n, i) in nodes" :key="n.name + i" class="trace-node" :class="{ last: i === nodes.length - 1 }">
          <div class="tn-line">
            <span class="tn-dot" :class="n.status">
              <span v-if="n.status === 'running'" class="spin"></span>
            </span>
            <span class="tn-name">{{ n.label || nodeLabel(n.name) }}</span>
            <span v-if="n.duration_ms != null" class="tn-dur">{{ n.duration_ms }}ms</span>
            <span v-if="n.note" class="tn-note">{{ n.note }}</span>
          </div>

          <div v-for="t in toolsOf(n, i === nodes.length - 1)" :key="t.name" class="trace-tool">
            <div class="tt-line" @click="toggleArgs(t.name)">
              <span class="tt-icon" :class="t.status">{{ t.status === 'running' ? '◌' : '✓' }}</span>
              <span class="tt-name">{{ t.label || toolLabel(t.name) }}</span>
              <span v-if="t.duration_ms != null" class="tt-dur">{{ t.duration_ms }}ms</span>
              <span class="tt-toggle">{{ openArgs[t.name] ? '▾' : '▸' }} 参数</span>
            </div>
            <pre v-if="openArgs[t.name]" class="tt-args">{{ fmtArgs(t.args) }}</pre>
          </div>
        </div>
      </div>

      <div v-if="isLive" class="trace-live">● 实时执行中…</div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { toolLabel, nodeLabel } from '../labels.js'

const props = defineProps({ trace: Object })
defineEmits(['close'])

const openArgs = reactive({})

const nodes = computed(() => (props.trace && props.trace.nodes) || [])
const hasTrace = computed(() => nodes.value.length > 0)
const isLive = computed(() =>
  nodes.value.some(n => n.status === 'running') ||
  ((props.trace && props.trace.tools) || []).some(t => t.status === 'running'))

const toolsOf = (n, isLast) => {
  const tools = (props.trace && props.trace.tools) || []
  const mine = tools.filter(t => t.node === n.name)
  // 没挂到节点的工具（兜底/异常路径）归入最后一个节点，避免丢展示
  if (isLast) mine.push(...tools.filter(t => !t.node))
  return mine
}

const toggleArgs = (name) => { openArgs[name] = !openArgs[name] }
const fmtArgs = (args) => {
  if (!args || Object.keys(args).length === 0) return '（无参数）'
  try {
    return JSON.stringify(args, null, 2)
  } catch (e) {
    return String(args)
  }
}
</script>

<style scoped>
.trace-panel {
  width: 264px; flex: none; background: #fff; border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px; display: flex; flex-direction: column;
  overflow: hidden;
}
.trace-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.trace-title { font-size: 13px; font-weight: 700; }
.trace-close { border: none; background: none; font-size: 18px; color: var(--text-faint); line-height: 1; }
.trace-close:hover { color: var(--danger); }
.trace-empty { font-size: 12px; color: var(--text-faint); line-height: 1.8; padding: 24px 4px; text-align: center; }

.trace-banner { font-size: 12px; border-radius: 8px; padding: 6px 10px; margin-bottom: 10px; }
.trace-banner.blocked { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
.trace-banner.routed { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }

.trace-flow { flex: 1; overflow: auto; }
.trace-node { position: relative; padding-left: 5px; }
.trace-node:not(.last)::before {
  content: ''; position: absolute; left: 9px; top: 18px; bottom: -6px;
  width: 2px; background: #e2e8f0; border-radius: 1px;
}
.tn-line { display: flex; align-items: center; gap: 7px; padding: 3px 0; }
.tn-dot {
  width: 12px; height: 12px; border-radius: 50%; flex: none;
  background: #e2e8f0; display: inline-flex; align-items: center; justify-content: center;
}
.tn-dot.running { background: #dbeafe; }
.tn-dot.done { background: #10b981; }
.tn-dot .spin {
  width: 6px; height: 6px; border-radius: 50%;
  border: 2px solid #93c5fd; border-top-color: #2563eb;
  animation: trace-spin .8s linear infinite;
}
@keyframes trace-spin { to { transform: rotate(360deg); } }
.tn-name { font-size: 13px; font-weight: 600; }
.tn-dur { font-size: 11px; color: var(--text-muted); }
.tn-note { font-size: 11px; color: #b91c1c; }

.trace-tool { margin: 2px 0 6px 26px; }
.tt-line {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 3px 8px; border-radius: 7px; background: #f8fafc; border: 1px solid #eef2f7;
}
.tt-line:hover { border-color: #c7d2fe; }
.tt-icon { font-size: 11px; color: #9ca3af; }
.tt-icon.running { color: #2563eb; }
.tt-icon.done { color: #059669; }
.tt-name { font-size: 12px; color: #334155; flex: 1; }
.tt-dur { font-size: 11px; color: var(--text-muted); }
.tt-toggle { font-size: 11px; color: var(--text-faint); }
.tt-args {
  margin: 4px 0 0; padding: 8px; border-radius: 7px; background: #0f172a; color: #e2e8f0;
  font-size: 11px; line-height: 1.5; overflow: auto; max-height: 220px; white-space: pre-wrap;
  word-break: break-all;
}
.trace-live { font-size: 11px; color: #2563eb; padding-top: 8px; animation: trace-blink 1.2s infinite; }
@keyframes trace-blink { 50% { opacity: .35; } }
</style>
