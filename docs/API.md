# 智能航空客服系统 · 多端接入接口文档

> 读者：负责 **安卓 App / 微信小程序 / 独立 Web 前端** 的同学。
> 目标：看完这一份文档，就能不问人地把「登录 → AI 客服对话 → 订票支付 → 值机选座 → 登机牌」整条链路跑通。
> 后端接口不变即本文档不失效；字段有新增会向后兼容，放心按示例解析。
>
> **要接支付的同学请转 [PAYMENT_HANDOFF.md](PAYMENT_HANDOFF.md)**：
> 那份文档专门讲支付（三个认知、时序、安卓/小程序完整代码、边界情况、自测清单），
> 本文档的 4.4.3 只作接口字段速查。

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
| 订票 | GET | `/api/flights/search` | 客户端航班搜索（出发/到达/日期，**只返回未起飞航班**，见 4.4.1.1） | 是 |
| 订票 | GET | `/api/booking_quote` | 订票报价（卡片展示） | 是 |
| 订票 | POST | `/api/book` | 创建订单（待支付） | 是 |
| 订票 | POST | `/api/pay` | 支付（模拟渠道，一步付讫） | 是 |
| 支付 | POST | `/api/pay/create` | 发起支付（返回收银台地址/表单） | 是 |
| 支付 | GET | `/api/pay/gateway/{pay_no}` | 收银台中转页（自动 POST 到渠道，**无需登录**） | 否 |
| 支付 | POST | `/api/pay/confirm` | 站内确认支付（模拟渠道用，**body 需带 `confirm_token`**，见 4.4.3.4） | 是 |
| 支付 | GET | `/api/pay/status?order_no=` | 查询支付状态（轮询用；**改签差价期间 `paid=false`**，见 4.4.3.5） | 是 |
| 支付 | POST | `/api/pay/notify/alipay` | 支付宝异步通知（**无需登录**，验签+幂等） | 否 |
| 支付 | GET | `/api/pay/return/alipay` | 支付宝同步回跳（**无需登录**，跳转结果页） | 否 |
| 改签 | GET | `/api/change_quote` | 改签报价（新旧航班/差价） | 是 |
| 改签 | POST | `/api/change` | 执行改签（差价走渠道，补差价会返回 `need_pay`，见 4.4.5） | 是 |
| 退票 | GET | `/api/refund_quote` | 自愿退票报价（手续费/到账） | 是 |
| 退票 | POST | `/api/refund` | 退票（voluntary 即时 / special 人工审核），经支付宝原路退回 | 是 |
| 值机 | GET | `/api/checkin/seats` | 座位图 | 是 |
| 值机 | POST | `/api/checkin` | 值机/改座（返回登机牌数据） | 是 |
| 值机 | POST | `/api/checkin/cancel` | 取消值机 | 是 |
| 值机 | GET | `/api/checkin/boardpass` | 登机牌查询 | 是 |
| 数据 | GET | `/api/my/orders` | 我的订单列表（含 `change_pending` 待付改签差价，见 4.6.1） | 是 |
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
| 工具调用 | `{"tool": {"name": "search_flights", "args": {...}, "status": "running"}}` | AI 正在调工具；`args` 是本次调用的参数（工具分片到达早期可能为空对象，**参数以 done 事件的 `tool_calls` 为准**），界面可显示「查询中…」 |
| 确认卡片 | `{"pending_action": {...}}` | 流结束前若 AI 发起了确认卡片，会推这个事件 |
| 结束 | `{"done": true, "session_id": "...", "thread_id": "...", "response": "全文", "tools": ["search_flights"], "tool_calls": [{"name": "search_flights", "args": {...}}]}` | 一轮结束；`response` 是全文（懒得拼增量时直接用它覆盖）；`tool_calls` 是全部工具调用（名称+参数）汇总，**以此为准** |
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
      "created_at_ts": 1757205000.0,
      "message_count": 6,
      "last_user_question": "帮我订那张最便宜的"
    }
  ]
}
```

- `title`：AI 自动生成的会话标题（列表页展示用）；`last_user_question`：最后一句提问（无标题时兜底显示）。
- `created_at`：可读时间（`YYYY-MM-DD HH:MM`）；`created_at_ts`：秒级时间戳（兼容旧前端/排序用）。
- 重启 LangGraph 后旧线程 checkpoint 可能丢失，但线程 `values` 中的历史仍保留；后端会在用户继续对话时自动回填历史，不会覆盖旧记录。

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

> 🔒 **HITL 服务端强制（2026-09-12 起）**：所有写接口（`book` / `pay` / `pay/confirm` /
> `change` / `refund` / `checkin`）都必须携带**一次性确认凭证 `confirm_token`**，
> 缺失/无效/已用/过期一律 **403** `{"error": "...", "need_confirm": true}`。
> 凭证由对应**准备接口签发**（`booking_quote` / `pay/create` / `change_quote` /
> `refund_quote` / `checkin/seats`，聊天确认卡片同样带），15 分钟有效、只能用一次、
> 绑定会员+动作+目标+参数指纹。**流程永远是：先调准备接口拿凭证 → 用户确认 → 带凭证调写接口；
> 收到 403 就重新调准备接口，不要重试旧凭证。**

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
  "passengers": 1, "unit_price": 600, "total_amount": 600,
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...", "confirm_expires_at": "2026-09-13 00:11:15"
}
```

