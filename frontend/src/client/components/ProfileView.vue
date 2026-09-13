<template>
  <div>
    <h2 class="page-title">个人中心</h2>
    <div class="profile-grid">
      <div class="card profile-card">
        <div class="profile-avatar">{{ memberInitial }}</div>
        <div class="profile-name">{{ member?.name || '会员' }}</div>
        <div class="profile-id">{{ member?.member_id }} · {{ member?.level || '' }}</div>
        <div class="profile-points">
          <div><strong>12,580</strong><span>积分</span></div>
          <div class="points-divider"></div>
          <div><strong>8,600</strong><span>里程</span></div>
        </div>
      </div>

      <div class="card quick-card">
        <div class="section-title">快捷入口</div>
        <div class="quick-links">
          <router-link to="/orders" class="quick-link"><span class="q-ico">📦</span>我的订单</router-link>
          <router-link to="/flights" class="quick-link"><span class="q-ico">🎫</span>机票预订</router-link>
          <router-link to="/checkin" class="quick-link"><span class="q-ico">🪑</span>值机选座</router-link>
          <router-link to="/boardpass" class="quick-link"><span class="q-ico">🎟</span>电子登机牌</router-link>
        </div>
      </div>
    </div>

    <div class="profile-sections">
      <div class="card">
        <div class="section-title">我的投诉</div>
        <div v-if="complaints.length" class="list">
          <div v-for="c in complaints" :key="c.ticket_no" class="list-item">
            <div class="item-head">
              <strong>{{ c.ticket_no }}</strong>
              <span class="badge" :class="c.status === '已解决' ? 'badge-ok' : 'badge-pending'">{{ c.status }}</span>
            </div>
            <div class="muted">{{ c.content }}</div>
            <div class="muted" v-if="c.reply">回复：{{ c.reply }}</div>
          </div>
        </div>
        <div v-else class="empty">暂无投诉</div>
      </div>

      <div class="card">
        <div class="section-title notice-head">
          服务通知
          <span v-if="notifications.unread_count" class="badge badge-danger">{{ notifications.unread_count }} 未读</span>
          <button class="btn btn-sm mark-read" @click="markRead">全部已读</button>
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
import { ref, computed, onMounted } from 'vue'
import { api } from '@/shared/api.js'
import { toastError } from '@/shared/ui.js'
import { useAuth } from '@/client/stores/auth.js'

const { state } = useAuth()
const member = computed(() => state.member)
const memberInitial = computed(() => (member.value && member.value.name ? member.value.name[0] : '客'))

const complaints = ref([])
const notifications = ref({ items: [], unread_count: 0 })

onMounted(async () => {
  try {
    const d = await api('/api/my/complaints')
    complaints.value = d.complaints || []
  } catch (e) { console.warn(e) }
  try {
    const d = await api('/api/my/notifications')
    notifications.value = {
      items: d.notifications || [],
      unread_count: d.unread_count || 0
    }
  } catch (e) { console.warn(e) }
})

async function markRead() {
  try {
    await api('/api/my/notifications/read', { method: 'POST' })
    notifications.value.unread_count = 0
    notifications.value.items = notifications.value.items.map(n => ({ ...n, is_read: true }))
  } catch (e) { toastError(e.message) }
}
</script>

<style scoped>
.profile-grid { display: grid; grid-template-columns: 280px 1fr; gap: 16px; margin-bottom: 16px; }
.profile-card { text-align: center; }
.profile-avatar {
  width: 76px; height: 76px; border-radius: 50%;
  background: var(--brand-gradient); color: #fff; font-size: 30px; font-weight: 600;
  display: flex; align-items: center; justify-content: center; margin: 0 auto 10px;
  box-shadow: 0 8px 18px -6px rgba(37, 99, 235, .5);
}
.profile-name { font-size: 18px; font-weight: 700; }
.profile-id { color: var(--text-muted); font-size: 13px; margin: 4px 0 14px; }
.profile-points {
  display: flex; justify-content: center; align-items: center; gap: 26px;
  padding-top: 14px; border-top: 1px dashed var(--border);
}
.profile-points strong { display: block; font-size: 20px; color: var(--primary); letter-spacing: -.02em; }
.profile-points span { font-size: 12px; color: var(--text-muted); }
.points-divider { width: 1px; height: 30px; background: var(--border); }

.quick-card { padding: 20px; }
.quick-links { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.quick-link {
  display: flex; align-items: center; gap: 10px;
  padding: 13px 14px; border: 1px solid var(--border); border-radius: 12px;
  text-decoration: none; color: var(--text); font-size: 14px;
  transition: all .16s var(--ease);
}
.quick-link:hover {
  border-color: var(--primary); color: var(--primary);
  transform: translateY(-1px); box-shadow: 0 6px 14px -6px rgba(37, 99, 235, .3);
}
.q-ico { font-size: 16px; }

.profile-sections { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.list-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.list-item:last-child { border-bottom: none; }
.list-item.unread { background: #f0f9ff; margin: 0 -8px; padding: 10px 8px; border-radius: 10px; }
.item-head { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }
.notice-head { display: flex; align-items: center; gap: 8px; }
.mark-read { margin-left: auto; }
.time { font-size: 11px; margin-top: 3px; }

@media (max-width: 860px) {
  .profile-grid, .profile-sections { grid-template-columns: 1fr; }
}
</style>
