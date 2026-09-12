# 安卓端支付接入文档

> **读者**：负责安卓 App 的同学。
> **目标**：不改后端一行代码，把「订单 → 支付 → 出票」接进 App，并在真机上跑通。
> **前置**：[API.md](API.md) 第 2 章（服务地址、token 用法）。本文只讲支付，
> 但假设你已经有一个能带 `Authorization` 头请求后端的 OkHttp 客户端。
> **支付相关疑问直接找后端**，接口有变动会第一时间更新本文。

本文与微信小程序端的差异集中在**打开收银台的容器**上，其余流程两端一致。
若你也在看通用版（含小程序），见 [PAYMENT_HANDOFF.md](PAYMENT_HANDOFF.md)。

---

## 目录

1. [动手前必须建立的三个认知](#1-动手前必须建立的三个认知)
2. [接口一览](#2-接口一览)
3. [完整时序](#3-完整时序)
4. [分步实现](#4-分步实现)
5. [完整可运行代码](#5-完整可运行代码)
6. [WebView 是这里最容易出错的环节](#6-webview-是这里最容易出错的环节)
7. [必须处理的边界情况](#7-必须处理的边界情况)
8. [真机联调与自测清单](#8-真机联调与自测清单)
9. [FAQ](#9-faq)
10. [已知限制](#10-已知限制)

---

## 1. 动手前必须建立的三个认知

### 1.1 客户端**不判断**支付成功

**支付结果以后端为准。客户端只做两件事：打开收银台、轮询后端。**

- ❌ 看到支付宝页面跳回来 / WebView 加载了 `return_url`，就认为"支付成功"。
- ✅ 跳转后轮询 `GET /api/pay/status`，后端返回 `paid=true` 才算数。

原因：同步回跳是浏览器行为，**可以被伪造**，而且用户在收银台按返回键也会触发。
后端只在收到支付宝的**异步通知**时才真正改单（验签 + 校验金额 + 幂等），
这个过程 App 完全看不见，只能靠轮询得知。

### 1.2 必须按 `mode` 分支

后端的支付渠道由服务端配置决定，随时可能切换：

| `mode` | 含义 | 你要做什么 |
|---|---|---|
| `redirect` | 去外部收银台（支付宝） | 用 WebView 打开 `pay_url` → 轮询状态 |
| `direct` | 站内直接完成（模拟渠道） | 调 `/api/pay/confirm` → 完成，不跳转 |

**只实现 `redirect` 的话，后端切回模拟渠道做演示时你的端就废了。**
两个分支都写其实只多十几行。

### 1.3 老的 `/api/pay` 不要用

`/api/pay` 是"一步付讫"的老接口，现在只作为模拟渠道兼容保留。
新流程一律走 `/api/pay/create`。老接口不会删，但也不会再增强。

### 1.4 写接口要带一次性确认凭证（confirm_token）★ 2026-09-12 新增

服务端已启用 **HITL 强制确认**：写库接口（`/api/pay/confirm`、`/api/pay`、
`/api/book`、`/api/change`、`/api/refund`、`/api/checkin`）都必须携带
**一次性确认凭证 `confirm_token`**，否则返回 **403** `need_confirm`：

```json
{ "error": "支付需要用户确认：缺少确认凭证（confirm_token）", "need_confirm": true }
```

规则三条：**凭证从 `/api/pay/create` 的响应里拿**（15 分钟有效、一次性、绑定订单）；
**调 `/api/pay/confirm` 时原样放进 body**；**403 了就重新走一遍 create**，别重试旧凭证。

对支付流程的影响：**`redirect` 分支（支付宝收银台）完全不变**，不需要凭证；
**只有 `direct` 分支（模拟渠道站内确认）多带一个字段**，见 4.4。

---

## 2. 接口一览

Base URL：公网 `http://flightagent.nat100.top`；本机调试 `http://127.0.0.1:5000`。

| 方法 | 路径 | 需登录 | 用途 |
|---|---|---|---|
| POST | `/api/pay/create` | ✅ | **发起支付**，拿到收银台地址 |
| GET | `/api/pay/gateway/{pay_no}` | ❌ | 收银台中转页（H5，`pay_url` 指向它） |
| POST | `/api/pay/confirm` | ✅ | 站内确认（**仅 `mode=direct`**），body 须带 create 下发的 `confirm_token` |
| GET | `/api/pay/status?order_no=` | ✅ | **轮询支付结果** |
| POST | `/api/pay/notify/alipay` | ❌ | 支付宝异步通知，**你不用管** |
| GET | `/api/pay/return/alipay` | ❌ | 支付宝同步回跳，**你不用管** |

> 最后两个是支付宝服务器与后端之间的事，**App 不会也不需要调用**。
> 列出来只是让你知道它们存在。

**401 的响应体**也是 `{"error": "..."}`（不是空 body），文案是
`未登录，请先登录会员账号`。所以你的拦截器可以统一按 `error` 字段提示。

所有需要登录的接口，**没有 `Authorization` 头或 token 失效，都返回 401**——
不要靠 400 和 401 混用去猜，这两个状态码是分开的。

**为什么中转页不需要登录**：它要在 WebView / 外部浏览器打开，那些容器未必带得上
登录态。安全上靠 `pay_no` 不可猜测（随机生成），且钱进的是商户账户。
**但别把 `pay_url` 到处转发**，它等于"这笔订单的付款入口"。

---

## 3. 完整时序

```
 App(WebView)               后端                        支付宝
   │                         │                            │
   │─ POST /api/pay/create ─>│                            │
   │                         │                            │
   │<─ {mode, pay_url,       │                            │
   │    pay_no, amount} ─────│                            │
   │                         │                            │
   ├─ mode == "direct" ? ────┤                            │
   │   POST /api/pay/confirm │（直接出票）                 │
   │<─ {success, message} ───│                            │
   │   结束 ✅                │                            │
   │                         │                            │
   └─ mode == "redirect" ? ──┤                            │
      WebView 打开 pay_url   │                            │
      （中转页 JS 自动提交表单）──────────────────────────>│
      │                      │                            │ 用户付款
      │                      │<── 异步通知 notify ─────────│
      │                      │  （验签+金额+幂等→改单）     │
      │                      │──── "success" ────────────>│
      │                      │                            │
      │  并行：每 2 秒轮询     │                            │
      │─ GET /api/pay/status >│                            │
      │<─ {paid: false} ──────│                            │
      │      ⋮ 重复 ⋮          │                            │
      │─ GET /api/pay/status >│                            │
      │<─ {paid: true} ───────│                            │
      │  结束 ✅               │                            │
```

**要点**：轮询与付款是两条并行线。异步通知可能 1 秒到、十几秒到，也可能不到
（此时后端在同步回跳时主动查单兜底）。所以**轮询必须足够久**。

---

## 4. 分步实现

### 4.1 发起支付

```http
POST /api/pay/create
Authorization: Bearer <token>
Content-Type: application/json

{ "order_no": "O8285152" }
```

成功（200）：

```json
{
  "success": true,
  "provider": "alipay_sandbox",
  "provider_label": "支付宝沙箱（手机网站支付）",
  "mode": "redirect",
  "pay_url": "http://127.0.0.1:5000/api/pay/gateway/P09111130284804",
  "pay_no": "P09111130284804",
  "order_no": "O8285152",
  "amount": 600.0,
  "message": "请在支付宝收银台完成付款",
  "confirm_token": "KwMDnSV7WEcWkuJ17LxH...",
  "confirm_expires_at": "2026-09-13 00:11:15"
}
```

| 字段 | 说明 |
|---|---|
| `mode` | `redirect` / `direct`，**按它分支** |
| `pay_url` | 仅 `redirect` 有。这是**后端中转页**，不是支付宝地址，别自己拼 |
| `pay_no` | 流水号。`direct` 时 confirm 要用；**查问题时报给后端** |
| `amount` | 数字（元），不是字符串 |
| `confirm_token` | **一次性确认凭证**：`direct` 模式调 confirm 必须带上（15 分钟有效、只能用一次）；`redirect` 模式用不到，忽略即可 |
| `confirm_expires_at` | 凭证过期时间，仅提示用 |

失败（400）：`{"error": "人话原因"}`，把 `error` 直接弹给用户：

| error | 含义 | 处理 |
|---|---|---|
| `订单不存在：xxx` | 订单号错 | 刷新订单列表 |
| `订单不属于当前登录会员` | 越权 | 不该出现，出现请报后端 |
| `订单状态为「已出票」，无需支付` | 已付过 | 刷新列表 |
| `订单状态为「已取消」，无需支付` | **超 15 分钟未付被自动取消** | 引导重新下单 |
| `支付渠道不可用：…` | 后端密钥没配好 | 报后端，不是你的问题 |

### 4.2 打开收银台（仅 `redirect`）

`pay_url` 是一个 **H5 页面，靠 JavaScript 自动提交表单**跳转到支付宝。

- ✅ `WebView.loadUrl(pay_url)`（外部浏览器 `Intent.ACTION_VIEW` 也可以）
- ❌ 用 OkHttp 直接 GET 它拿 HTML
- ❌ 自己解析里面的 URL 再打开

后两种都会失败，原因见第 6 章。

### 4.3 轮询结果

```http
GET /api/pay/status?order_no=O8285152
Authorization: Bearer <token>
```

> 参数是 **`order_no`，不是 `pay_no`**。传错返回"订单不存在"。

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
`order_status` 具体是"已出票"还是"已改签"。

间隔 **2 秒**，最多 **90 次**（约 3 分钟）。单次失败不要中断，继续下一拍。

### 4.4 `mode=direct` 时站内确认

```http
POST /api/pay/confirm
Authorization: Bearer <token>
Content-Type: application/json

{ "pay_no": "P09111130284804", "confirm_token": "<create 响应里的 confirm_token>" }
```

成功直接出票。**这个接口只有模拟渠道能用**，真实渠道调它会报错——
所以必须按 `mode` 分支。

**confirm_token 相关的 403**（见 1.4）：

| error | 原因 | 处理 |
|---|---|---|
| `…需要用户确认：缺少确认凭证（confirm_token）` | body 里没带凭证 | 从 create 响应取出 `confirm_token` 放进 body |
| `该确认凭证已被使用…` / `确认凭证已过期…` / `确认凭证无效…` | 凭证一次性、15 分钟 | **重新调 `/api/pay/create`** 换新凭证，别重试旧的 |

> `redirect` 分支不需要凭证：落账由支付宝回调在后端完成，轮询 `paid` 即可。

---

## 5. 完整可运行代码

### 5.1 网络层

复用 API.md 2.5 的 OkHttp 客户端（已用拦截器统一加 token、已处理 401）。
下面只加支付相关的部分。

```kotlin
// PayApi.kt
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

private val jsonMedia = "application/json; charset=utf-8".toMediaType()

class PayApi(private val client: OkHttpClient, private val base: String) {

    /** 发起支付 */
    fun create(token: String, orderNo: String): PayIntent {
        val body = """{"order_no":"$orderNo"}""".toRequestBody(jsonMedia)
        val req = Request.Builder()
            .url("$base/api/pay/create")
            .addHeader("Authorization", "Bearer $token")
            .post(body)
            .build()
        return client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (resp.code == 401) throw AuthExpiredException()
            if (!resp.isSuccessful) throw ApiException(JSONObject(text).optString("error"))
            val d = JSONObject(text)
            PayIntent(
                mode         = d.optString("mode"),
                payUrl       = d.optString("pay_url"),
                payNo        = d.optString("pay_no"),
                amount       = d.optDouble("amount", 0.0),
                label        = d.optString("provider_label"),
                confirmToken = d.optString("confirm_token"),   // direct 分支 confirm 要用
            )
        }
    }

    /** direct 模式：站内确认（confirm_token 必带，见 1.4 / 4.4） */
    fun confirm(token: String, payNo: String, confirmToken: String): String {
        val body = """{"pay_no":"$payNo","confirm_token":"$confirmToken"}""".toRequestBody(jsonMedia)
        val req = Request.Builder()
            .url("$base/api/pay/confirm")
            .addHeader("Authorization", "Bearer $token")
            .post(body)
            .build()
        return client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (resp.code == 401) throw AuthExpiredException()
            if (!resp.isSuccessful) throw ApiException(JSONObject(text).optString("error"))
            JSONObject(text).optString("message")
        }
    }

    /** 轮询：只看 paid */
    fun isPaid(token: String, orderNo: String): Boolean {
        val req = Request.Builder()
            .url("$base/api/pay/status?order_no=$orderNo")
            .addHeader("Authorization", "Bearer $token")
            .get()
            .build()
        return client.newCall(req).execute().use { resp ->
            if (resp.code == 401) throw AuthExpiredException()
            if (!resp.isSuccessful) return@use false
            JSONObject(resp.body?.string().orEmpty()).optBoolean("paid", false)
        }
    }
}

data class PayIntent(
    val mode: String,
    val payUrl: String,
    val payNo: String,
    val amount: Double,
    val label: String,
    val confirmToken: String,
)

class AuthExpiredException : Exception("登录已过期")
class ApiException(message: String) : Exception(message)
```

### 5.2 ViewModel 编排

```kotlin
// PayViewModel.kt
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class PayViewModel(private val api: PayApi, private val token: () -> String) : ViewModel() {

    private val _state = MutableStateFlow<PayState>(PayState.Idle)
    val state: StateFlow<PayState> = _state

    /** 由 Activity 观察：收到就打开 WebView */
    private val _openCashier = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val openCashier: SharedFlow<String> = _openCashier

    fun pay(orderNo: String) {
        viewModelScope.launch {
            _state.value = PayState.Creating
            val t = token()
            try {
                val intent = withContext(Dispatchers.IO) { api.create(t, orderNo) }

                if (intent.mode == "direct") {
                    val msg = withContext(Dispatchers.IO) {
                        api.confirm(t, intent.payNo, intent.confirmToken)
                    }
                    _state.value = PayState.Success(msg.ifBlank { "支付成功" })
                    return@launch
                }

                // redirect：通知 UI 开 WebView，然后开始轮询
                _openCashier.emit(intent.payUrl)
                _state.value = PayState.Waiting

                repeat(90) {                       // 2 秒 × 90 ≈ 3 分钟
                    delay(2000)
                    val paid = try {
                        withContext(Dispatchers.IO) { api.isPaid(t, orderNo) }
                    } catch (e: AuthExpiredException) {
                        throw e
                    } catch (e: Exception) {
                        false                          // 单次失败不中断，等下一拍
                    }
                    if (paid) {
                        _state.value = PayState.Success("支付成功，订单已出票")
                        return@launch
                    }
                }
                // 超时：状态未知，绝不能让用户重复付款
                _state.value = PayState.Timeout("支付结果确认中，请稍后刷新订单列表")

            } catch (e: AuthExpiredException) {
                _state.value = PayState.NeedLogin
            } catch (e: ApiException) {
                _state.value = PayState.Failed(e.message ?: "支付发起失败")
            } catch (e: Exception) {
                _state.value = PayState.Failed("网络异常，请重试")
            }
        }
    }
}

sealed interface PayState {
    data object Idle : PayState
    data object Creating : PayState
    data object Waiting : PayState
    data class Success(val message: String) : PayState
    data class Timeout(val message: String) : PayState
    data class Failed(val message: String) : PayState
    data object NeedLogin : PayState
}
```

### 5.3 Activity 里承载 WebView

```kotlin
class CashierActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PAY_URL = "pay_url"
        /** 收银台结束（用户返回 / 支付完成）时带回，供订单页刷新 */
        const val RESULT_DISMISSED = 1001

        fun start(context: Context, payUrl: String) {
            context.startActivity(Intent(context, CashierActivity::class.java)
                .putExtra(EXTRA_PAY_URL, payUrl))
        }
    }

    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)

        val payUrl = intent.getStringExtra(EXTRA_PAY_URL).orEmpty()

        webView.settings.apply {
            javaScriptEnabled = true       // ★ 必须：中转页靠 JS 自动提交表单
            domStorageEnabled = true
            // 沙箱是 http，真机 Android 9+ 默认禁明文，这里放行（详见第 6 章）
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest
            ): Boolean {
                val url = request.url.toString()
                // ★ 支付宝 App 的 scheme，不交给系统就唤不起 App
                if (url.startsWith("alipays://") || url.startsWith("alipay://")) {
                    return try {
                        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                        true
                    } catch (e: ActivityNotFoundException) {
                        // 用户没装支付宝：留在当前页，让他用网页版收银台
                        false
                    }
                }
                return false
            }
        }

        webView.loadUrl(payUrl)
    }

    /** 用户按返回：直接关掉，让订单页去轮询/刷新，不要在这里判断成败 */
    override fun onBackPressed() {
        setResult(RESULT_DISMISSED)
        super.onBackPressed()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
```

**`AndroidManifest.xml`** —— Android 11+ 需要声明可见性，否则 `ACTION_VIEW`
找不到支付宝（`ActivityNotFoundException`）：

```xml
<queries>
    <intent>
        <action android:name="android.intent.action.VIEW" />
        <data android:scheme="alipays" />
    </intent>
</queries>

<!-- 沙箱是 http，需要放行明文；生产换 https 后可删掉这段 -->
<application
    android:usesCleartextTraffic="true"
    ... >
```

---

## 6. WebView 是这里最容易出错的环节

按出错概率从高到低排：

### 6.1 页面空白 / 卡在"正在跳转…"

**原因**：没开 JavaScript。中转页靠 `onload` 自动 `form.submit()`，
JS 一关就永远停在那一页。

**修**：`webView.settings.javaScriptEnabled = true`。

### 6.2 点了付款没反应 / 唤起不了支付宝

**原因**：收银台会跳 `alipays://` 唤起支付宝 App，但 WebView 默认会尝试
自己加载这个 scheme，结果加载失败，什么也不发生。

**修**：在 `shouldOverrideUrlLoading` 里拦截该 scheme，用
`Intent.ACTION_VIEW` 交给系统（见 5.3）。

### 6.3 装了支付宝还是 `ActivityNotFoundException`

**原因**：Android 11（API 30）起有**包可见性**限制，不声明 `<queries>`
就查不到支付宝。

**修**：加 5.3 的 `<queries>` 块。

### 6.4 真机上收银台报网络错误 / 白屏

**原因**：沙箱环境是 **http**，Android 9+ 默认禁止明文流量。

**修**：`android:usesCleartextTraffic="true"`（仅调试期，生产换 https）。

### 6.5 想用外部浏览器而不是 WebView？

可以，`Intent.ACTION_VIEW` 打开 `pay_url` 即可，且**不用操心 6.1~6.3**。
代价是用户付完款回到 App 需要手动切回，且你拿不到"用户已返回"的回调，
只能靠轮询（本来也要轮询）。

| | WebView | 外部浏览器 |
|---|---|---|
| 需要 alipays scheme 拦截 | ✅ 必须 | ❌ 不需要 |
| 需要开 JS | ✅ 必须 | 天然支持 |
| 用户付完自动回 App | 可自行实现 | 需手动切回 |
| 体验 | 更连贯 | 更省事 |

**建议**：首版用外部浏览器把链路跑通，再换 WebView 优化体验。

### 6.6 别自己解析中转页

技术上你可以 GET 到 HTML、提取 `action` 和隐藏字段、自己 POST。
**但不要这么做**：表单字段和签名方式可能随渠道调整，
中转页是后端维护的稳定契约，解析它等于把实现细节耦合进 App。

---

## 7. 必须处理的边界情况

| 场景 | 后果 | 处理 |
|---|---|---|
| **用户没付款就返回** | 订单仍待支付 | 停止轮询 + 刷新列表。**不要**提示"支付失败"，他可能只是放弃了 |
| **超过 15 分钟未付** | 后端自动取消订单 | 再发起会返回「已取消」，引导重新下单 |
| **连点"去支付"** | 可能重复下单 | 后端同订单+渠道+金额会**复用**同一笔流水，不会重复扣款；前端仍建议加防抖/置灰按钮 |
| **Activity 销毁 / App 被杀** | 轮询中断 | 回订单页时**主动查一次** `/api/pay/status`，别只依赖轮询结果 |
| **轮询期间 token 过期** | 401 | 跳登录。重登后回订单页，状态查询会补上结果 |
| **direct 确认返回 403 need_confirm** | 凭证缺失/已用/过期 | **重新调 create** 拿新凭证再 confirm，别重试旧凭证 |
| **异步通知比用户返回慢** | 用户看到"未支付" | 这正是要轮询 3 分钟的原因，别把超时缩到 10 秒 |
| **轮询全部超时** | 状态未知 | 提示"结果确认中，请稍后刷新"，**绝不要**引导重新支付 |
| **WebView 里支付完成后停在支付宝页面** | 用户困惑 | 把收银台做成独立 Activity，付款后用户按返回即关闭，订单页刷新 |

**最容易出事的一条**：轮询超时后提示"重新支付"。
异步通知可能只是迟到，用户再付一次就是重复付款。宁可让他刷新列表。

---

## 8. 真机联调与自测清单

### 8.1 先用模拟渠道跑通（强烈建议第一步）

让后端把 `.env` 的 `PAY_PROVIDER=mock`，你的端会走 `mode=direct`：
**不跳转、不连支付宝、点一下直接出票**。

- [ ] 点"去支付"→ 直接出票，不弹 WebView
- [ ] 确认走的是 `/api/pay/confirm` 分支，且 body 带了 create 下发的 `confirm_token`
- [ ] 用同一张凭证 confirm 第二次 → 403「已被使用」（验一次性）；重新 create 后可再付

先把 direct 跑通，再测 redirect。这样能把"网络/鉴权/分支逻辑"的问题
和"支付宝/WebView"的问题分开定位。

### 8.2 再测沙箱真实付款

后端切 `PAY_PROVIDER=alipay_sandbox` 后：

**设备要求（硬约束，先确认再开工）**
> 沙箱**支付宝 App 不支持 iOS、不支持 Android 14+、不支持 Android 6-、
> 也不支持模拟器**。你需要一台 **Android 7~13 真机**，
> 并安装**沙箱版支付宝 App**（普通支付宝 App 扫沙箱码无效）。

- [ ] 发起支付，WebView 打开 `pay_url`，页面显示"正在跳转到支付宝收银台…"
- [ ] 自动跳到支付宝，或用 `alipays://` 唤起沙箱版 App
- [ ] 用**沙箱买家账号**登录付款（账号在支付宝开放平台沙箱控制台，找后端要）
- [ ] 付款后 3~15 秒内，轮询返回 `paid=true`
- [ ] 订单列表里该订单变成「已出票」
- [ ] **再点一次"去支付"**，应提示已出票、无需支付（验幂等）
- [ ] 付款中途按返回 → 订单仍待支付 → 可再次发起
- [ ] 等 15 分钟不付 → 订单自动取消 → 再支付报「已取消」

### 8.3 自查手段

- 付款后让**后端查库**最直观。
- 你自己能看的是 `/api/pay/status` 的 `pay_status` 从 `待支付` → `支付成功`。
- 若一直是 `待支付` 但钱确实付了：把 **`pay_no`** 给后端查单。
  （这通常是异步通知没到，后端会在同步回跳时兜底查单。）

### 8.4 抓包注意

- 用 Charles / Fiddler 抓包时，**`pay_url` 那一跳不要改包**，签名会失效。
- 只抓你自己的 API 请求（`/api/pay/*`）就好，别拦支付宝域名。

---

## 9. FAQ

**Q：收银台一片空白 / 卡在"正在跳转"？**
A：容器没开 JavaScript。见 6.1。

**Q：打开 `pay_url` 看到 `{"stat":"fail","msg":"RefererCheckFailed"}`？**
A：你绕过了中转页、直接拿了支付宝原始链接。用后端给的 `pay_url` 原样打开。

**Q：轮询用 `pay_no` 还是 `order_no`？**
A：**`order_no`**。传 `pay_no` 返回"订单不存在"。

**Q：支付成功了但订单还是待支付？**
A：异步通知没到。后端会在同步回跳时查单兜底；仍不行就把 `pay_no` 给后端。
**不要让用户重新付。**

**Q：`mode` 会变吗？**
A：会。后端切渠道时 `redirect`/`direct` 就变了，所以两个分支都要实现。

**Q：`amount` 有时 `600.0` 有时 `600`？**
A：都是数字，JSON 里等价。按数字解析，别按字符串。

**Q：一定要用 WebView 吗？能用系统浏览器吗？**
A：可以，见 6.5。首版建议用系统浏览器先跑通。

**Q：沙箱能不能用模拟器测？**
A：**不能**。见 8.2 的设备要求。

**Q：付款成功后需要我调什么接口通知后端吗？**
A：**不需要**。后端以支付宝的异步通知为准。你只需要轮询确认结果。

---

## 10. 已知限制

### 10.1 沙箱环境的设备限制

沙箱**支付宝 App 不支持 iOS、Android 14+、Android 6-、模拟器**，
且沙箱码只能用沙箱版 App 扫。这是支付宝侧的限制，不是本项目的。

**生产环境（`PAY_PROVIDER=alipay`）没有这些限制**，用真实支付宝 App 即可。

### 10.2 沙箱收银台的地址限制

沙箱对收银台有 **Referer 白名单**，只放行本机地址（`http://127.0.0.1:5000/`）
与支付宝自身域名。**公网隧道域名会被拒**。

所以本地联调时：
- 若你的 App 在**同一台机器**上跑（模拟器），用 `http://127.0.0.1:5000` 可以；
- 若在**真机**上跑，需把 `pay_url` 的 host 换成手机可达的地址，
  但这会撞上 Referer 白名单 → 实测可能失败。
  **真机联调建议直接找后端配合**（后端可用 `CHECKOUT_BASE_URL` 调整），
  或先在生产域名下验证。

### 10.3 微信支付

微信支付 V3 **官方不提供沙箱环境**，V2 的 `sandboxnew` 已废弃。
目前项目只接支付宝；微信若要做，只能走生产小额（1 分钱）验证。

---

## 附：改动记录

| 日期 | 变更 |
|---|---|
| 2026-09-11 | 初版。从通用交接文档拆出安卓专项，补充 Kotlin 完整代码、WebView 五类坑、真机设备约束 |
| 2026-09-12 | 写接口启用服务端 HITL：`/api/pay/create` 响应新增 `confirm_token`，`/api/pay/confirm` 必须回传（redirect 分支不受影响）。见 1.4 / 4.4，代码示例已同步 |
