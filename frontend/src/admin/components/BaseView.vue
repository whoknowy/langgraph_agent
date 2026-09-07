<template>
  <div>
    <h2 class="page-title">机场 · 航司维护</h2>
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>机场列表</template>
          <div class="inline-form">
            <el-input v-model="airportForm.iata3" placeholder="三字码 PEK" style="width: 110px" />
            <el-input v-model="airportForm.city_cn" placeholder="城市中文" style="width: 110px" />
            <el-input v-model="airportForm.city_en" placeholder="City" style="width: 110px" />
            <el-input-number v-model="airportForm.lat" :precision="2" :controls="false" placeholder="纬度" style="width: 110px" />
            <el-input-number v-model="airportForm.lon" :precision="2" :controls="false" placeholder="经度" style="width: 110px" />
            <el-button type="primary" @click="addAirport">添加</el-button>
          </div>
          <el-table :data="airports" v-loading="loading" max-height="420" style="margin-top: 12px">
            <el-table-column prop="iata3" label="三字码" width="90" />
            <el-table-column prop="city_cn" label="城市中文" width="110" />
            <el-table-column prop="city_en" label="城市英文" min-width="120" />
            <el-table-column prop="lat" label="纬度" width="90" />
            <el-table-column prop="lon" label="经度" width="90" />
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>航司列表</template>
          <div class="inline-form">
            <el-input v-model="airlineForm.code" placeholder="二字码 CA" style="width: 110px" />
            <el-input v-model="airlineForm.name_cn" placeholder="航司名称" style="width: 160px" />
            <el-checkbox v-model="airlineForm.is_lcc">廉价</el-checkbox>
            <el-button type="primary" @click="addAirline">添加</el-button>
          </div>
          <el-table :data="airlines" v-loading="loading" max-height="420" style="margin-top: 12px">
            <el-table-column prop="code" label="二字码" width="90" />
            <el-table-column prop="name_cn" label="名称" min-width="160" />
            <el-table-column label="类型" width="120">
              <template #default="{ row }">{{ row.is_lcc ? '廉价航空' : '全服务' }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'
import { toastError } from '../../ui.js'

const airports = ref([])
const airlines = ref([])
const loading = ref(false)
const airportForm = ref({ iata3: '', city_cn: '', city_en: '', lat: 0, lon: 0 })
const airlineForm = ref({ code: '', name_cn: '', is_lcc: false })

onMounted(loadAll)

async function loadAll() {
  loading.value = true
  try {
    const [a, l] = await Promise.all([
      api('/admin/api/airports', { admin: true }),
      api('/admin/api/airlines', { admin: true })
    ])
    airports.value = a.airports || []
    airlines.value = l.airlines || []
  } catch (e) { console.warn(e) } finally { loading.value = false }
}

async function addAirport() {
  try {
    await api('/admin/api/airports', { method: 'POST', body: { ...airportForm.value }, admin: true })
    airportForm.value = { iata3: '', city_cn: '', city_en: '', lat: 0, lon: 0 }
    await loadAll()
  } catch (e) { toastError(e.message) }
}

async function addAirline() {
  try {
    await api('/admin/api/airlines', { method: 'POST', body: { ...airlineForm.value }, admin: true })
    airlineForm.value = { code: '', name_cn: '', is_lcc: false }
    await loadAll()
  } catch (e) { toastError(e.message) }
}
</script>

<style scoped>
.inline-form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
</style>
