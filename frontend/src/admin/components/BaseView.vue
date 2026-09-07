<template>
  <div>
    <h2 class="page-title">机场 · 航司维护</h2>
    <div class="two-col">
      <div class="card">
        <div class="section-title">机场列表</div>
        <div class="toolbar">
          <div class="inline-form">
            <input v-model="airportForm.iata3" placeholder="三字码 PEK" style="width:90px" />
            <input v-model="airportForm.city_cn" placeholder="城市中文" style="width:100px" />
            <input v-model="airportForm.city_en" placeholder="City" style="width:100px" />
            <input v-model.number="airportForm.lat" type="number" placeholder="纬度" style="width:80px" />
            <input v-model.number="airportForm.lon" type="number" placeholder="经度" style="width:80px" />
            <button class="btn btn-sm" @click="addAirport">添加</button>
          </div>
        </div>
        <table class="data-table">
          <thead><tr><th>三字码</th><th>城市中文</th><th>城市英文</th><th>纬度</th><th>经度</th></tr></thead>
          <tbody>
            <tr v-for="a in airports" :key="a.iata3"><td>{{ a.iata3 }}</td><td>{{ a.city_cn }}</td><td>{{ a.city_en }}</td><td>{{ a.lat }}</td><td>{{ a.lon }}</td></tr>
            <tr v-if="!airports.length"><td colspan="5" class="empty">暂无机场</td></tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="section-title">航司列表</div>
        <div class="toolbar">
          <div class="inline-form">
            <input v-model="airlineForm.code" placeholder="二字码 CA" style="width:90px" />
            <input v-model="airlineForm.name_cn" placeholder="航司名称" style="width:140px" />
            <label class="lcc"><input type="checkbox" v-model="airlineForm.is_lcc" /> 廉价</label>
            <button class="btn btn-sm" @click="addAirline">添加</button>
          </div>
        </div>
        <table class="data-table">
          <thead><tr><th>二字码</th><th>名称</th><th>类型</th></tr></thead>
          <tbody>
            <tr v-for="a in airlines" :key="a.code"><td>{{ a.code }}</td><td>{{ a.name_cn }}</td><td>{{ a.is_lcc ? '廉价航空' : '全服务' }}</td></tr>
            <tr v-if="!airlines.length"><td colspan="3" class="empty">暂无航司</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api.js'

const airports = ref([])
const airlines = ref([])
const airportForm = ref({ iata3: '', city_cn: '', city_en: '', lat: 0, lon: 0 })
const airlineForm = ref({ code: '', name_cn: '', is_lcc: false })

onMounted(loadAll)

async function loadAll() {
  try { airports.value = (await api('/admin/api/airports', { admin: true })).airports || [] } catch (e) { console.warn(e) }
  try { airlines.value = (await api('/admin/api/airlines', { admin: true })).airlines || [] } catch (e) { console.warn(e) }
}

async function addAirport() {
  try {
    await api('/admin/api/airports', { method: 'POST', body: { ...airportForm.value }, admin: true })
    airportForm.value = { iata3: '', city_cn: '', city_en: '', lat: 0, lon: 0 }
    await loadAll()
  } catch (e) { alert(e.message) }
}

async function addAirline() {
  try {
    await api('/admin/api/airlines', { method: 'POST', body: { ...airlineForm.value }, admin: true })
    airlineForm.value = { code: '', name_cn: '', is_lcc: false }
    await loadAll()
  } catch (e) { alert(e.message) }
}
</script>

<style scoped>
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.toolbar { margin-bottom: 10px; }
.inline-form { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.inline-form input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 7px; outline: none; }
.lcc { font-size: 12px; display: flex; align-items: center; gap: 4px; }
</style>