> `confirm_token` 是本报价对应的**下单凭证**（绑定航班/日期/舱位/人数指纹），
> 下一步 `POST /api/book` 必须原样带回。报价参数变了要重新取报价换新凭证。

#### 4.4.1.1 航班搜索 `GET /api/flights/search?departure=北京&destination=上海&date=2026-09-08`

供 Vue 客户端机票预订页直查航班列表（与 AI 的 search_flights 工具同一数据源）。需登录。

> ⏰ **只返回还没起飞的航班**（2026-09-15 起）：
> - `date` 早于今天 → 返回 `{"error": "不能查询过去日期的航班：… 已过期，仅支持今天（YYYY-MM-DD）及以后"}`；
> - `date` 等于今天 → **自动剔除起飞时刻已过的班次**，只留还能订的；
>   若当天班次已全部起飞，返回 `{"error": "今天（…）北京→上海 的 N 个航班均已起飞，请查询明天及以后的航班"}`；
> - 不传 `date` → 返回价格区间，区间只统计今天及以后（`price_range.from` 恒 ≥ 今天），
>   只剩历史价格的班次不再返回。
>
> 错误沿用本项目约定：HTTP 200 + `{"error": "..."}`，客户端读 `error` 字段提示即可。

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

过去日期 / 当天已全部起飞时的响应：

```json
{ "error": "不能查询过去日期的航班：2026-09-14 已过期，仅支持今天（2026-09-15）及以后" }
```

```json
{ "error": "今天（2026-09-15）北京→上海 的 2 个航班均已起飞，请查询明天及以后的航班" }
```

#### 4.4.2 创建订单 `POST /api/book`

```json
{ "flight_no": "CA1061", "flight_date": "2026-09-08", "cabin": "经济", "passengers": 1,
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH..." }
```

