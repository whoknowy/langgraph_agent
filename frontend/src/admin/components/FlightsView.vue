<template>
  <div>
    <h2 class="page-title">航班管理</h2>
    <el-card shadow="never" style="margin-bottom: 16px">
      <template #header>新增航班（自动生成未来30天票价）</template>
      <el-form :model="form" label-width="110px" style="max-width: 900px">
        <el-row :gutter="16">
          <el-col :span="8"><el-form-item label="航班号"><el-input v-model="form.flight_no" placeholder="CA1999" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="航司"><el-select v-model="form.airline_code" placeholder="选择航司" style="width: 100%"><el-option v-for="a in airlines" :key="a.code" :label="a.name_cn + ' (' + a.code + ')'" :value="a.code" /></el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="出发机场"><el-select v-model="form.dep_iata" placeholder="选择机场" style="width: 100%"><el-option v-for="a in airports" :key="a.iata3" :label="a.city_cn + ' (' + a.iata3 + ')'" :value="a.iata3" /></el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="到达机场"><el-select v-model="form.arr_iata" placeholder="选择机场" style="width: 100%"><el-option v-for="a in airports" :key="a.iata3" :label="a.city_cn + ' (' + a.iata3 + ')'" :value="a.iata3" /></el-select></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="起飞"><el-input v-model="form.dep_time" placeholder="08:00" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="到达"><el-input v-model="form.arr_time" placeholder="10:15" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="经济舱基准价"><el-input-number v-model="form.econ_price" :min="100" :max="20000" :step="10" style="width: 100%" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="执飞日"><el-input v-model="form.freq_days" placeholder="1234567" /></el-form-item></el-col>
          <el-col :span="8"><el-form-item label="机型"><el-input v-model="form.aircraft" placeholder="A320" /></el-form-item></el-col>
        </el-row>
        <el-form-item>
          <el-button type="primary" :loading="creating" @click="create">{{ creating ? '创建中…' : '上架航班' }}</el-button>
          <span v-if="msg" class="success-text" style="margin-left: 12px">{{ msg }}</span>
          <span v-if="err" class="error-text" style="margin-left: 12px">{{ err }}</span>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div style="display: flex; gap: 10px; align-items: center">
          <el-input v-model="q" placeholder="搜索航班/城市/航司" clearable style="width: 260px" @keyup.enter="load" />
          <el-button @click="load">搜索</el-button>
        </div>
      </template>
      <el-table :data="flights" v-loading="loading" style="width: 100%">
        <el-table-column prop="flight_no" label="航班号" width="110" />
        <el-table-column prop="airline" label="航司" min-width="120" />
        <el-table-column label="航线" min-width="180">
          <template #default="{ row }">{{ row.dep_city }}({{ row.dep_iata }}) - {{ row.arr_city }}({{ row.arr_iata }})</template>
        </el-table-column>
        <el-table-column label="时刻" width="140">
          <template #default="{ row }">{{ row.dep_time }} - {{ row.arr_time }}</template>
        </el-table-column>
        <el-table-column prop="aircraft" label="机型" width="110" />
        <el-table-column prop="freq_days" label="连班日" width="100" />
        <el-table-column label="登机口" width="100">
          <template #default="{ row }">{{ row.gate || '-' }}</template>
        </el-table-column>
        <el-table-column label="最低经济舱" width="120">
          <template #default="{ row }">¥{{ row.econ_price_from || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }"><el-button size="small" @click="assignGate(row)">指派登机口</el-button></template>
        </el-table-column>
        <template #empty><el-empty description="暂无航班" /></template>
      </el-table>
    </el-card>
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
const loading = ref(false)
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
  loading.value = true
  try {
    const d = await api('/admin/api/flights' + (q.value ? '?q=' + encodeURIComponent(q.value) : ''), { admin: true })
    flights.value = d.flights || []
  } catch (e) { alert(e.message) } finally { loading.value = false }
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
