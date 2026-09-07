<template>
  <div>
    <h2 class="page-title">个人中心</h2>
    <div class="profile-grid">
      <div class="card profile-card">
        <div class="profile-avatar">{{ (member && member.name ? member.name[0] : '客') }}</div>
        <div class="profile-name">{{ member?.name || '会员' }}</div>
        <div class="profile-id">{{ member?.member_id }} · {{ member?.level || '' }}</div>
        <div class="profile-points">
          <div><strong>12,580</strong><span>积分</span></div>
          <div><strong>8,600</strong><span>里程</span></div>
        </div>
      </div>

      <div class="card quick-card">
        <div class="section-title">快捷入口</div>
        <div class="quick-links">
          <router-link to="/orders" class="quick-link">📦 我的订单</router-link>
          <router-link to="/flights" class="quick-link">🎫 机票预订</router-link>
          <router-link to="/checkin" class="quick-link">🪑 值机选座</router-link>
          <router-link to="/boardpass" class="quick-link">🎟 电子登机牌</router-link>
        </div>
      </div>
    </div>

    <div class="profile-sections">
      <div class="card">
        <div class="section-title">我的投诉</div>
        <div v-if="complaints.length" class="list">
          <div v-for="c in complaints" :key="c.ticket_no" class="list-item">
            <div><strong>{{ c.ticket_no }}</strong><span class="badge" :class="c.status === '已解决' ? 'badge-ok' : 'badge-pending'">{{ c.status }}</span></div>
            <div class="muted">{{ c.content }}</div>
            <div class="muted" v-if="c.reply">回复：{{ c.reply }}</div>
          </div>
        </div>
        <div v-else class="empty">暂无投诉</div>
      </div>

      <div class="card">
        <div class="section-title">
          服务通知
          <span v-if="notifications.unread_count" class="badge badge-danger">{{ notifications.unread_count }}未读</span>
          <button class="btn btn-sm" style="margin-left:10px" @click="markRead">全部已读</button>
        </div>
        <div v-if="notifications.items.length" class="list">
          <div v-for="n in notifications.items" :key="n.id" class="list-item" :class="{ unread: !n.is_read }">
            <div><strong>{{ n.title }}</strong></div>
            <div class="muted">{{ n.content }}</div>
            <div class="muted time">{{ n.created_at }}</div>
          </div>
        </div>
        <div v-else class="empty">暂无通知</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

defineProps({ member: Object })

const complaints = ref([])
const notifications = ref({ items: [], unread_count: 0 })

onMounted(async () => {
  try {
    const d = await api('/api/my/complaints')
    complaints.value = d.complaints || []
  } catch (e) { console.warn(e) }
  try {
    const d = await api('/api/my/notifications')
    notifications.value = d
  } catch (e) { console.warn(e) }
})

async function markRead() {
  try {
    await api('/api/my/notifications/read', { method: 'POST' })
    notifications.value.unread_count = 0
    notifications.value.items = notifications.value.items.map(n => ({ ...n, is_read: true }))
  } catch (e) { alert(e.message) }
}
</script>

<style scoped>
.profile-grid { display: grid; grid-template-columns: 280px 1fr; gap: 16px; margin-bottom: 16px; }
.profile-card { text-align: center; }
.profile-avatar {
  width: 72px; height: 72px; border-radius: 50%; background: var(--primary); color: #fff; font-size: 30px;
  display: flex; align-items: center; justify-content: center; margin: 0 auto 10px;
}
.profile-name { font-size: 18px; font-weight: 700; }
.profile-id { color: var(--text-muted); font-size: 13px; margin: 4px 0 14px; }
.profile-points { display: flex; justify-content: center; gap: 30px; padding-top: 14px; border-top: 1px dashed var(--border); }
.profile-points strong { display: block; font-size: 20px; color: var(--primary); }
.profile-points span { font-size: 12px; color: var(--text-muted); }
.quick-card { padding: 18px; }
.quick-links { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.quick-link { padding: 12px; border: 1px solid var(--border); border-radius: 10px; text-decoration: none; color: var(--text); font-size: 14px; }
.quick-link:hover { border-color: var(--primary); color: var(--primary); }
.profile-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.list-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.list-item.unread { background: #f0f9ff; margin: 0 -8px; padding: 10px 8px; border-radius: 8px; }
.time { font-size: 11px; margin-top: 3px; }
</style>