> `confirm_token` 来自上一步 `/api/booking_quote` 的响应。缺了会 403 `need_confirm`。

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
{ "order_no": "O4832015", "confirm_token": "KwMDnSV7WEcWkuJ17LxH..." }
```

成功：`{"success": true, "order_no": "O4832015", "status": "已出票", "message": "..."}`。

> 这是**模拟渠道**的一步付讫接口（`PAY_PROVIDER=mock` 时）。
> `confirm_token` 从 `POST /api/pay/create` 的响应拿（先 create 再 pay）。
> 接入真实渠道后请改走下面 4.4.3.1 起的「发起支付 → 收银台 → 回调」流程。

> 💡 **要接支付的同学请先看 [PAYMENT_HANDOFF.md](PAYMENT_HANDOFF.md)**，
> 那里有完整时序、安卓/小程序代码、边界情况与自测清单。本节仅作字段速查。

#### 4.4.3.1 支付渠道说明

`PAY_PROVIDER` 决定走哪条路，见 `config.py`：

| 值 | 行为 | 是否跳转外部收银台 |
|---|---|---|
| `mock`（默认） | 站内一步付讫，供演示与自动化测试 | 否 |
| `alipay_sandbox` | 支付宝沙箱，真实收银台、真实回调 | 是 |
| `alipay` | 支付宝生产 | 是 |

无论哪个渠道，**落账都只认一条路径**：`payments` 表的 `pay_no` 流水由
`payment_repo.mark_paid()` 翻转，`UPDATE ... WHERE status='待支付'` 且
`rowcount == 1` 才推进订单——异步通知重投 8 次也只会出票一次。

#### 4.4.3.2 发起支付 `POST /api/pay/create`

```json
{ "order_no": "O6749065" }
```

成功：

```json
{
  "success": true,
  "provider": "alipay_sandbox",
  "provider_label": "支付宝沙箱",
  "mode": "redirect",
  "pay_url": "http://127.0.0.1:5000/api/pay/gateway/P09111114270069",
  "pay_no": "P09111114270069",
  "order_no": "O6749065",
  "amount": 630.0,
  "message": "请在支付宝收银台完成付款",
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...",
  "confirm_expires_at": "2026-09-13 00:11:15"
}
```

- **`mode` 决定你该做什么**（后端可能换渠道，客户端别写死）：
  - `redirect` → 打开 `pay_url`，然后轮询状态（**不需要 confirm_token**）；
  - `direct` → 不跳转，直接调 4.4.3.4 确认（**必须带 confirm_token**）。
- `confirm_token` 是一次性支付凭证（15 分钟有效），`direct` 分支回传给 4.4.3.4；
  `redirect` 分支用不到，忽略即可。
- `amount` 是数字不是字符串。
- **`amount` 不一定是票款**：如果该订单有一笔待支付的**改签差价**（见 4.4.5），
  这里收的就是差价，响应会多带 `"purpose": "change_diff"` + `"fare_diff"` + `"change_request_id"`；
  付成功后订单落成「已改签」，而不是重复出票。客户端按同一个流程处理即可，无需分支。
- 同一订单 + 同渠道 + 同金额会**复用**已有的待支付流水，不会堆积悬挂记录。
- 订单 15 分钟未支付会被自动取消（后端定时任务），取消后支付接口返回 400。

#### 4.4.3.3 收银台中转页 `GET /api/pay/gateway/{pay_no}`

返回一段会自动提交的 HTML 表单，Referer 恒为本站点。**无需登录**——
它要在 WebView / 外部浏览器里打开，那些容器未必带得上登录态；
安全上靠 `pay_no` 不可猜测（随机生成）。

> **为什么要中转**：直接把支付宝的 `pay_url` 粘到地址栏打开时 Referer 为空，
> 沙箱会返回 `{"stat":"fail","msg":"RefererCheckFailed"}`。
> 用本站页面渲染表单再 POST 出去，是官方推荐的接入姿势，也规避了这个问题。

#### 4.4.3.4 确认支付 `POST /api/pay/confirm`

```json
{ "pay_no": "P09111114270069", "confirm_token": "KwMDnSV7WEcWkuJ17LxH..." }
```

成功则订单推进到已出票。**仅模拟渠道允许**，真实渠道的流水只能由回调翻转。

> `confirm_token` 来自 4.4.3.2 的响应，**必带**。403 `need_confirm` 的三种情况
> （缺少/已被使用/已过期）处理方式一致：**重新调 `/api/pay/create` 换新凭证**。

#### 4.4.3.5 查询状态 `GET /api/pay/status?order_no=O6749065`

> 注意参数是 **`order_no`（订单号）不是 `pay_no`**，接口会带出该订单最近一笔流水。

```json
{
  "order_no": "O6749065",
  "order_status": "已出票",
  "paid": true,
  "pay_no": "P09111114270069",
  "pay_status": "支付成功",
  "provider": "alipay_sandbox",
  "amount": 630
}
```

`paid` 是**唯一**的支付结果判据，**别用 `order_status` 反推**。平时它由订单状态推导
（`status != "待支付"` 即已付）；但在**改签补差价**期间，订单状态本身就是「已出票」，
此时接口会明确返回 `paid: false` + `change_pending: true`——含义是「差价还没到账、
改签还没生效」（见 4.4.5）。该分支的**全部**字段如下（`purpose` 标明这次查的是差价）：

```json
{ "order_no": "OE2E01", "order_status": "已出票", "paid": false,
  "pay_no": null, "pay_status": "待支付", "provider": null, "amount": 40,
  "purpose": "change_diff", "change_pending": true,
  "change_request_id": "CHG-8f2b1c9d...",
  "message": "改签差价 40 元待支付，支付成功后改签自动生效" }
