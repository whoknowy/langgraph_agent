import { createRouter, createWebHashHistory } from 'vue-router'
import AdminLayout from './components/AdminLayout.vue'
import DashboardView from './components/DashboardView.vue'
import RefundsView from './components/RefundsView.vue'
import ComplaintsView from './components/ComplaintsView.vue'
import FlightsView from './components/FlightsView.vue'
import OrdersView from './components/OrdersView.vue'
import BaseView from './components/BaseView.vue'
import MembersView from './components/MembersView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', component: DashboardView },
        { path: 'refunds', component: RefundsView },
        { path: 'complaints', component: ComplaintsView },
        { path: 'flights', component: FlightsView },
        { path: 'orders', component: OrdersView },
        { path: 'base', component: BaseView },
        { path: 'members', component: MembersView }
      ]
    }
  ]
})

export default router
