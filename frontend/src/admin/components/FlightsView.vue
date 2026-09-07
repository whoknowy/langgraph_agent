<template>
  <div>
    <h2 class="page-title">航班管理</h2>
    <div class="card" style="margin-bottom:16px">
      <div class="section-title">新增航班（自动生成未来30天票价）</div>
      <div class="form-grid">
        <div class="form-field"><label>航班号 *</label><input v-model="form.flight_no" placeholder="CA1999" /></div>
        <div class="form-field"><label>航司 *</label><select v-model="form.airline_code"><option value="">选择航司</option><option v-for="a in airlines" :key="a.code" :value="a.code">{{ a.name_cn }} ({{ a.code }})</option></select></div>
        <div class="form-field"><label>出发机场 *</label><select v-model="form.dep_iata"><option value="">选择机场</option><option v-for="a in airports" :key="a.iata3" :value="a.iata3">{{ a.city_cn }} ({{ a.iata3 }})</option></select></div>
        <div class="form-field"><label>到达机场 *</label><select v-model="form.arr_iata"><option value="">选择机场</option><option v-for="a in airports" :key="a.iata3" :value="a.iata3">{{ a.city_cn }} ({{ a.iata3 }})</option></select></div>
        <div class="form-field"><label>起飞 *</label><input v-model="form.dep_time" placeholder="08:00" /></div>
        <div class="form-field"><label>到达 *</label><input v-model="form.arr_time" placeholder="10:15" /></div>
        <div class="form-field"><label>经济舱基准价 *</label><input v-model.number="form.econ_price" type="number" placeholder="600" /></div>
        <div class="form-field"><label>执飞日</label><input v-model="form.freq_days" placeholder="1234567" /></div>
        <div class="form-field"><label>机型</label><input v-model="form.aircraft" placeholder="A320" /></div>
      </div>
      <div class="form-actions">
        <button class="btn btn-primary" :disabled="creating" @click="create">{{ creating ? '创建中…' : '上架航班' }}</button>
        <span v-if="msg" class="success-text">{{ msg }}</span>
        <span v-if="err" class="error-text">{{ err }}</span>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <input v-model="q" placeholder="搜索航班/城市/航司" @keyup.enter="load" />
        <button class="btn" @click="load">搜索</button>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>航班号</th><th>航司</th><th>航线</th><th>时刻</th><th>机型</th><th>连班日</th><th>登机口</th><th>最低经济舱</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in flights" :key="f.flight_no">
            <td><strong>{{ f.flight_no }}</strong></td>
            <td>{{ f.airline }}</td>
            <td>{{ f.dep_city }}({{ f.dep_iata }}) - {{ f.arr_city }}({{ f.arr_iata }})</td>
            <td>{{ f.dep_time }} - {{ f.arr_time }}</td>
            <td>{{ f.aircraft }}</td>
            <td>{{ f.freq_days }}</td>
            <td>{{ f.gate || '-' }}</td>
            <td>¥{{ f.econ_price_from || '-' }}</td>
            <td><button class="btn btn-sm" @click="assignGate(f)">指派登机口</button></td>
          </tr>
          <tr v-if="!flights.length"><td colspan="9" class="empty">暂无航班</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const flights = ref([])
const airlines = ref([])
const airports = ref([])
const q = ref('')
const creating = ref(false)
const msg = ref('')
const err = ref('')
const form = ref({
  flight_no: '', airline_code: '', dep_iata: '', arr_iata: '',
  dep_time: '08:00', arr_time: '10:15', econ_price: 600, freq_days: '1234567', aircraft: 'A320'
})

onMounted(async () => {
  await load()
  try {
    const d = await api('/admin/api/airlines', { admin: true })
    airlines.value = d.airlines || []
  } catch (e) { console.warn(e) }
  try {
    const d = await api('/admin/api/airports', { admin: true })
    airports.value = d.airports || []
  } catch (e) { console.warn(e) }
})

async function load() {
  try {
    const d = await api('/admin/api/flights' + (q.value ? '?q=' + encodeURIComponent(q.value) : ''), { admin: true })
    flights.value = d.flights || []
  } catch (e) { alert(e.message) }
}

async function create() {
  creating.value = true
  msg.value = ''
  err.value = ''
  try {
    const d = await api('/admin/api/flights', { method: 'POST', body: { ...form.value }, admin: true })
    msg.value = d.message || '创建成功'
    form.value.flight_no = ''
    await load()
  } catch (e) {
    err.value = e.message
  } finally {
    creating.value = false
  }
}

async function assignGate(f) {
  const gate = prompt('为 ' + f.flight_no + ' 指派登机口', f.gate || '')
  if (gate === null) return
  try {
    const d = await api('/admin/api/flights/gate', { method: 'POST', body: { flight_no: f.flight_no, gate }, admin: true })
    alert(d.message || '已指派')
    await load()
  } catch (e) { alert(e.message) }
}
</script>

<style scoped>
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.form-actions { margin-top: 14px; display: flex; gap: 10px; align-items: center; }
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.toolbar input { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; outline: none; }
</style>