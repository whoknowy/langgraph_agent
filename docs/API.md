# 智能航空客服系统 · 多端接入接口文档

> 读者：负责 **安卓 App / 微信小程序 / 独立 Web 前端** 的同学。
> 目标：看完这一份文档，就能不问人地把「登录 → AI 客服对话 → 订票支付 → 值机选座 → 登机牌」整条链路跑通。
> 后端接口不变即本文档不失效；字段有新增会向后兼容，放心按示例解析。

---

## 目录

1. [准备工作](#1-准备工作)
2. [登录与 token（先读这章）](#2-登录与-token先读这章)
3. [接口总表](#3-接口总表)
4. [接口详情](#4-接口详情)
5. [确认卡片 pending_action 详解](#5-确认卡片-pending_action-详解)
6. [三大业务流程手把手教程](#6-三大业务流程手把手教程)
7. [订单状态机](#7-订单状态机)
8. [常见问题与踩坑](#8-常见问题与踩坑)
9. [管理端接口（附录）](#9-管理端接口附录)
10. [文档更新记录](#10-文档更新记录)

---

## 1. 准备工作

### 1.1 服务地址

后端只有一个端口（Flask 5000），所有接口都在它上面：

| 环境 | Base URL | 说明 |
|---|---|---|
| 本机调试 | `http://127.0.0.1:5000` | 后端跑在自己电脑上 |
| 局域网联调 | `http://<后端同学的内网IP>:5000` | 同一 WiFi 下手机直连电脑（Windows 需放行 5000 端口防火墙） |
| 公网联调 | `http://flightagent.nat100.top` | **固定公网地址**（NATAPP 付费隧道 → 后端 5000 端口），同一地址也支持 https；对外演示和多端联调用它 |

> 本文档下面所有示例统一用 `http://127.0.0.1:5000` 占位，实际替换成你的 Base URL 即可。

### 1.2 你只需要懂的四条规则

1. **所有请求和响应都是 JSON**。POST 请求必须带请求头 `Content-Type: application/json`，请求体是 JSON 字符串。
2. **除「登录/注册/演示账号/健康检查」外，所有接口都要带 token**，放在请求头：`Authorization: Bearer <token>`（注意 Bearer 后面有一个空格）。
3. **出错的统一格式**是 HTTP 状态码 + `{"error": "人话原因"}`。状态码含义：400 参数/业务不对、401 没登录或 token 失效、403 没权限、500 服务器内部错误。
4. **金额都是整数元**（如 `720` 表示 720 元），日期格式 `YYYY-MM-DD`，时间格式 `HH:MM`（24 小时制）。

### 1.3 推荐调试工具

- **Apifox / Postman**：图形化调试，把本文档的请求直接抄进去。
- **curl**（Windows 用 Git Bash）：本文示例可直接复制执行。
- 浏览器地址栏：只能测 GET 接口，且没法加 Authorization 头，仅适合先看看通不通。

---

## 2. 登录与 token（先读这章）

### 2.1 token 是什么、为什么用它

后端用 **JWT token** 做身份认证：你登录成功，后端发给你一串字符串（token），之后每个请求你把这串东西放在请求头里带上，后端验一下就知道「你是谁」。

- 它相当于一张**临时通行证**：有效期 7 天，过期作废，重新登录拿新的。
- 安卓/小程序没有浏览器 Cookie 机制，所以统一用 token + 请求头，三个端（Web/安卓/小程序）用法完全一样。

### 2.2 第一步：拿演示账号（可跳过）

```bash
curl http://127.0.0.1:5000/api/demo_accounts
```

响应（无需登录）：

```json
{
  "accounts": [
    { "member_id": "M1000", "name": "张伟", "phone_suffix": "9059", "level": "金卡" },
    { "member_id": "M1001", "name": "李磊", "phone_suffix": "2835", "level": "银卡" }
  ]
}
```

### 2.3 第二步：登录，拿 token

演示账号用「会员号 + 手机号后4位」登录：

```bash
curl -X POST http://127.0.0.1:5000/api/login \
  -H "Content-Type: application/json" \
  -d '{"member_id": "M1000", "phone_suffix": "9059"}'
```

响应：

```json
{
  "member": { "member_id": "M1000", "name": "张伟", "level": "金卡" },
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXAi....",
  "message": "欢迎回来，张伟"
}
```

注册用户用「账号 + 密码」登录（账号可以是会员号或手机号）：

```bash
curl -X POST http://127.0.0.1:5000/api/login \
  -H "Content-Type: application/json" \
  -d '{"account": "M1000", "password": "你的密码"}'
```

失败时返回 `401` + `{"error": "会员号不存在，请核对后重试"}` 之类的人话提示。

### 2.4 第三步：之后每个请求带上 token

把登录拿到的 `token` 原样放进请求头：

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXAi....
```

验证一下（应返回 200 和你的会员信息）：

```bash
curl http://127.0.0.1:5000/api/me -H "Authorization: Bearer 刚才的token"
```

### 2.5 各端代码示例

**微信小程序**：封装一个带 token 的请求函数，全项目都用它。

```javascript
// utils/request.js
const BASE = 'http://flightagent.nat100.top';   // 固定公网地址（本地调试可换成 http://127.0.0.1:5000）
const token = () => wx.getStorageSync('token') || '';

function request(path, { method = 'GET', data = {} } = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + path,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        'Authorization': token() ? ('Bearer ' + token()) : '',
      },
      success(res) {
        if (res.statusCode === 401) {          // token 过期/未登录 → 清掉并跳登录页
          wx.removeStorageSync('token');
          wx.navigateTo({ url: '/pages/login/login' });
          reject(res.data); return;
        }
        (res.statusCode >= 200 && res.statusCode < 300) ? resolve(res.data) : reject(res.data);
      },
      fail: reject,
    });
  });
}
// 登录后保存 token：
// wx.setStorageSync('token', res.token)
module.exports = { request, BASE };
```

**安卓（Kotlin + OkHttp）**：用拦截器统一加 token，全 App 自动生效。

```kotlin
// 构建 OkHttpClient 时加一个拦截器
val client = OkHttpClient.Builder()
    .addInterceptor { chain ->
        val token = TokenStore.token   // 你自己存的 SharedPreferences
        val req = if (token.isNotEmpty())
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        else chain.request()
        chain.proceed(req)
    }
    .connectTimeout(15, TimeUnit.SECONDS)
    .readTimeout(180, TimeUnit.SECONDS)   // 聊天接口 LLM 生成可能较慢，读超时给足
    .build()
```

**Web（浏览器 fetch）**：

```javascript
const res = await fetch(BASE + '/api/my/orders', {
  headers: { 'Authorization': 'Bearer ' + localStorage.getItem('token') },
});
if (res.status === 401) { /* 跳登录页 */ }
```

### 2.6 token 过期怎么办

token 有效期 7 天。过期后所有接口返回 **401**。统一处理方案：在请求封装层拦截 401 → 清除本地 token → 跳转登录页 → 登录成功后存新 token 并重试原请求。不要在每个页面单独处理。

---

## 3. 接口总表

「需登录」= 要带 `Authorization: Bearer <token>`。

| 分类 | 方法 | 路径 | 说明 | 需登录 |
|---|---|---|---|---|
| 账号 | POST | `/api/login` | 登录（尾号模式 / 密码模式） | 否 |
| 账号 | POST | `/api/register` | 注册（成功即自动登录，直接返回 token） | 否 |
| 账号 | GET | `/api/demo_accounts` | 演示账号列表 | 否 |
| 账号 | GET | `/api/me` | 查当前登录人 | 是 |
| 账号 | POST | `/api/logout` | 退出（客户端删 token 即可，此接口主要清 Cookie） | 否 |
| 聊天 | POST | `/api/chat` | **非流式**对话（小程序推荐） | 是 |
| 聊天 | POST | `/api/chat/stream` | **流式**对话（SSE，安卓/Web 可用） | 是 |
| 会话 | GET | `/api/sessions` | 会话列表 | 是 |
| 会话 | GET | `/api/sessions/{id}` | 会话详情（含完整聊天记录） | 是 |
| 会话 | DELETE | `/api/sessions/{id}` | 删除会话 | 是 |
| 会话 | POST | `/api/sessions/{id}/clear` | 清空会话（返回新线程 id） | 是 |
| 会话 | POST | `/api/new_session` | 生成一个本地会话号（可忽略，见 4.3.5） | 是 |
| 订票 | GET | `/api/flights/search` | 客户端航班搜索（出发/到达/日期） | 是 |
| 订票 | GET | `/api/booking_quote` | 订票报价（卡片展示） | 是 |
| 订票 | POST | `/api/book` | 创建订单（待支付） | 是 |
| 订票 | POST | `/api/pay` | 支付（待支付 → 已出票） | 是 |
| 改签 | GET | `/api/change_quote` | 改签报价（新旧航班/差价） | 是 |
| 改签 | POST | `/api/change` | 执行改签 | 是 |
| 退票 | GET | `/api/refund_quote` | 自愿退票报价（手续费/到账） | 是 |
| 退票 | POST | `/api/refund` | 退票（voluntary 即时 / special 人工审核） | 是 |
| 值机 | GET | `/api/checkin/seats` | 座位图 | 是 |
| 值机 | POST | `/api/checkin` | 值机/改座（返回登机牌数据） | 是 |
| 值机 | POST | `/api/checkin/cancel` | 取消值机 | 是 |
| 值机 | GET | `/api/checkin/boardpass` | 登机牌查询 | 是 |
| 数据 | GET | `/api/my/orders` | 我的订单列表 | 是 |
| 数据 | GET | `/api/my/complaints` | 我的投诉列表 | 是 |
| 数据 | GET | `/api/my/notifications` | 我的通知（可只看未读） | 是 |
| 数据 | POST | `/api/my/notifications/read` | 全部通知标记已读 | 是 |
| 系统 | GET | `/api/health` | 健康检查 | 否 |

---

## 4. 接口详情

### 4.1 账号

#### 4.1.1 注册 `POST /api/register`

请求体：

```json
{ "name": "王小明", "phone": "13812345678", "password": "abc123", "email": "a@b.com" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| name | 是 | 姓名，20 字以内 |
| phone | 是 | 11 位手机号，`1` 开头，全系统唯一 |
| password | 是 | 至少 6 位 |
| email | 否 | 须含 @，50 字以内 |

成功响应（**注册即登录**，直接把 token 存起来）：

```json
{
  "member": { "member_id": "M1017", "name": "王小明", "level": "普卡" },
  "token": "eyJhbGci...",
  "message": "注册成功，王小明！你的会员号是 M1017"
}
```

> 提醒用户记下 `member_id`，之后可用它登录。失败返回 400 + `{"error": "该手机号已注册，请直接登录"}`。

#### 4.1.2 登录 `POST /api/login`

见 [第 2 章](#2-登录与-token先读这章)。两种模式二选一：`{member_id, phone_suffix}` 或 `{account, password}`。

#### 4.1.3 当前登录人 `GET /api/me`

成功：`{"member": {"member_id": "M1000", "name": "张伟", "level": "金卡"}}`
未登录/token 失效：`401 {"member": null}` —— **App 启动时调它判断是否已登录**。

### 4.2 聊天（核心功能）

后端是「多智能体客服」：你把用户的话发过去，AI 可能直接回答，也可能发起一张**确认卡片**（订票/退票/改签/选座），卡片数据放在 `pending_action` 字段里，详见 [第 5 章](#5-确认卡片-pending_action-详解)。

#### 4.2.1 非流式 `POST /api/chat`（小程序用这个）

请求体：

```json
{ "message": "帮我查一下明天北京到上海的机票", "session_id": "default" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| message | 是 | 用户输入的话 |
| session_id | 否 | **会话（线程）id**。不传或传 `"default"` = 开启全新会话；传之前响应里返回的 `thread_id` = 继续这个会话（AI 记得之前聊过什么） |

成功响应（等 AI 全部生成完才返回，可能要 10~60 秒，**客户端超时请设 ≥180 秒**）：

```json
{
  "response": "明天北京→上海的部分航班如下：| 航班号 | 航司 | ...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "pending_action": null
}
```

- `response` 是 **Markdown 文本**（可能含表格/列表），客户端自行渲染；不做富文本渲染时至少保留换行。
- **把返回的 `thread_id` 存下来**，用户在这个会话里继续发言时作为 `session_id` 传回来。
- `pending_action` 非空时按第 5 章渲染确认卡片。

#### 4.2.2 流式 `POST /api/chat/stream`（安卓/Web 可用，小程序不支持）

请求体与 `/api/chat` 完全一样。响应是一个 **SSE 流**（`Content-Type: text/event-stream`）：

- 每条消息一行 `data: {JSON}\n\n`；**流结束的标志**是收到一行 `data: [DONE]\n\n`。
- 按顺序可能收到以下事件：

| 事件 | JSON | 含义 |
|---|---|---|
| 文本增量 | `{"content": "明天北京"}` | AI 正在生成的一段文字，**追加**到界面上（注意是增量，不是全量） |
| 工具调用 | `{"tool": {"name": "search_flights", "status": "running"}}` | AI 正在查数据，界面可显示「查询中…」动画 |
| 确认卡片 | `{"pending_action": {...}}` | 流结束前若 AI 发起了确认卡片，会推这个事件 |
| 结束 | `{"done": true, "session_id": "...", "thread_id": "...", "response": "全文", "tools": ["search_flights"]}` | 一轮结束；`response` 是全文（懒得拼增量时直接用它覆盖） |
| 出错 | `{"error": "流式响应中断"}` | 之后紧跟 `[DONE]`，按失败处理 |

安卓读流示例（OkHttp）：

```kotlin
val body = client.newCall(Request.Builder()
    .url("$BASE/api/chat/stream")
    .post("{\"message\":\"你好\",\"session_id\":\"default\"}"
        .toRequestBody("application/json".toMediaType()))
    .header("Authorization", "Bearer $token")
    .build()).execute().body ?: return

BufferedSource source = body.source()
while (source.readUtf8Line()?.also { line ->
    when {
        line == "data: [DONE]" -> return            // 流结束
        line.startsWith("data: ") -> {
            val json = line.removePrefix("data: ")
            // 用 Gson/Fastjson 解析，按 content/tool/pending_action/done/error 分发
        }
    }
}) {} // 逐行读
```

> **微信小程序注意**：`wx.request` 默认拿不到流式中间数据。小程序端请直接用非流式 `/api/chat`（效果一样，只是没有打字机动画）。确要流式需用 `enableChunked: true` + `onChunkReceived`（基础库 2.20+），自行拼接再按 `\n\n` 切分。

### 4.3 会话管理

会话 = 一个独立的聊天线程。**历史记录保存在服务端**，换设备登录后也能看到本人的全部会话。

#### 4.3.1 会话列表 `GET /api/sessions`

```json
{
  "sessions": [
    {
      "session_id": "550e8400-...",
      "title": "北京上海明日机票查询",
      "created_at": "2026-09-07 10:30",
      "message_count": 6,
      "last_user_question": "帮我订那张最便宜的"
    }
  ]
}
```

- `title`：AI 自动生成的会话标题（列表页展示用）；`last_user_question`：最后一句提问（无标题时兜底显示）。

#### 4.3.2 会话详情 `GET /api/sessions/{session_id}`

```json
{
  "session": {
    "session_id": "550e8400-...",
    "created_at": 1757205000.0,
    "conversation_history": [
      { "is_user": true,  "content": "帮我查明天北京到上海的机票", "role": "user" },
      { "is_user": false, "content": "好的，为您找到以下航班...", "role": "assistant" }
    ]
  }
}
```

点历史会话时用它**整体恢复聊天记录**（`is_user` 区分左右气泡）。

#### 4.3.3 删除会话 `DELETE /api/sessions/{session_id}` → `{"message": "会话删除成功"}`

#### 4.3.4 清空会话 `POST /api/sessions/{session_id}/clear`

返回 `{"message": "会话清空成功", "new_thread_id": "新的id"}`。**客户端要把当前会话 id 替换成 `new_thread_id`**（原线程已废弃）。

#### 4.3.5 关于 `/api/new_session`

它只是生成一个随机号存进 Web Cookie，多端接入**可以完全忽略**。你的「新建会话」按钮 = 把当前 `session_id` 变量清空置为 `"default"` 即可，下次发消息后端会自动建新线程并返回新的 `thread_id`。

### 4.4 订票 / 支付 / 改签 / 退票

这一组是**确认卡片对应的真正写库接口**。报价类（`*_quote`）是 GET 幂等的，随便调；下单/支付/改签/退票是 POST。

#### 4.4.1 订票报价 `GET /api/booking_quote?flight_no=CA1061&flight_date=2026-09-08&cabin=经济&passengers=1`

| 参数 | 说明 |
|---|---|
| flight_no | 航班号，如 `CA1061` |
| flight_date | `YYYY-MM-DD` |
| cabin | 只能是 `经济` 或 `商务` |
| passengers | 1~9 整数 |

成功：

```json
{
  "flight_no": "CA1061", "airline": "中国国航", "route": "北京-上海",
  "dep_time": "06:40", "flight_date": "2026-09-08", "cabin": "经济",
  "passengers": 1, "unit_price": 600, "total_amount": 600
}
```

#### 4.4.1.1 航班搜索 `GET /api/flights/search?departure=北京&destination=上海&date=2026-09-08`

供 Vue 客户端机票预订页直查航班列表（与 AI 的 search_flights 工具同一数据源）。需登录。

成功：

```json
{
  "departure": "北京", "destination": "上海", "date": "2026-09-08", "count": 3,
  "flights": [
    { "flight_no": "CA1061", "airline": "中国国航", "dep_time": "06:40", "arr_time": "08:55",
      "aircraft": "A320neo", "date": "2026-09-08", "prices": { "经济": 680, "商务": 1710 } }
  ]
}
```

#### 4.4.2 创建订单 `POST /api/book`

```json
{ "flight_no": "CA1061", "flight_date": "2026-09-08", "cabin": "经济", "passengers": 1 }
```

成功：

```json
{
  "success": true, "order_no": "O4832015", "status": "待支付",
  "flight_no": "CA1061", "airline": "中国国航", "route": "北京-上海",
  "dep_time": "06:40", "flight_date": "2026-09-08", "cabin": "经济",
  "passengers": 1, "unit_price": 600, "total_amount": 600,
  "message": "订单 O4832015 已创建（待支付），金额 600 元"
}
```

> 订单归属自动绑定当前登录会员，**订单号 `order_no` 之后所有操作都靠它**。

#### 4.4.3 支付 `POST /api/pay`

```json
{ "order_no": "O4832015" }
```

成功：`{"success": true, "order_no": "O4832015", "status": "已出票", "message": "..."}`。演示系统的「支付」只是状态流转（待支付 → 已出票），不接真实支付渠道。

#### 4.4.4 改签报价 `GET /api/change_quote?order_no=O4832015&new_flight_no=MU5101&new_date=2026-09-09&new_cabin=经济`

规则：**同航线任意航班、未来日期**，免改签费，只算差价。成功：

```json
{
  "order_no": "O4832015", "passengers": 1,
  "old":  { "flight_no": "CA1061", "date": "2026-09-08", "cabin": "经济", "amount": 600 },
  "new":  { "flight_no": "MU5101", "airline": "东方航空", "route": "北京-上海",
            "date": "2026-09-09", "dep_time": "08:00", "arr_time": "10:15",
            "cabin": "经济", "unit_price": 620, "amount": 620 },
  "fare_diff": 20, "diff_desc": "需补差价 20 元", "change_fee": 0, "message": "..."
}
```

`fare_diff` 正数=用户补差价，负数=退差价，0=无差价。

#### 4.4.5 执行改签 `POST /api/change`

```json
{ "order_no": "O4832015", "new_flight_no": "MU5101", "new_date": "2026-09-09", "new_cabin": "经济" }
```

成功：`{"success": true, "order_no": "...", "status": "已改签", "old": {...}, "new": {...}, "fare_diff": 20, "message": "...原值机已取消，请重新值机"}`

#### 4.4.6 退票报价 `GET /api/refund_quote?order_no=O4832015`

自愿退票按离起飞时间分档收费：

| 距起飞 | 费率 |
|---|---|
| ≥72 小时 | 5% |
| 48~72 小时 | 10% |
| 24~48 小时 | 20% |
| <24 小时 | 30% |

```json
{
  "order_no": "O4832015", "amount": 600, "fee_rate": 0.1,
  "fee_tier": "起飞前48-72小时（收10%）", "fee": 60,
  "predict_amount": 540, "depart_time": "2026-09-08 06:40"
}
```

#### 4.4.7 执行退票 `POST /api/refund`

```json
{ "order_no": "O4832015", "refund_type": "voluntary" }
```

| refund_type | 含义 | 流程 |
|---|---|---|
| `voluntary` | 自愿退票 | 按上表费率**即时退款**，状态直接变「已退款」 |
| `special` | 特殊退票（航班延误/取消等非自愿原因） | 进入「退票中」，**人工审核**，可争取全额退 |

voluntary 成功：`{"success": true, "order_no": "...", "status": "已退款", "refund_amount": 540, "fee": 60, "message": "..."}`
special 成功：状态变「退票中」，message 末尾会带「（特殊退票已受理，人工审核中）」；之后可在「我的订单」里看到状态，管理端驳回后会恢复原状态。

### 4.5 值机选座 / 登机牌

**值机窗口**：起飞前 24 小时开放，起飞前 45 分钟截止。只有「已出票 / 已改签」的订单能值机。值机不改订单状态。

#### 4.5.1 座位图 `GET /api/checkin/seats?flight_no=CA1061&flight_date=2026-09-08&order_no=O4832015`

- `order_no` 可选：带上时，本订单已选的座位会标 `mine: true`。

响应（按舱位分区的排×列）：

```json
{
  "flight_no": "CA1061", "flight_date": "2026-09-08",
  "free": 220, "total": 300,
  "cabins": {
    "商务": [ { "row": "1", "seats": [
        { "seat_no": "1A", "status": "free", "mine": false },
        { "seat_no": "1C", "status": "occupied", "mine": false } ] } ],
    "经济": [ { "row": "31", "seats": [
        { "seat_no": "31A", "status": "free", "mine": false },
        { "seat_no": "31B", "status": "occupied", "mine": false } ] } ]
  }
}
```

> 座位首次生成时**全部为 `free`**，只有真实值机成功才会变为 `occupied`；不再随机模拟预占座位。
>
> `free` / `total` 是该航班当天的**全舱位统计**；客户端如果只展示订单对应舱位，请按实际渲染的舱位自行汇总数量。

布局：商务舱 1~3 排（A C \| D F），经济舱 31~55 排（A B C \| D E F）。`status` 只有 `free` / `occupied` 两种；渲染时只允许点 `free` 的座位。**渲染前先按订单的 cabin 只展示对应舱位**（订单舱位从 `/api/my/orders` 的 `cabin` 字段拿）。

#### 4.5.2 值机 / 改座 `POST /api/checkin`

```json
{ "order_no": "O4832015", "seat_no": "31A" }
```

成功直接返回登机牌数据：

```json
{
  "success": true, "message": "值机成功",
  "order_no": "O4832015", "passenger": "张伟", "member_id": "M1000",
  "airline": "中国国航", "flight_no": "CA1061", "route": "北京 → 上海",
  "flight_date": "2026-09-08", "dep_time": "06:40", "cabin": "经济",
  "seat_no": "31A", "gate": "B11", "boarding_time": "06:10"
}
```

- 对已值机订单换座位 = 改座，旧座位自动释放。
- 常见失败（400）：座位被抢（提示换座）、舱位不符（经济舱订单不能选商务座）、窗口未开放/已截止（`error` 里有原因）。

#### 4.5.3 取消值机 `POST /api/checkin/cancel` → `{"success": true, "released": true, "seat_no": "31A", "message": "..."}`

#### 4.5.4 登机牌 `GET /api/checkin/boardpass?order_no=O4832015`

返回结构同 4.5.2 的成功响应。未值机时 400 `{"error": "订单 O4832015 尚未值机"}`。
登机口规则：管理端在后台指派的登机口**实时生效**（改口后用户再查登机牌就是新口）。

### 4.6 我的数据

#### 4.6.1 我的订单 `GET /api/my/orders`

```json
{
  "member_id": "M1000", "count": 2, "total_amount": 1710,
  "orders": [
    {
      "order_no": "O1889686", "member_id": "M1000",
      "flight": "春秋航空9C1418", "flight_no": "9C1418",
      "route": "北京-西安", "flight_date": "2026-08-30", "dep_time": "19:29",
      "cabin": "经济", "amount": 720, "refund_amount": 720,
      "status": "已退款", "created_at": "2026-08-30",
      "checked_in": false, "checkin_seat": "", "checkin_gate": "", "boarding_time": ""
    }
  ]
}
```

- `checked_in: true` 时 `checkin_seat` / `checkin_gate` / `boarding_time` 有值——订单列表里可直接展示「已值机 31A」。
- 客户端按 `status` 决定给哪些操作按钮，见 [第 7 章状态机](#7-订单状态机)。

#### 4.6.2 我的投诉 `GET /api/my/complaints`

```json
{ "complaints": [ { "ticket_no": "T0007", "member_id": "M1000", "order_no": "O4832015",
    "content": "航班延误 3 小时", "status": "处理中", "reply": "", "created_at": "..." } ], "count": 1 }
```

（投诉通过 AI 客服聊天发起，客户端只做展示。）

#### 4.6.3 我的通知 `GET /api/my/notifications?unread=1`

`unread=1` 只返回未读。响应：

```json
{
  "notifications": [
    { "id": 12, "title": "登机口变更提醒", "content": "您乘坐的 CA1061 登机口变更为 C4",
      "ntype": "gate_change", "is_read": false, "created_at": "2026-09-07 09:00:00" }
  ],
  "unread_count": 1
}
```

通知是**拉取式**（没有推送）：客户端进入首页/打开通知页时拉一次即可，有 `unread_count` 就显示红点。全部已读：`POST /api/my/notifications/read` → `{"success": true, "marked": 1}`。

---

## 5. 确认卡片 pending_action 详解

AI 客服在聊天中替用户发起操作时，**不会直接写数据库**，而是在 `pending_action` 里放一张「待确认卡片」，等用户点了按钮、客户端调对应写接口才真正生效。安全模型：写库永远走 REST + 登录身份，AI 说的不算。

非流式接口在 JSON 的 `pending_action` 字段返回；流式接口在流结束时推 `{"pending_action": {...}}` 事件。没有发起操作时该字段为 `null`，**先判空再解析**。

| type | 发起场景 | 卡片关键字段 | 用户确认后调用 |
|---|---|---|---|
| `book_flight` | 用户想订票，AI 已查到航班 | `flight_no`、`flight_date`、`cabin`、`passengers` | `POST /api/book` |
| `refund` | 用户想退票 | `refund_type`（`voluntary`/`special`）、`order_no` | `POST /api/refund` |
| `change_flight` | 用户想改签 | `order_no`、`new_flight_no`、`new_date`、`new_cabin` | `POST /api/change` |
| `seat_map` | 用户要值机选座 | `order_no`、`flight_no`、`flight_date` | 打开选座面板：`GET /api/checkin/seats` → `POST /api/checkin` |

客户端渲染卡片的标准姿势（以订票为例）：

1. 收到 `pending_action.type == "book_flight"`；
2. 调 `GET /api/booking_quote?flight_no=...&flight_date=...&cabin=...&passengers=...` 拿**服务端实时报价**（不要信卡片里 AI 报的价，报价接口才是准的）；
3. 展示卡片：航班信息 + 单价 × 人数 = 总价 + 「确认预订」「取消」两个按钮；
4. 用户点确认 → `POST /api/book`（参数用卡片字段）→ 成功后提示订单号，并引导支付 `POST /api/pay`；
5. 支付完成可顺带提示「可以去值机了」。退票/改签卡片同理先调对应 `*_quote` 再执行。

> 卡片字段来自 AI 的工具调用参数，个别字段可能缺失或格式不规整（比如人数传了字符串）。**客户端只把它们当「预填值」**，提交前用报价接口校验，业务错误后端会返回 400 + 人话提示，展示给用户即可。

---

## 6. 三大业务流程手把手教程

### 流程 A：聊天 → 订票 → 支付（最核心）

```
用户输入"帮我订明天北京到上海的机票"（session_id 传 "default" 或不传）
   ↓ POST /api/chat
AI 返回航班列表（Markdown 表格），用户说"订最便宜的 CA1061 经济舱 1 人"
   ↓ POST /api/chat （session_id 用上一步返回的 thread_id）
本轮响应 pending_action = {"type":"book_flight","flight_no":"CA1061",...}
   ↓ 渲染确认卡片；先 GET /api/booking_quote 拿实时总价
用户点"确认预订"
   ↓ POST /api/book  → 拿到 order_no，status=待支付
引导支付
   ↓ POST /api/pay   → status=已出票
完成。提示用户可在"我的订单"里值机
```

### 流程 B：值机选座 → 登机牌

```
"我的订单"里找到 status ∈ {已出票, 已改签} 且 checked_in=false 的订单
   ↓ 检查值机窗口：起飞前 24h ~ 45min（服务端也会校验，早了会 400 提示）
   ↓ GET /api/checkin/seats?flight_no=...&flight_date=...&order_no=...
渲染座位图（只渲染订单 cabin 对应舱位；free 可点）
   ↓ 用户选座 31A
POST /api/checkin {order_no, seat_no:"31A"}
   ↓ 成功 → 响应里就是登机牌数据（seat_no/gate/boarding_time/passenger...）
渲染登机牌卡片；之后随时 GET /api/checkin/boardpass?order_no=... 重查
```

### 流程 C：改签 / 退票

```
改签：改签卡片确认后 → GET /api/change_quote（展示新旧航班+差价）→ POST /api/change
      成功后原值机被自动取消，status=已改签，需要重新值机
自愿退票：GET /api/refund_quote（展示手续费档位+到账金额）→ POST /api/refund {refund_type:"voluntary"}
          即时到账，status=已退款
特殊退票（延误/取消）：POST /api/refund {refund_type:"special"} → status=退票中，等人工审核
```

---

## 7. 订单状态机

```
                    ┌────────────（管理端审批通过）──────────→ 已退款
待支付 ──支付──→ 已出票 ──改签──→ 已改签 ──特殊退票──→ 退票中 ──┤
   │               │  │          │  ▲                   └──（驳回）──→ 恢复原状态
   │（下单15分钟   │  └─特殊退票──┘  └──重新值机（值机不改状态）
   │  未支付自动   └─自愿退票──→ 已退款
   │  取消）
   └──→ 已取消
任何状态：起飞时间一过 ──→ 已使用（后端定时任务自动改）
```

> 待支付订单**超过 15 分钟未支付会自动取消**（并收到站内通知），客户端在订票成功页应倒计时提醒用户尽快支付。

客户端按钮建议：

| status | 可展示的操作 |
|---|---|
| 待支付 | 去支付（`/api/pay`，**15 分钟内有效**，超时自动取消） |
| 已出票 | 值机/选座（未值机）、查看登机牌（已值机）、改签、自愿退票、特殊退票 |
| 已改签 | 重新值机、改签、退票 |
| 退票中 | 只读（等待审核） |
| 已退款 / 已取消 / 已使用 | 只读 |

---

## 8. 常见问题与踩坑

| 问题 | 原因与解法 |
|---|---|
| 所有接口都 401 | 请求头没带对：必须是 `Authorization: Bearer <token>`（Bearer 后有空格）；或 token 过期 → 重新登录 |
| POST 报 400「消息不能为空」之类 | 忘了 `Content-Type: application/json`，后端根本没解析到 body |
| 小程序真机请求全部失败 | 正式包/真机要求 HTTPS + 已配置的合法域名。开发期在开发者工具勾「不校验合法域名」，真机预览打开调试模式；`flightagent.nat100.top` 是 NATAPP 付费固定域名（主域已备案），正式包可在小程序后台把 `https://flightagent.nat100.top` 配为 request 合法域名（能否通过以公众平台实际校验为准） |
| 聊天接口 10 秒就超时 | LLM 生成慢，客户端读超时设 **180 秒**以上（安卓 OkHttp `readTimeout`） |
| 流式收到一堆 `data: {...}` 不知道怎么结束 | 收到 `data: [DONE]` 这一行才算结束；`done: true` 事件里带全文和 thread_id |
| 聊天有回复但没弹确认卡片 | `pending_action` 为 null 是正常的（AI 只是回答了没发起操作）；有卡片时非流式看 `pending_action` 字段、流式看 `pending_action` 事件 |
| 选座一直报「刚刚被其他乘客抢占」 | 说明同一航班该座位刚被真实值机占用（座位是原子占座的），重新选一个即可 |
| 报「值机尚未开放」 | 起飞前 24 小时才开放值机，测试时请选 24 小时内起飞的航班（后端同学可造测试数据） |
| 下单后过一会订单变「已取消」 | 正常：待支付超过 15 分钟被后端定时任务自动取消，重新下单即可 |
| 传了别人的订单号 | 返回 400/403「订单不属于当前登录会员」——这是权限校验，不是 bug |
| Markdown 表格显示成乱码 | `response` 是 Markdown，安卓可用 Markwon、小程序用 towxml 之类渲染；不渲染时至少按 `\n` 换行展示 |
| Web 前端跨域报错 | 后端默认同源部署（页面和接口同域名），没开 CORS。若你的前端部署在别的域名，联系后端同学加 `flask-cors` |
| 金额显示 720.0 | 后端金额是整数元，直接按整数展示「¥720」 |

---

## 9. 管理端接口（附录）

多端同学一般不碰管理端，这里只列概要。管理端走 `/admin/api/*`，登录后同样发 token（`typ` 为 admin），用法与会员 token 相同：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/api/login` | 管理员登录 |
| GET | `/admin/api/me` | 当前管理员（含 must_change_password） |
| POST | `/admin/api/change_password` | 修改自己密码（首次登录强制） |
| GET | `/admin/api/stats`、`/admin/api/stats/trend` | 运营统计 / 7天趋势+热门航线 |
| GET | `/admin/api/refunds` + POST approve/reject | 特殊退票审批 |
| GET | `/admin/api/complaints` + POST resolve/escalate/reopen | 投诉处理 |
| GET/POST | `/admin/api/flights`、`/admin/api/flights/gate` | 航班管理 / 登机口指派 |
| GET | `/admin/api/orders`、`/admin/api/customers` | 订单 / 客户查询 |

**首次登录强制改密**：登录后调除 `me`/`change_password` 外的接口都会 403 `{"error": "首次登录必须先修改密码", "must_change_password": true}` —— 客户端检测到 `must_change_password: true` 时弹出改密页，走 `/admin/api/change_password`（body: `{old_password, new_password}`，新密码 ≥8 位且不能是常见弱口令）。

---

## 10. 文档更新记录

- 2026-09-09：座位图改为「初始全部可选、真实值机后才占用」，不再随机模拟预占座位；补充 `free` / `total` 为全舱位统计说明。
- 2026-09-07：新增 `GET /api/flights/search` 客户端航班搜索接口（已写入接口总表与 4.4.1.1）。