```

> `pay_no` 在**用户还没点过「去支付」**之前是 `null`（那笔差价流水还没建），
> 点过之后就是对应的支付流水号。

前端在打开收银台后按 2 秒轮询这个接口，直到
`paid=true` 或超时（建议 3 分钟封顶）。

#### 4.4.3.6 支付宝异步通知 `POST /api/pay/notify/alipay`

由支付宝服务器调用，**不需要登录**。处理顺序：验签 → 校验 `app_id` → 比对金额
→ 幂等落账。返回 `success` / `failure` 两个纯文本之一（必须是这两个词，
否则支付宝会认为通知失败并重投，最多 8 次）。

验签按支付宝网关规则自行拼待签名字符串：**剔除 `sign`、`sign_type`，剔除值为空的
参数**，其余按键名 ASCII 升序以 `键=值` 用 `&` 连接，再用支付宝公钥做 SHA256withRSA。
两个必须踩准的点：

1. **字符集跟通知里的 `charset` 走**。实测沙箱通知声明 `charset=GBK`，中文参数
   （如 `subject`）按 GBK 编码、签名也是对 **GBK 字节**做的。若按 UTF-8 解码参数
   或按 UTF-8 转字节，原文必然对不上 →「签名校验未通过」。所以必须用**原始报文**
   （`request.get_data()`）按声明的 charset 解析，不能只用框架解析好的 dict。
2. **空值参数不参与签名**，拼串前要剔除。

`python-alipay-sdk` 的 `AliPay.verify()` 固定按 UTF-8 转字节、且不剔除空值，因此
本项目不用它验通知（仅作兜底）。验签失败时日志会打印通知声明的 charset、
空值参数名与我方拼出的原文，便于定位。

#### 4.4.3.7 同步回跳 `GET /api/pay/return/alipay`

买家付款后浏览器跳回的地址。它**只负责跳结果页**，不改单；页面会主动查单
（`alipay.trade.query`）兜底，避免异步通知迟到导致用户看到"未支付"。

#### 4.4.4 改签报价 `GET /api/change_quote?order_no=O4832015&new_flight_no=MU5101&new_date=2026-09-09&new_cabin=经济`

规则：**同航线任意航班、未来日期**，免改签费，只算差价。成功：

```json
{
  "order_no": "O4832015", "passengers": 1,
  "old":  { "flight_no": "CA1061", "date": "2026-09-08", "cabin": "经济", "amount": 600 },
  "new":  { "flight_no": "MU5101", "airline": "东方航空", "route": "北京-上海",
            "date": "2026-09-09", "dep_time": "08:00", "arr_time": "10:15",
            "cabin": "经济", "unit_price": 620, "amount": 620 },
  "fare_diff": 20, "diff_desc": "需补差价 20 元", "change_fee": 0, "message": "...",
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...", "confirm_expires_at": "2026-09-13 00:11:15"
}
```

`fare_diff` 正数=用户补差价，负数=退差价，0=无差价。`confirm_token` 绑定新航班/日期/舱位指纹，执行改签时回传。

#### 4.4.5 执行改签 `POST /api/change`

**差价一律走渠道**（多退少补都落到支付宝），所以改签可能是**两段式**的：

```json
{ "order_no": "O4832015", "new_flight_no": "MU5101", "new_date": "2026-09-09", "new_cabin": "经济",
  "requestId": "CHG-8f2b1c9d...", "confirm_token": "KwMDnSV7WEcWkuJ17LxH..." }
```

`requestId` 是**改签幂等键**（双击/断网重发不会改两次；不传时服务端兜底生成），
它同时兼作差价退款的渠道请求号。

按报价里的 `fare_diff` 分三种走向：

| 差价 | 响应 | 客户端还要做什么 |
|---|---|---|
| **> 0 补差价** | `{"success": true, "need_pay": true, "fare_diff": 40, "request_id": "CHG-...", "message": "改签需补差价 40 元，请先完成支付；支付成功后改签自动生效"}`——**订单此时一点没动** | 接着走支付：`POST /api/pay/create`（收的就是差价，响应带 `"purpose": "change_diff"`、`"amount": 40`）→ 跳收银台 → 轮询 `/api/pay/status`。**支付成功后服务端自动把订单落成「已改签」**，前端不用再调本接口 |
| **< 0 退差价** | `{"success": true, "refunded": true, "fare_diff": -40, "refund": {...}, "message": "订单 ... 已改签至 ...；差价 40 元已原路退回"}` | 已经改签完成，不用再做别的 |
| **= 0 无差价** | `{"success": true, "order_no": "...", "status": "已改签", "old": {...}, "new": {...}, "fare_diff": 0, "message": "..."}` | 直接完成 |

**两种非正常走向也要处理**：

| 场景 | 响应 | 客户端要做什么 |
|---|---|---|
| 把**同一个 `requestId`** 又提交一次（断网重发/连点） | 已生效：`{"success": true, "idempotent": true, "applied": true, "fare_diff": 40, ...}`<br>仍在等付款：`{"success": true, "need_pay": true, "idempotent": true, "request_id": "CHG-原请求号", ...}` | 已生效的**当成功处理**；等付款的接着走支付。注意返回的 `request_id` 是**原来那笔**的，不是本次新传的 |
| 该订单**已有未支付的改签差价**，这次又要改到**别的**航班 | HTTP 400：`{"error": "该订单还有一笔未支付的改签差价（CA1061 2026-09-21，需补 40 元）；请先完成支付，或等该笔支付超时后再改签", "blocked_by_pending_change": true, "pending_request_id": "CHG-...", "fare_diff": 40}` | **不要**提示"改签失败"就完事：引导用户去付**那一笔**（`pending_request_id` 指向的差价），或等该笔支付超时（15 分钟）后重试。⚠️ 这个字段**刻意不叫 `need_pay`**，避免被误当成"给本次请求付款" |

> 同一订单只允许存在一笔待支付的改签差价。目标与那笔**相同**时不会报错，
> 而是直接把那笔待支付流水返回给客户端（便于「继续支付」）。

> ⚠️ 两条服务端强制的不变量（实测已锁进单测）：
> 1. **订单变成「已改签」⇔ 差价必然已结清**。补差价先收款、退差价先原路退款；
>    渠道没成功就不改签——退差价失败时返回 400 且订单保持原样（可在退款流水里重试）。
> 2. **等差价支付期间，`/api/pay/status` 的 `paid` 是 `false`**（另带 `change_pending: true`）。
>    因为订单状态本身就是「已出票」，**别用 `order_status != 待支付` 去判断支付结果**。
>    该状态下的完整字段见 4.4.3.5。

同场景 `POST /api/pay/create` 响应（**只列关键字段**，完整字段见 4.4.3.2；
注意 `amount` 是差价、不是票款）：

```json
{ "success": true, "provider": "alipay_sandbox", "mode": "redirect",
  "pay_no": "P09161919003681", "order_no": "OE2E01", "amount": 40.0,
  "purpose": "change_diff", "fare_diff": 40, "change_request_id": "CHG-8f2b1c9d...",
  "pay_url": "http://<你的域名>/api/pay/gateway/P09161919003681",
  "message": "请在支付宝收银台完成付款" }
