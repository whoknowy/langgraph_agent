# 支付功能接入交接文档（安卓 App / 微信小程序）

> **读者**：负责安卓 App、微信小程序的同学。
> **目标**：看完这一份，不改后端一行代码，就能把「订单 → 支付 → 出票」接上。
> **前置**：先读 [API.md](API.md) 的第 1、2 章（服务地址、token 用法），本文档只讲支付。
> **后端对接人**：支付相关疑问直接找后端，改接口前会先在本文档更新。

---

## 目录

1. [先建立三个正确认知（最重要）](#1-先建立三个正确认知最重要)
2. [接口一览](#2-接口一览)
3. [完整流程](#3-完整流程)
4. [分步接入](#4-分步接入)
5. [各端完整代码](#5-各端完整代码)
6. [必须处理的边界情况](#6-必须处理的边界情况)
7. [联调与自测清单](#7-联调与自测清单)
8. [常见问题 FAQ](#8-常见问题-faq)
9. [已知限制与后续](#9-已知限制与后续)

---

## 1. 先建立三个正确认知（最重要）

这三条如果理解错了，功能能跑但一定会出事故，请务必看完。

### 1.1 客户端**不判断**支付成功

**支付结果以后端为准，客户端只做两件事：把收银台打开、然后轮询后端。**

- ❌ 错误做法：看到支付宝页面跳回来了，就认为"支付成功"。
  同步回跳是浏览器行为，**可以伪造**，而且用户在收银台按返回键也会触发。
- ✅ 正确做法：跳转后轮询 `GET /api/pay/status`，后端说 `paid=true` 才算数。

后端在收到支付宝的**异步通知**时才真正改单（验签 + 校验金额 + 幂等），
这个过程客户端完全看不见，只能通过轮询得知。

### 1.2 必须按 `mode` 分支，不要写死"一定会跳转"

后端接了多个支付渠道，当前用的是哪个由服务端配置决定，随时可能切换：

| `mode` | 含义 | 你要做什么 |
|---|---|---|
| `redirect` | 要去外部收银台（支付宝） | 打开 `pay_url` → 轮询状态 |
| `direct` | 站内直接完成（模拟渠道） | 调 `/api/pay/confirm` → 完成 |

**只写 `redirect` 一种分支的话，后端切回模拟渠道做演示时你的端就废了。**
两个分支都实现，其实只多 5 行代码。

### 1.3 老的 `/api/pay` 接口还在，但**新功能不要用它**

`/api/pay` 是"一步付讫"的老接口（调一次直接出票），现在只作为模拟渠道和兼容保留。
新流程统一走 `/api/pay/create`。老接口不会删，但也不再增强。

---

## 2. 接口一览

Base URL 同 API.md：公网联调用 `http://flightagent.nat100.top`，本机调试用 `http://127.0.0.1:5000`。

| 方法 | 路径 | 需登录 | 用途 |
|---|---|---|---|
| POST | `/api/pay/create` | 是 | **发起支付**，拿到收银台地址 |
| GET | `/api/pay/gateway/{pay_no}` | 否 | 收银台中转页（H5，`pay_url` 就指向它） |
| POST | `/api/pay/confirm` | 是 | 站内确认支付（**仅 `mode=direct` 时用**） |
| GET | `/api/pay/status?order_no=` | 是 | **轮询支付结果** |
| POST | `/api/pay/notify/alipay` | 否 | 支付宝异步通知，**客户端不用管** |
| GET | `/api/pay/return/alipay` | 否 | 支付宝同步回跳，**客户端不用管** |

> 后两个是支付宝服务器/浏览器与后端之间的事，客户端**不会也不需要**调用。
> 列出来只是让你知道它们存在，别去 curl 它们。

**关于中转页为什么不需要登录**：它要在外部浏览器 / WebView 里打开，
那些容器未必带得上年你的登录态。安全上靠 `pay_no` 本身不可猜测（随机生成），
且付款的钱进的是商户账户，拿到它也没有攻击动机。
**但别把 `pay_url` 到处转发**，它等价于"这笔订单的付款入口"。

---

## 3. 完整流程

```
 你的端                          后端                         支付宝
   │                              │                             │
   │─ POST /api/pay/create ──────>│                             │
   │  {order_no}                  │                             │
   │                              │                             │
   │<─ {mode, pay_url, pay_no} ───│                             │
   │                              │                             │
   │                              │                             │
   ├─ mode == "direct" ? ────┐    │                             │
   │                         │    │                             │
   │  POST /api/pay/confirm ─┴───>│  （直接出票）                │
   │<─ {success, message} ────────│                             │
   │  结束 ✅                      │                             │
   │                              │                             │
   ├─ mode == "redirect" ?        │                             │
   │   打开 pay_url（WebView/浏览器）                            │
   │──────────────────────────────────────────────────────────>│
   │                              │                             │ 用户付款
   │                              │<── 异步通知 notify ──────────│
   │                              │    （验签+金额+幂等→改单）    │
   │                              │──── "success" ─────────────>│
   │                              │                             │
   │  同时：每 2 秒轮询            │                             │
   │─ GET /api/pay/status ───────>│                             │
   │<─ {paid: false} ─────────────│                             │
   │        ⋮ 重复 ⋮               │                             │
   │─ GET /api/pay/status ───────>│                             │
   │<─ {paid: true} ──────────────│                             │
   │  结束 ✅                      │                             │
```

**要点**：轮询和付款是两条并行的线。异步通知可能 1 秒到，也可能十几秒，
也可能永远不到（此时后端会在用户回跳时主动查单兜底）。所以轮询要**持续足够久**。

---

## 4. 分步接入

### 4.1 第一步：发起支付

```http
POST /api/pay/create
Authorization: Bearer <token>
Content-Type: application/json

{ "order_no": "O8285152" }
```

成功（HTTP 200）：

```json
{
  "success": true,
  "provider": "alipay_sandbox",
  "provider_label": "支付宝沙箱",
  "mode": "redirect",
  "pay_url": "http://flightagent.nat100.top/api/pay/gateway/P09111130284804",
  "pay_no": "P09111130284804",
  "order_no": "O8285152",
  "amount": 600.0,
  "message": "请在支付宝收银台完成付款"
}
```

**字段说明**

| 字段 | 说明 |
|---|---|
| `mode` | `redirect` 跳转收银台 / `direct` 站内确认，**按它分支** |
| `pay_url` | `redirect` 时才有。这是**后端的中转页**，不是支付宝地址，别自己拼 |
| `pay_no` | 支付流水号，`direct` 模式调 confirm 要用；**调试报错时把它给后端** |
| `amount` | 数字（元），不是字符串 |

**失败（HTTP 400）**：`{"error": "人话原因"}`，直接把 `error` 弹给用户即可。常见：

| error | 含义 | 建议处理 |
|---|---|---|
| `订单不存在：xxx` | 订单号错了 | 刷新订单列表 |
| `订单不属于当前登录会员` | 越权 | 不应该出现，出现请报后端 |
| `订单状态为「已出票」，无需支付` | 已经付过了 | 刷新列表 |
| `订单状态为「已取消」，无需支付` | **超过 15 分钟未付被自动取消** | 提示用户重新下单 |
| `支付渠道不可用：…` | 后端没配好密钥 | 报后端，不是你的问题 |

### 4.2 第二步：打开收银台（仅 `mode=redirect`）

`pay_url` 是一个 H5 页面，**必须用能执行 JS 的容器打开**——它会在加载时自动提交一个表单
跳到支付宝。用"直接 GET 请求拿 HTML"或者"自己解析里面的 URL 再打开"都会失败。

- **安卓**：`WebView.loadUrl(pay_url)`（外部浏览器亦可）
- **小程序**：见 5.1，有域名限制，**先读 9.1**

> **为什么有个中转页？** 直接拿支付宝的原始链接打开，Referer 是空的，
> 沙箱会返回 `{"stat":"fail","msg":"RefererCheckFailed"}`。
> 后端中转一次再提交，Referer 恒为本站，问题就没了。
> **所以不要试图绕过这个中转页。**

### 4.3 第三步：轮询结果

```http
GET /api/pay/status?order_no=O8285152
Authorization: Bearer <token>
```

> 参数 **`order_no` 不是 `pay_no`**，传错了会返回 `订单不存在`。

```json
{
  "order_no": "O8285152",
  "order_status": "已出票",
  "paid": true,
  "pay_no": "P09111130284804",
  "pay_status": "支付成功",
  "provider": "alipay_sandbox",
  "amount": 600
}
```

**只看 `paid` 一个字段就够了**——它由后端按订单状态推导，你不用关心
`order_status` 到底是"已出票"还是"已改签"。

轮询建议：**间隔 2 秒，最多 90 次（约 3 分钟）**。单次请求失败不要中断，继续下一拍。

### 4.4 `mode=direct` 时：站内确认

```http
POST /api/pay/confirm
Authorization: Bearer <token>
Content-Type: application/json

{ "pay_no": "P09111130284804" }
```

成功直接就是已出票。这个接口**只有模拟渠道能用**，真实渠道调它会报错——
这也是为什么必须按 `mode` 分支。

---

## 5. 各端完整代码

### 5.1 微信小程序

```javascript
// utils/pay.js
// request 直接复用 API.md 2.5 里那个（已带 token、已处理 401），别再抄一份
const { request } = require('./request');

/**
 * 支付主流程
 * @param {string} orderNo 订单号
 * @param {(payUrl:string)=>void} openCashier 打开收银台的回调（见下方 onOpenCashier）
 */
async function payOrder(orderNo, openCashier) {
  const d = await request('/api/pay/create', { method: 'POST', data: { order_no: orderNo } });

  if (d.mode === 'direct') {
    // 模拟渠道：站内直接确认，不用跳转
    const r = await request('/api/pay/confirm', { method: 'POST', data: { pay_no: d.pay_no } });
    wx.showToast({ title: r.message || '支付成功', icon: 'none' });
    return true;
  }

  // 真实渠道：打开收银台 + 轮询
  openCashier(d.pay_url);
  return await waitPaid(orderNo);
}

/** 轮询：2 秒一拍，最多 90 拍（约 3 分钟） */
function waitPaid(orderNo) {
  return new Promise((resolve) => {
    let ticks = 0;
    const timer = setInterval(async () => {
      ticks += 1;
      try {
        const s = await request('/api/pay/status', { data: { order_no: orderNo } });
        if (s.paid) {
          clearInterval(timer);
          wx.showToast({ title: '支付成功，订单已出票' });
          resolve(true);
          return;
        }
      } catch (e) {
        // 单次失败不打断，继续等下一拍
      }
      if (ticks >= 90) {
        clearInterval(timer);
        wx.showToast({ title: '等待支付超时，请返回刷新订单', icon: 'none' });
        resolve(false);
      }
    }, 2000);
  });
}

module.exports = { payOrder };
```

页面里调用：

```javascript
const { payOrder } = require('../../utils/pay');

Page({
  async onPayTap(e) {
    const orderNo = e.currentTarget.dataset.orderNo;
    wx.showLoading({ title: '正在发起支付' });
    try {
      await payOrder(orderNo, (payUrl) => {
        // 见 9.1：小程序不能直接开外链，这里是唯一需要你按实际情况改的地方
        wx.hideLoading();
        wx.setClipboardData({
          data: payUrl,
          success: () => wx.showModal({
            title: '请在浏览器完成付款',
            content: '链接已复制，粘贴到浏览器打开并完成付款，然后回到小程序查看结果。',
            showCancel: false,
          }),
        });
      });
      this.loadOrders();   // 刷新订单列表
    } catch (err) {
      wx.hideLoading();
      wx.showToast({ title: err.error || '支付发起失败', icon: 'none' });
    }
  },
});
```

### 5.2 安卓（Kotlin + OkHttp + WebView）

```kotlin
// PayRepository.kt
class PayRepository(private val base: String, private val token: () -> String) {

    private val json = MediaType.get("application/json; charset=utf-8")
    private val client = OkHttpClient()

    private fun call(path: String, body: String? = null): JSONObject {
        val req = Request.Builder()
            .url(base + path)
            .addHeader("Authorization", "Bearer ${token()}")
            .apply { if (body == null) get() else post(body.toRequestBody(json)) }
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (resp.code == 401) throw AuthExpiredException()
            if (!resp.isSuccessful) throw ApiException(JSONObject(text).optString("error"))
            return JSONObject(text)
        }
    }

    /** 发起支付，返回 (mode, payUrl, payNo) */
    fun create(orderNo: String): PayIntent {
        val d = call("/api/pay/create", """{"order_no":"$orderNo"}""")
        return PayIntent(
            mode = d.optString("mode"),
            payUrl = d.optString("pay_url"),
            payNo = d.optString("pay_no"),
        )
    }

    /** direct 模式：站内确认 */
    fun confirm(payNo: String): String =
        call("/api/pay/confirm", """{"pay_no":"$payNo"}""").optString("message")

    /** 轮询结果 */
    fun isPaid(orderNo: String): Boolean =
        call("/api/pay/status?order_no=$orderNo").optBoolean("paid", false)
}

data class PayIntent(val mode: String, val payUrl: String, val payNo: String)
class AuthExpiredException : Exception()
class ApiException(msg: String) : Exception(msg)
```

ViewModel 里编排：

```kotlin
fun pay(orderNo: String) {
    viewModelScope.launch(Dispatchers.IO) {
        try {
            val intent = repo.create(orderNo)
            if (intent.mode == "direct") {
                withContext(Dispatchers.Main) {
                    toast(repo.confirm(intent.payNo))
                    _paid.value = true
                }
                return@launch
            }
            // redirect：让 Activity 打开 WebView，然后开始轮询
            withContext(Dispatchers.Main) { _openCashier.value = intent.payUrl }
            repeat(90) {                      // 2 秒 × 90 ≈ 3 分钟
                delay(2000)
                if (repo.isPaid(orderNo)) {
                    withContext(Dispatchers.Main) { _paid.value = true }
                    return@launch
                }
            }
            withContext(Dispatchers.Main) { toast("等待支付超时，请返回刷新订单") }
        } catch (e: ApiException) {
            withContext(Dispatchers.Main) { toast(e.message) }
        } catch (e: AuthExpiredException) {
            withContext(Dispatchers.Main) { navigateToLogin() }
        }
    }
}
```

**WebView 必须这样配置**，否则支付宝唤起会失败：

```kotlin
webView.settings.apply {
    javaScriptEnabled = true        // 中转页靠 JS 自动提交表单，不开就停在空白页
    domStorageEnabled = true
}
webView.webViewClient = object : WebViewClient() {
    override fun shouldOverrideUrlLoading(v: WebView, req: WebResourceRequest): Boolean {
        val url = req.url.toString()
        // alipays:// / alipay:// 是支付宝 App 的 scheme，
        // 不交给系统处理的话，用户装了支付宝也唤不起来
        if (url.startsWith("alipays://") || url.startsWith("alipay://")) {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            return true
        }
        return false
    }
}
webView.loadUrl(payUrl)
```

**另外**：`AndroidManifest.xml` 里加 scheme 查询白名单（Android 11+ 必须，否则
`Intent.ACTION_VIEW` 找不到支付宝）：

```xml
<queries>
    <intent>
        <action android:name="android.intent.action.VIEW" />
        <data android:scheme="alipays" />
    </intent>
</queries>
```

---

## 6. 必须处理的边界情况

| 场景 | 后果 | 该怎么处理 |
|---|---|---|
| **用户没付款就返回** | 订单仍是待支付 | 停止轮询，刷新列表即可。**不要**提示"支付失败"，用户可能只是放弃了 |
| **超过 15 分钟未付** | 后端自动取消订单 | 下次 create 会返回 `订单状态为「已取消」`，引导重新下单 |
| **用户连点"去支付"** | 理论上会重复下单 | 后端同订单同渠道同金额会**复用**同一笔流水，不会重复扣款；但前端仍建议加防抖 |
| **页面销毁 / App 被杀** | 轮询中断 | 回到订单页时**主动查一次** `/api/pay/status`，别只依赖轮询 |
| **轮询期间 token 过期** | 401 | 跳登录。重新登录后回订单页，状态查询会补上结果 |
| **异步通知比用户返回慢** | 用户看到"未支付" | 这正是要轮询 3 分钟的原因，别把超时设成 10 秒 |
| **轮询全部超时** | 状态未知 | 提示"结果确认中，请稍后刷新"，**不要**让用户重复付款 |

**最容易出事的一条**：轮询超时后引导用户"重新支付"。
异步通知可能只是迟到，用户再付一次就是重复付款。宁可让他刷新列表。

---

## 7. 联调与自测清单

### 7.1 让后端切到模拟渠道（推荐先这么联调）

后端把 `.env` 里 `PAY_PROVIDER=mock`，你的端就会走 `mode=direct`：
不用跳转、不用连支付宝、点一下就出票。**先把 direct 分支跑通，再测 redirect。**

### 7.2 沙箱真实付款自测

后端切 `PAY_PROVIDER=alipay_sandbox` 后：

- [ ] 发起支付，`pay_url` 能打开，页面显示"正在跳转到支付宝收银台…"
- [ ] 自动跳到支付宝，页面标题是「支付宝 - 网上支付 安全快速！」
- [ ] 用**沙箱买家账号**登录付款（不是你自己的支付宝；沙箱账号在支付宝开放平台
      沙箱控制台的「沙箱账号」里，找后端要）
- [ ] 付款后 3~15 秒内，轮询返回 `paid=true`
- [ ] 订单列表里该订单状态变成「已出票」
- [ ] **再点一次"去支付"**，应提示已出票、无需支付（验证幂等）
- [ ] 付款中途按返回键 → 订单仍是待支付 → 可再次发起支付
- [ ] 等 15 分钟不付 → 订单自动取消 → 再支付报「已取消」

### 7.3 抓包自查

付款后让后端查库最直观；你自己能看的是：`/api/pay/status` 的 `pay_status`
从 `待支付` 变成 `支付成功`。若一直是 `待支付` 但钱确实付了，把 `pay_no` 给后端查。

---

## 8. 常见问题 FAQ

**Q：收银台页面一片空白 / 卡在"正在跳转"？**
A：容器没开 JavaScript。中转页靠 `onload` 自动提交表单，JS 关掉就停住了。
安卓检查 `javaScriptEnabled = true`。

**Q：打开 `pay_url` 看到 `RefererCheckFailed`？**
A：说明有人绕过了中转页、直接拿了支付宝原始链接。用后端返回的 `pay_url` 原样打开即可。

**Q：我直接 GET `/api/pay/gateway/xxx` 拿到 HTML，自己提取 action 和字段 POST 行不行？**
A：技术上能通，但**别这么做**。表单字段和签名方式可能随渠道调整，
中转页是后端维护的稳定契约，自己解析等于把实现细节耦合进客户端。

**Q：轮询该用 `pay_no` 还是 `order_no`？**
A：**`order_no`**。传 `pay_no` 会返回"订单不存在"。

**Q：支付成功了但订单还是待支付？**
A：异步通知没到（本地联调常见，因为支付宝要能访问到你的后端）。
后端在用户回跳时会主动查单兜底；如果仍不行，把 `pay_no` 给后端手动查单。
**不要**让用户重新付。

**Q：`mode` 会变吗？**
A：会。后端切渠道时 `redirect` / `direct` 就变了。所以两个分支都要实现。

**Q：金额字段怎么有时是 `600.0` 有时是 `600`？**
A：都是数字，JSON 里 `600.0` 和 `600` 等价。按数字解析，不要按字符串。

---

## 9. 已知限制与后续

### 9.1 手机网站支付（`wap.pay`）已上线 ✅

**状态更新（2026-09-11）**：后端已补齐 `alipay.trade.wap.pay`，两种场景并存，
用环境变量 `ALIPAY_SCENE` 切换，**不需要改代码**：

| `ALIPAY_SCENE` | 下单接口 | product_code | 适用端 |
| --- | --- | --- | --- |
| `page`（默认） | `alipay.trade.page.pay` | `FAST_INSTANT_TRADE_PAY` | PC 浏览器 |
| `wap` | `alipay.trade.wap.pay` | `QUICK_WAP_PAY` | 手机浏览器 / 安卓 WebView / 小程序 web-view |

当前沙箱环境已设为 `wap`（`alipay_provider` 的 `label` 会显示
「支付宝沙箱（手机网站支付）」，联调时可据此确认）。

**为什么移动端一定要用 `wap`**：沙箱的 `page` 收银台页面引用了蚂蚁内网资源
（`seccllprod.stable.alipay.net` / `umidprod.stable.alipay.net`），公网 DNS
解析失败，会出现「能进收银台但点付款一直请求失败」。`wap` 不走那套页面，
手机端体验也更正常。

**对接方无需感知 scene**：客户端拿到的仍是同样的 `pay_url`（后端的中转页），
打开方式不变。区别只在于中转页里的表单指向哪个支付宝接口。

**小程序端仍需注意**：微信小程序无法用 `web-view` 打开外部链接，除非域名在
小程序后台配置为「业务域名」（要求 HTTPS + 已备案 + 放校验文件）。
`flightagent.nat100.top` 是内网穿透域名，能否通过校验不确定。
所以 5.1 示例代码里 `openCashier` 用的仍是**复制链接 → 引导浏览器打开**
的过渡方案。生产环境换成自有域名后可直接用 `web-view`。
安卓端 WebView 可正常打开 `wap` 收银台，参考 5.2 的 scheme 拦截写法。

### 9.2 微信支付

微信支付 V3 **官方不提供沙箱环境**，V2 的 `sandboxnew` 已废弃。
所以短期内不会有"微信沙箱支付"，真要接只能走生产小额（1 分钱）验证。
目前小程序端的支付走的是上面这套支付宝 H5 方案。

### 9.3 回调地址说明

异步通知地址由后端配置（`ALIPAY_NOTIFY_URL`），**客户端不需要传、也不该传**。
本地联调时后端用 NATAPP 穿透，公网地址 `http://flightagent.nat100.top`。

---

## 附：改动记录

| 日期 | 变更 |
|---|---|
| 2026-09-11 | 初版。支付渠道抽象上线，含 mock / 支付宝沙箱两种模式 |
| 2026-09-11 | 补 `wap.pay`（手机网站支付）渠道，`ALIPAY_SCENE` 切换 page/wap；9.1 由"待办"改为"已上线" |
