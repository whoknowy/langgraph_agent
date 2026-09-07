<template>
  <div class="admin-shell">
    <aside class="admin-sidebar">
      <div class="admin-brand">运营管理平台<small>多智能体航空客服系统</small></div>
      <nav class="admin-nav">
        <router-link to="/dashboard" class="nav-link" active-class="active">📊 工作台</router-link>
        <router-link to="/refunds" class="nav-link" active-class="active">💰 退款处理</router-link>
        <router-link to="/complaints" class="nav-link" active-class="active">📝 投诉处理</router-link>
        <router-link to="/flights" class="nav-link" active-class="active">✈️ 航班管理</router-link>
        <router-link to="/orders" class="nav-link" active-class="active">📦 订单查询</router-link>
        <router-link to="/base" class="nav-link" active-class="active">🏢 机场·航司</router-link>
        <router-link to="/members" class="nav-link" active-class="active">👥 会员</router-link>
      </nav>
      <div class="admin-foot">
        <div class="admin-who">{{ admin?.name || admin?.username }}</div>
        <button class="btn btn-sm" @click="$emit('logout')">退出登录</button>
      </div>
    </aside>
    <div class="admin-main">
      <main class="admin-content">
        <router-view :admin="admin" />
      </main>
    </div>
  </div>
</template>

<script setup>
defineProps({ admin: Object })
defineEmits(['logout'])
</script>

<style scoped>
.admin-shell { display: flex; height: 100vh; overflow: hidden; }
.admin-sidebar {
  width: 200px; flex: none; background: #1e293b; color: #cbd5e1;
  display: flex; flex-direction: column; padding: 16px 10px;
}
.admin-brand { padding: 6px 10px 16px; font-size: 15px; font-weight: 700; color: #fff; }
.admin-brand small { display: block; font-size: 11px; font-weight: 400; color: #94a3b8; margin-top: 3px; }
.admin-nav { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.nav-link { padding: 10px 12px; border-radius: 8px; color: #cbd5e1; text-decoration: none; font-size: 13.5px; }
.nav-link:hover { background: rgba(255,255,255,.06); color: #fff; }
.nav-link.active { background: var(--primary); color: #fff; font-weight: 600; }
.admin-foot { padding: 10px; font-size: 12px; color: #94a3b8; }
.admin-who { margin-bottom: 8px; }
.admin-main { flex: 1; min-width: 0; overflow: auto; background: var(--bg); }
.admin-content { padding: 20px 24px; }
</style>