<template>
  <div class="client-shell">
    <aside class="client-sidebar" :class="{ open: navOpen }">
      <div class="side-brand">
        <div class="brand-badge">✈</div>
        <div>
          <div class="brand-name">智能航空客服</div>
          <div class="brand-sub">让出行更简单</div>
        </div>
      </div>
      <nav class="side-nav" @click="navOpen = false">
        <router-link v-for="item in NAV" :key="item.to" :to="item.to" class="nav-link" active-class="active">
          <span class="nav-ico">{{ item.icon }}</span> {{ item.label }}
        </router-link>
      </nav>
      <div class="side-foot">
        <div class="foot-user">
          <div class="foot-avatar">{{ memberInitial }}</div>
          <div class="foot-meta">
            <div class="foot-name">{{ member?.name || '会员' }}</div>
            <div class="foot-id">{{ member?.member_id }}</div>
          </div>
        </div>
        <button class="logout-btn" @click="logout">退出登录</button>
      </div>
    </aside>
    <div v-if="navOpen" class="nav-mask" @click="navOpen = false"></div>

    <div class="client-main">
      <header class="client-topbar">
        <div class="topbar-left">
          <button class="hamburger" title="菜单" @click="navOpen = true">☰</button>
          <div class="topbar-title">
            <h2>{{ pageTitle }}</h2>
            <span class="muted">智能航空客服系统 · 会员端</span>
          </div>
        </div>
        <div class="topbar-user">
          <div class="avatar">{{ memberInitial }}</div>
          <div class="user-meta">
            <div class="user-name">{{ member?.name || '会员' }}</div>
            <div class="user-level">{{ member?.member_id }} · {{ member?.level || '' }}</div>
          </div>
        </div>
      </header>
      <main class="client-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from '@/client/stores/auth.js'

const NAV = [
  { to: '/chat', icon: '💬', label: 'AI 客服对话', title: 'AI 客服对话' },
  { to: '/flights', icon: '🎫', label: '机票预订', title: '机票预订' },
  { to: '/checkin', icon: '🪑', label: '值机选座', title: '值机选座' },
  { to: '/boardpass', icon: '🎟', label: '登机牌', title: '电子登机牌' },
  { to: '/orders', icon: '📦', label: '我的订单', title: '我的订单' },
  { to: '/profile', icon: '👤', label: '个人中心', title: '个人中心' }
]

const { state, logout } = useAuth()
const member = computed(() => state.member)
const memberInitial = computed(() => (member.value && member.value.name ? member.value.name[0] : '客'))

const route = useRoute()
const pageTitle = computed(() => (NAV.find(n => n.to === route.path) || {}).title || '智能航空客服')

const navOpen = ref(false)
</script>

<style scoped>
.client-shell { display: flex; height: 100vh; overflow: hidden; }

/* ---------- 侧边栏 ---------- */
.client-sidebar {
  width: 232px; flex: none; color: #cbd5e1;
  background: linear-gradient(180deg, #0a1a38 0%, #0d2247 60%, #10295a 100%);
  display: flex; flex-direction: column; padding: 18px 12px;
}
.side-brand { display: flex; gap: 10px; align-items: center; padding: 4px 10px 20px; }
.brand-badge {
  width: 40px; height: 40px; border-radius: 13px; flex: none;
  background: var(--brand-gradient);
  display: flex; align-items: center; justify-content: center; font-size: 20px; color: #fff;
  box-shadow: 0 6px 14px -4px rgba(56, 189, 248, .5);
}
.brand-name { color: #fff; font-weight: 700; font-size: 15px; letter-spacing: .01em; }
.brand-sub { font-size: 11px; color: #8ca3c7; margin-top: 2px; }

.side-nav {
  display: flex; flex-direction: column; justify-content: center; gap: 12px; flex: 1;
}
.nav-link {
  display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-radius: 12px;
  color: #b7c4da; text-decoration: none; font-size: 14px;
  transition: background .15s, color .15s;
}
.nav-link:hover { background: rgba(255, 255, 255, .07); color: #fff; }
.nav-link.active {
  background: var(--brand-gradient); color: #fff; font-weight: 600;
  box-shadow: 0 6px 14px -4px rgba(37, 99, 235, .5);
}
.nav-ico { width: 20px; text-align: center; }

.side-foot { padding-top: 12px; border-top: 1px solid rgba(255, 255, 255, .08); }
.foot-user { display: flex; align-items: center; gap: 10px; padding: 4px 6px 10px; }
.foot-avatar {
  width: 34px; height: 34px; border-radius: 50%; flex: none;
  background: rgba(255, 255, 255, .14); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 14px;
}
.foot-name { color: #fff; font-size: 13px; font-weight: 600; }
.foot-id { font-size: 11px; color: #8ca3c7; }
.logout-btn {
  width: 100%; padding: 8px 0; border: 1px solid rgba(255, 255, 255, .16);
  border-radius: 10px; background: transparent; color: #b7c4da; font-size: 13px;
  transition: all .15s;
}
.logout-btn:hover { background: rgba(255, 255, 255, .08); color: #fff; }

.nav-mask { display: none; }

/* ---------- 主区 ---------- */
.client-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.client-topbar {
  height: 60px; flex: none;
  background: rgba(255, 255, 255, .78); backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 22px;
}
.topbar-left { display: flex; align-items: center; gap: 10px; }
.hamburger {
  display: none; width: 36px; height: 36px; border: 1px solid var(--border);
  border-radius: 10px; background: #fff; color: var(--text-muted); font-size: 15px;
}
.topbar-title h2 { font-size: 16px; letter-spacing: -.01em; }
.topbar-title span { font-size: 12px; margin-left: 8px; }
.topbar-user { display: flex; align-items: center; gap: 10px; }
.avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--brand-gradient); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 600;
  box-shadow: 0 3px 8px -2px rgba(37, 99, 235, .4);
}
.user-name { font-weight: 600; font-size: 13px; }
.user-level { font-size: 11.5px; color: var(--text-muted); }

.client-content { flex: 1; min-height: 0; overflow: auto; padding: 20px; }
/* 聊天页需要占满高度做内部滚动 */
.client-content:has(> .chat-view) { overflow: hidden; display: flex; flex-direction: column; }
.client-content:has(> .chat-view) > .chat-view { flex: 1; }

/* ---------- 响应式：移动端侧边栏抽屉化 ---------- */
@media (max-width: 900px) {
  .client-sidebar {
    position: fixed; left: 0; top: 0; bottom: 0; z-index: 130;
    transform: translateX(-100%); transition: transform .25s var(--ease);
    box-shadow: var(--shadow-lg);
  }
  .client-sidebar.open { transform: translateX(0); }
  .nav-mask {
    display: block; position: fixed; inset: 0; z-index: 125;
    background: rgba(15, 23, 42, .4); backdrop-filter: blur(2px);
  }
  .hamburger { display: block; }
  .topbar-title span { display: none; }
  .user-meta { display: none; }
  .client-content { padding: 12px; }
}
</style>