```

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
  "predict_amount": 540, "depart_time": "2026-09-08 06:40",
  "refund_type": "voluntary",
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...", "confirm_expires_at": "2026-09-13 00:11:15"
}
```

> `confirm_token` **绑定 `refund_type` 指纹**：拿报价时是自愿退票，执行时就不能改成特殊退票（会 403），反之亦然。

#### 4.4.7 执行退票 `POST /api/refund`

```json
{ "order_no": "O4832015", "refund_type": "voluntary",
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...", "requestId": "uuid-由客户端生成" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `order_no` | 是 | 订单号 |
| `refund_type` | 是 | `voluntary` 自愿 / `special` 特殊（非自愿） |
| `confirm_token` | 是 | 来自 `/api/refund_quote`，一次性；缺失或重复使用 → 403 `need_confirm` |
| `requestId` | **强烈建议** | 幂等键，客户端生成并保存（如 UUID）。**重试/断网重发必须复用同一个值** |

> - `requestId` 身兼两职：本地 `refunds` 表的幂等键 + 支付宝的**退款请求号 `out_request_no`**。
>   同一个 `requestId` 重复提交，本地直接返回首次结果；
>   即使请求穿透到支付宝，支付宝按「同 `out_trade_no` + 同 `out_request_no` 只退一次」返回幂等结果，
>   也**不会重复退款**。不传时服务端会生成一个，但那时重试会被当成一次新退款，请务必自带。
> - 金额不由客户端指定：自愿退票按上表费率由服务端计算，客户端只能退整单（费率内）。

| refund_type | 含义 | 流程 |
|---|---|---|
| `voluntary` | 自愿退票 | 服务端按上表费率算钱 → **先调支付宝退款** → 成功才把订单置「已退款」 |
| `special` | 特殊退票（航班延误/取消等非自愿原因） | 进入「退票中」，**人工审核**；管理员审批时按同样顺序调渠道退款 |

**成功响应**

```json
{
  "success": true, "order_no": "O4832015", "status": "已退款",
  "refund_amount": 540, "fee": 60,
  "channel": "alipay_sandbox", "channel_status": "成功",
  "requestId": "uuid-由客户端生成",
  "channel_message": "退款成功：540.00 元已由支付宝沙箱原路退回；本笔支付已全额退完",
  "message": "订单 O4832015 已退款 540 元（手续费 60 元，起飞前48-72小时（收10%））"
}
```

`channel` 是实际退款的渠道（`mock` / `alipay_sandbox` / `alipay`，由该订单**当初支付时**用的渠道决定），
`channel_status` 取值：`成功` / `失败` / `处理中` / `渠道不支持` / `无需渠道退款`。

**失败与异常（HTTP 400，除非另注）**

| 响应特征 | 含义 | 客户端应如何处理 |
|---|---|---|
| `error` 含「超出可退金额」 | 本笔支付可退余额不足（部分已退过） | 不要自动重试，展示可退余额 |
| `error` 含「支付宝侧查无此交易」 | 该笔支付没有真实经过支付宝（测试单/线下单），渠道无法原路退回 | 提示转人工；运营可走 `settle` 的 `offline:true` 线下退款结案 |
| `unsettled: true` / `pending: true`（409） | 该订单有一笔退款还没定论（上次超时），后端拒绝发起新退款 | 按「退款处理中」提示，别引导用户反复点 |
| `retryable: true` | 渠道侧临时问题（如商户余额不足），**可以重试** | 稍后用**同一个 `requestId`** 重试 |
| `unknown: true`（`channel_status: "处理中"`） | **结果未知**（网络超时/响应验签失败），钱可能已经退了 | **禁止直接重试**；提示"退款处理中"，由运营对账（见 4.4.8）后收口 |
| `idempotent: true` | 重复提交（同一 `requestId`） | 当成功处理即可 |
| `need_confirm`（403） | 凭证缺失/过期/已用过/参数指纹不符 | 重新调 `/api/refund_quote` 拿新凭证，再让用户确认 |
| `needs_manual: true`（**500**） | 钱已经退成功、但订单状态没更新（并发冲突） | 已转人工；不要提示用户"退款失败" |

> **退款是同步的，正常秒级完成**，但服务端坚持「**先渠道退款、成功之后才改订单状态**」。
> 这个顺序换来一条硬保证：**任何显示为「已退款」的订单，钱必然已经退出去**（或明确标记为线下退款）。
> 若反过来先改单再退款，一旦渠道失败就会出现"订单退了、钱没退"的假账——那是查不回来的。

#### 4.4.8 退款对账（运营侧）`POST /admin/api/refunds/query`

结果未知（`unknown: true`）时用它确认钱到底退了没：

```json
{ "out_request_no": "uuid-由客户端生成的 requestId", "order_no": "O4832015" }
```

```json
{ "success": true, "queried": true, "found": false, "refunded": false,
  "channel": "alipay_sandbox", "out_request_no": "...", "refundable": 540,
  "message": "渠道查无这笔退款，可安全重试（请复用同一退款请求号）" }
