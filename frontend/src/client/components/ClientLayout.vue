<template>
  <div class="client-shell">
    <aside class="client-sidebar">
      <div class="side-brand">
        <div class="brand-badge">✈</div>
        <div>
          <div class="brand-name">智能航空客服</div>
          <div class="brand-sub">让出行更简单</div>
        </div>
      </div>
      <nav class="side-nav">
        <router-link to="/chat" class="nav-link" active-class="active">
          <span class="nav-ico">💬</span> AI 客服对话
        </router-link>
        <router-link to="/flights" class="nav-link" active-class="active">
          <span class="nav-ico">🎫</span> 机票预订
        </router-link>
        <router-link to="/checkin" class="nav-link" active-class="active">
          <span class="nav-ico">🪑</span> 值机选座
        </router-link>
        <router-link to="/boardpass" class="nav-link" active-class="active">
          <span class="nav-ico">🎟</span> 登机牌
        </router-link>
        <router-link to="/orders" class="nav-link" active-class="active">
          <span class="nav-ico">📦</span> 我的订单
        </router-link>
        <router-link to="/profile" class="nav-link" active-class="active">
          <span class="nav-ico">👤</span> 个人中心
        </router-link>
      </nav>
      <div class="side-foot">
        <button class="btn btn-sm" @click="$emit('logout')">退出登录</button>
      </div>
    </aside>
    <div class="client-main">
      <header class="client-topbar">
        <div class="topbar-title">
          <h2>{{ pageTitle }}</h2>
          <span class="version-badge">Vue 重构版</span>
          <span class="muted">智能航空客服系统 · 会员端</span>
        </div>
        <div class="topbar-user">
          <div class="avatar">{{ (member && member.name ? member.name[0] : '客') }}</div>
          <div class="user-meta">
            <div class="user-name">{{ member?.name || '会员' }}</div>
            <div class="user-level">{{ member?.member_id }} · {{ member?.level || '' }}</div>
          </div>
        </div>
      </header>
      <main class="client-content">
        <router-view :member="member" />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

defineProps({ member: Object })
defineEmits(['logout'])
const route = useRoute()
const titles = {
  '/chat': 'AI 客服对话',
  '/flights': '机票预订',
  '/checkin': '值机选座',
  '/boardpass': '电子登机牌',
  '/orders': '我的订单',
  '/profile': '个人中心'
}
const pageTitle = computed(() => titles[route.path] || '智能航空客服')
</script>

<style scoped>
.client-shell { display: flex; height: 100vh; overflow: hidden; }
.client-sidebar {
  width: 210px; flex: none; background: #0b1e3f; color: #cbd5e1;
  display: flex; flex-direction: column; padding: 16px 10px;
}
.side-brand { display: flex; gap: 10px; align-items: center; padding: 6px 10px 20px; }
.brand-badge {
  width: 40px; height: 40px; border-radius: 12px; background: linear-gradient(145deg,#2563eb,#3b82f6);
  display: flex; align-items: center; justify-content: center; font-size: 20px; color: #fff;
}
.brand-name { color: #fff; font-weight: 700; font-size: 15px; }
.brand-sub { font-size: 11px; color: #94a3b8; margin-top: 2px; }
.side-nav { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.nav-link {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 9px;
  color: #cbd5e1; text-decoration: none; font-size: 14px;
}
.nav-link:hover { background: rgba(255,255,255,.06); color: #fff; }
.nav-link.active { background: var(--primary); color: #fff; font-weight: 600; }
.nav-ico { width: 20px; text-align: center; }
.side-foot { padding-top: 10px; }
.client-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.client-topbar {
  height: 58px; flex: none; background: #fff; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
}
.topbar-title h2 { font-size: 16px; }
.topbar-title span { font-size: 12px; margin-left: 8px; }
.version-badge {
  display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px;
  background: #dcfce7; color: #15803d; font-size: 11px; font-weight: 600;
}
.topbar-user { display: flex; align-items: center; gap: 10px; }
.avatar {
  width: 34px; height: 34px; border-radius: 50%; background: var(--primary); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 600;
}
.user-name { font-weight: 600; font-size: 13px; }
.user-level { font-size: 11.5px; color: var(--text-muted); }
.client-content { flex: 1; min-height: 0; overflow: auto; padding: 20px; }
</style>
