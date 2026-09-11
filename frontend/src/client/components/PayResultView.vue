<template>
  <div>
    <h2 class="page-title">支付结果</h2>

    <div class="card pay-result">
      <div v-if="loading" class="muted">正在确认支付结果…</div>

      <template v-else-if="status">
        <div v-if="status.paid" class="result-line">
          <span class="result-icon ok">✓</span>
          <span>支付成功，订单已出票</span>
        </div>
        <div v-else class="result-line">
          <span class="result-icon wait">…</span>
          <span>尚未收到支付成功通知（订单当前：{{ status.order_status }}）</span>
        </div>

        <div class="result-meta">
          <div>订单号 <strong>{{ status.order_no }}</strong></div>
          <div>金额 <strong>¥{{ status.amount }}</strong></div>
          <div v-if="status.pay_no">支付流水 {{ status.pay_no }}（{{ status.pay_status }}）</div>
        </div>

        <div class="result-actions">
          <button class="btn btn-sm" @click="check">重新查询</button>
          <button class="btn btn-primary btn-sm" @click="router.push('/orders')">查看我的订单</button>
        </div>
        <p v-if="!status.paid" class="muted tip">
          若已完成付款，异步通知可能稍后才到，稍等几秒点「重新查询」即可；
          一直未到账请联系客服，切勿重复付款。
        </p>
      </template>

      <div v-else class="muted">{{ error || '缺少订单号，无法查询支付结果' }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, qs } from '../../api.js'

const route = useRoute()
const router = useRouter()
const status = ref(null)
const loading = ref(true)
const error = ref('')

onMounted(check)

async function check() {
  const orderNo = route.query.order_no
  if (!orderNo) {
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    status.value = await api('/api/pay/status' + qs({ order_no: orderNo }))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.pay-result { padding: 20px; }
.result-line { display: flex; align-items: center; gap: 10px; font-size: 16px; margin-bottom: 14px; }
.result-icon {
  width: 26px; height: 26px; border-radius: 50%; display: inline-flex;
  align-items: center; justify-content: center; color: #fff; font-size: 15px;
}
.result-icon.ok { background: #2f9e6f; }
.result-icon.wait { background: #9a8f7a; }
.result-meta { color: #5c5850; font-size: 14px; line-height: 1.9; margin-bottom: 16px; }
.result-actions { display: flex; gap: 10px; }
.tip { margin-top: 14px; font-size: 13px; line-height: 1.7; }
</style>