```

- `refunded: true` → 渠道确认退款成功，**不要**再发起退款（会命中渠道幂等，没有任何意义）；
  运营接着调 `POST /admin/api/refunds/settle` 把订单状态收口为「已退款」；
- `queried: true, refunded: false` → 渠道明确回答"没有这笔退款的成功记录"，可用同一个退款请求号重新发起；
- `queried: false`（返回 400）→ 渠道答不上来（网络故障），**不能**据此认为没退款，稍后重查或转人工。

**对账会改动退款流水的状态**，从而控制后续能不能再退款：

| refunds.status | 何时出现 | 是否允许再对该订单发起退款 |
|---|---|---|
| `退票中` | 用户提交特殊退票，等管理端审批（**钱还没退**） | 允许（审批流程用） |
| `退款待对账` | 渠道结果未知（网络超时）：钱可能已经退了 | **不允许**，先对账 |
| `渠道已退款` | 对账确认渠道已退款成功，但订单状态没跟上 | **不允许**，走 settle 收口 |
| `退款失败` | 渠道明确拒绝（余额不足等，或支付宝查无此交易） | 允许（复用同一个退款请求号重试） |
| `已退款` | 退款 + 订单状态都已完成 | 订单已是「已退款」，本就不能再退 |
| `线下退款` | 渠道明确退不了，运营走了线下退款收口 | 同上（订单已结案） |

> 退款流水是**每次尝试只留一行**（`request_id` 唯一）：同一请求号重试会把这行更新成最新结果，
> 所以不会出现"一行失败 + 一行成功"的重复记录，对账时看一行即可。

#### 4.4.9 确认退款完成（运营侧）`POST /admin/api/refunds/settle`

「钱已经通过渠道退了，但订单状态没跟上」时的收口动作（通常发生在超时未定论 → 对账确认已退之后）。

```json
{ "order_no": "O4832015", "admin_note": "对账确认已退款" }
```

**前置条件**：该订单必须已有一条 `渠道已退款` 的退款流水——那条流水就是"钱确实退过"的凭证。
没有它则返回 400，避免这个接口变成"不经过渠道也能把订单标成已退款"的后门。

渠道**明确退不了**时（支付宝返回 `ACQ.TRADE_NOT_EXIST`、或该渠道未实现退款），
可加 `"offline": true` 走线下退款结案，但要求：

```json
{ "order_no": "O4832015", "admin_note": "支付宝查无此交易，改为线下转账", "offline": true }
```

- 必须写备注且 ≥4 个字；
- 必须已存在一条 `退款失败` 流水，且失败原因是"渠道不可能退"（查无此交易 / 渠道不支持）——
  像"商户余额不足"这种**可重试**的失败不允许走线下；
- 流水记为 `线下退款`，给用户的站内通知写明"已由客服线下办理"（不会谎称原路退回），
  审计单独记一条 `线下退款`。

> 为什么要有这个出口：测试单/线下收款单在支付宝侧不存在，不给出口的话退票队列会永久卡在「退票中」。

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

> 💡 本接口响应里带 `confirm_token`（值机凭证）：**带上 `order_no` 时才签发**，
> 用户选好座位后 `POST /api/checkin` 时原样回传。

#### 4.5.2 值机 / 改座 `POST /api/checkin`

```json
{ "order_no": "O4832015", "seat_no": "31A", "confirm_token": "KwMDnSV7WEcWkuJ17LxH..." }
```

> `confirm_token` 来自上一步 `/api/checkin/seats` 的响应（见 4.5.1），必带。

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
      "checked_in": false, "checkin_seat": "", "checkin_gate": "", "boarding_time": "",
      "change_pending": false, "change_diff": 0, "change_target": ""
    }
  ]
}
```

