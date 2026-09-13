import { createRouter, createWebHashHistory } from 'vue-router'
import ClientLayout from './components/ClientLayout.vue'
import ChatView from './components/chat/ChatView.vue'
import FlightSearchView from './components/FlightSearchView.vue'
import CheckinView from './components/CheckinView.vue'
import BoardingPassView from './components/BoardingPassView.vue'
import ProfileView from './components/ProfileView.vue'
import MyOrdersView from './components/MyOrdersView.vue'
import PayResultView from './components/PayResultView.vue'

// 注意：后端支付同步回调 302 到 /#/pay/result?...，hash 路由路径不能改
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      component: ClientLayout,
      children: [
        { path: '', redirect: '/chat' },
        { path: 'chat', component: ChatView },
        { path: 'flights', component: FlightSearchView },
        { path: 'checkin', component: CheckinView },
        { path: 'boardpass', component: BoardingPassView },
        { path: 'profile', component: ProfileView },
        { path: 'orders', component: MyOrdersView },
        { path: 'pay/result', component: PayResultView }
      ]
    }
  ]
})

export default router