- `checked_in: true` 时 `checkin_seat` / `checkin_gate` / `boarding_time` 有值——订单列表里可直接展示「已值机 31A」。
- **`change_pending`：该订单有一笔「待支付差价」的改签**（订单状态仍是「已出票」，改签还没生效）。
  为 `true` 时应在订单卡片上提示并可让用户「继续支付差价」——否则用户中途放弃付款后
  在界面上再也回不到收银台。`change_diff` 是要补的金额，`change_target` 形如
  `"CA1061 2026-09-21"`（说明这笔钱是为了改到哪一班）。正常订单为
  `false / 0 / ""`（见 4.4.5）。
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
| `change_flight` | 用户想改签 | `order_no`、`new_flight_no`、`new_date`、`new_cabin` | `POST /api/change`（可能返回 `need_pay`，见下方） |
| `seat_map` | 用户要值机选座 | `order_no`、`flight_no`、`flight_date` | 打开选座面板：`GET /api/checkin/seats` → `POST /api/checkin` |

客户端渲染卡片的标准姿势（以订票为例）：

1. 收到 `pending_action.type == "book_flight"`；
2. 调 `GET /api/booking_quote?flight_no=...&flight_date=...&cabin=...&passengers=...` 拿**服务端实时报价**（不要信卡片里 AI 报的价，报价接口才是准的），**响应里的 `confirm_token` 留好**；
3. 展示卡片：航班信息 + 单价 × 人数 = 总价 + 「确认预订」「取消」两个按钮；
4. 用户点确认 → `POST /api/book`（参数用卡片字段 + **上一步的 `confirm_token`**）→ 成功后提示订单号，并引导支付 `POST /api/pay/create`（→ `direct` 带凭证确认 / `redirect` 开收银台，见 4.4.3）；
5. 支付完成可顺带提示「可以去值机了」。退票/改签/值机卡片同理：**先调对应准备接口（`*_quote` / `checkin/seats`）拿实时数据和新凭证，再带凭证调写接口**。

**改签卡片要多做一件事**：卡片字段里**没有差价**（只有订单号和新航班/日期/舱位），
所以渲染时要调 `GET /api/change_quote` 拿 `fare_diff`/`diff_desc`，把
「需补差价 ¥X / 退回差价 ¥X」**显示在「确认改签」按钮之前**——否则用户要点到支付宝
收银台才知道自己要补多少钱。且 `POST /api/change` 返回 `need_pay: true` 时表示
**订单还没改**，客户端应接着走 `POST /api/pay/create` 收差价（详见 4.4.5）。

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
   ↓ 渲染确认卡片；先 GET /api/booking_quote 拿实时总价 + confirm_token
用户点"确认预订"
   ↓ POST /api/book（带 confirm_token）→ 拿到 order_no，status=待支付
引导支付
   ↓ POST /api/pay/create → direct: POST /api/pay/confirm（带 confirm_token）
   ↓                       → redirect: 打开 pay_url 收银台 + 轮询 /api/pay/status
完成。提示用户可在"我的订单"里值机
```

### 流程 B：值机选座 → 登机牌

```
"我的订单"里找到 status ∈ {已出票, 已改签} 且 checked_in=false 的订单
   ↓ 检查值机窗口：起飞前 24h ~ 45min（服务端也会校验，早了会 400 提示）
   ↓ GET /api/checkin/seats?flight_no=...&flight_date=...&order_no=...（响应带 confirm_token）
渲染座位图（只渲染订单 cabin 对应舱位；free 可点）
   ↓ 用户选座 31A
POST /api/checkin {order_no, seat_no:"31A", confirm_token}
   ↓ 成功 → 响应里就是登机牌数据（seat_no/gate/boarding_time/passenger...）
渲染登机牌卡片；之后随时 GET /api/checkin/boardpass?order_no=... 重查
```

### 流程 C：改签 / 退票

```
改签：改签卡片确认后 → GET /api/change_quote（新旧航班+差价+confirm_token）
      → POST /api/change { confirm_token, requestId }
      ├─ 差价 > 0（需补差）：订单**暂不改**，响应 need_pay=true
      │   → POST /api/pay/create（收差价）→ 跳收银台 → 轮询 /api/pay/status
      │   → 支付成功后服务端自动把订单落成「已改签」，客户端不用再调 /api/change
      ├─ 差价 < 0（退差价）：服务端先原路退回差价，成功后才改签，一次调用即完成
      └─ 差价 = 0：直接改签
      改签成功后原值机被自动取消，status=已改签，需要重新值机
自愿退票：GET /api/refund_quote（手续费档位+到账金额+confirm_token）
          → POST /api/refund {refund_type:"voluntary", confirm_token, requestId}
          即时到账，status=已退款（requestId 幂等，重试复用同一个）
特殊退票（延误/取消）：POST /api/refund {refund_type:"special", confirm_token, requestId}
          → status=退票中，等人工审核
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
| GET | `/admin/api/refunds` + POST approve/reject | 特殊退票审批（审批通过会**先调支付宝退款**，成功才把订单置「已退款」） |
| POST | `/admin/api/refunds/query` | 退款对账：确认某笔退款在支付宝侧是否成功（结果未知时的兜底） |
| POST | `/admin/api/refunds/settle` | 确认退款完成：渠道已退款、订单状态没跟上时收口为「已退款」；`offline:true` 走线下退款（渠道退不了时） |
| GET | `/admin/api/complaints` + POST resolve/escalate/reopen | 投诉处理 |
| GET/POST | `/admin/api/flights`、`/admin/api/flights/gate` | 航班管理 / 登机口指派 |
| GET | `/admin/api/orders`、`/admin/api/customers` | 订单 / 客户查询 |

**首次登录强制改密**：登录后调除 `me`/`change_password` 外的接口都会 403 `{"error": "首次登录必须先修改密码", "must_change_password": true}` —— 客户端检测到 `must_change_password: true` 时弹出改密页，走 `/admin/api/change_password`（body: `{old_password, new_password}`，新密码 ≥8 位且不能是常见弱口令）。

---

## 10. 文档更新记录

- 2026-09-16：**改签差价接入支付宝（多退少补都落渠道）**。`POST /api/change` 变为按差价分流：
  补差价返回 `need_pay`（订单暂不改，走 `/api/pay/create` 收差价，收款成功后服务端自动落成改签）、
  退差价先调 `alipay.trade.refund` 原路退回成功后才改签、无差价直接改签；新增改签幂等键 `requestId`。
  重写 4.4.5 并补充 `/api/pay/create` 可能收差价（`purpose: change_diff`）与
  差价支付期间 `/api/pay/status` 的语义（`paid=false` + `change_pending`）。
- 2026-09-16（同一批，补漏 + 纠错）：
  - **4.4.3.5 原文"`paid` 由订单状态推导"已不成立**（改签差价期间订单状态是「已出票」
    但 `paid=false`）→ 改为「只看 `paid`，别用 `order_status` 反推」并补响应示例；
  - 4.6.1 我的订单补 `change_pending` / `change_diff` / `change_target` 三字段；
  - 第 5 章卡片补「改签卡片必须先调 `/api/change_quote` 把差价显示在按钮之前」；
  - 4.4.5 补两条走向（同 `requestId` 重放、已有待付差价又改别的航班 →
    `blocked_by_pending_change`）；接口总表给 `/api/pay/status`、`/api/my/orders` 加了指引。
- 2026-09-09：会话列表补充 `created_at_ts`；修复 LangGraph `/state` 重启后偶发为空导致聊天记录/会话预览丢失的问题。
- 2026-09-09：修复 LangGraph 重启后旧线程 checkpoint 丢失、继续对话时覆盖历史的问题（发送前自动回填线程 values 中的历史消息）。
- 2026-09-09：座位图改为「初始全部可选、真实值机后才占用」，不再随机模拟预占座位；补充 `free` / `total` 为全舱位统计说明。
- 2026-09-15：`GET /api/flights/search` 与 AI 的 `search_flights` 工具改为**只返回尚未起飞的航班**——过去日期直接报错，查询当天时剔除起飞时刻已过的班次（若当天全飞完则提示改查明天），不带日期时价格区间只统计今天及以后。
- 2026-09-07：新增 `GET /api/flights/search` 客户端航班搜索接口（已写入接口总表与 4.4.1.1）。
- 2026-09-16：**退款接上支付宝**：`POST /api/refund` 改为「先调 `alipay.trade.refund` 原路退款、成功才改订单状态」，支持部分退款与重复退款防护（`requestId` 作为支付宝 `out_request_no`）；新增 `POST /admin/api/refunds/query` 退款对账。见 4.4.7 / 4.4.8。
