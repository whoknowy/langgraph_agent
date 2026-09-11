# ✈️ 智能航空客服系统（多智能体 × LangGraph × 真实数据）

一个**在航班场景下可真实落地的多智能体客服系统**：5 个业务智能体通过 LangGraph 编排，
基于本地 SQLite 模拟真实民航数据（航班/票价/订单/投诉），LLM 通过 ReAct 循环自主调用
真实工具查数据、下订单，配合管理员运营平台形成完整业务闭环。

> 客户端 <http://127.0.0.1:5000> · 管理端 <http://127.0.0.1:5000/admin>

## 功能总览

### 客户端（会员）

| 能力 | 说明 |
|---|---|
| 会员登录/注册 | 演示账号「会员号+手机尾号」一键登录；**支持自助注册**（手机号+密码，口令哈希存储），注册后可用密码登录；登录身份自动注入对话，**无需再报会员号** |
| 查询 | 航班/票价、价格构成、目的地天气（Open-Meteo 真实预报）、延误预测、价格趋势、**联网搜索**（博查：景点门票/活动/交通等实时信息）、订单账单、投诉记录 |
| 订票 | 查票 → **确认卡片**（实时报价）→ 确认下单（待支付）→ 支付出票 |
| 值机选座 | 订单「已出票」且在值机窗口（起飞前24h~45min）→ 座位图点选 → 原子占座 → **电子登机牌**（座位/登机口/登机时间）；支持改座；改签/退票自动取消值机释放座位 |
| 改签 | 智能体自动推荐同航线可改航班 → 卡片确认 → 免改签费、差价多退少补 |
| 退票 | **自愿退**：按公示费率（5%~30% 分档）即时退款；**特殊退**（延误/取消）：人工审核队列 |
| 投诉 | 查询进度；新投诉即时落库返回单号，管理端回复后客户可查到处理意见 |
| 行程规划 | 旅行规划师：真实航班+真实天气生成结构化行程（概览/航班表/逐日安排/预算/提示）；看中哪班**直接说"就订这班"即转订票确认卡片** |
| 站内通知 | 退款审批结果、订单超时取消、投诉回复等主动触达：面板未读红点 + 智能体可代为转告 |
| 我的数据 | 订单/投诉/通知面板，行内支付、退票操作 |

### 管理端（/admin）

工作台统计 · 退款审批（同意/驳回，改签单驳回后正确恢复「已改签」）· 投诉处理（回复解决/升级/重开）·
航班上架（自动生成未来 30 天双舱票价）· 机场/航司维护 · 订单全局查询 · 会员查询 ·
工作台图表（7 天订单/退款趋势 + 热门航线 Top5 + 今日值机）。
管理动作自动产生会员站内通知；订单生命周期后台任务：起飞自动「已使用」、**待支付超 15 分钟自动「已取消」并通知**。

演示账号：会员 `M1001 / 2835`（尾号），管理员 `admin / admin123`（**首次登录强制修改密码**，
之后以新密码登录）。可选接入 LangSmith 轨迹观测（`.env` 两个变量，见 GETTING_STARTED）。

## 架构

```
START → 敏感词守卫 ──L3高危──→ 拦截话术 → END
          ↓ 放行
        LLM 意图分类（单次调用，5 选 1）
          ↓ 条件路由
┌ 机票专家   ── ReAct：search_flights / 票价构成 / 天气 / 延误 / 趋势 + 订票确认卡片
├ 账单专家   ── ReAct：账单查询 + 退票/改签确认卡片
├ 投诉专家   ── ReAct：投诉查询/登记（create_complaint 落库）
├ 旅行规划师 ── ReAct：真实航班+天气 → 结构化行程
└ 综合客服   ── 纯对话兜底
  ↓
final_response → END
```

**设计要点**

- **真实数据**：航班/票价/延误统计/会员/订单/投诉全部存本地 SQLite（首次启动自动种子 26 机场、
  10 航司、300+ 航班、未来 30 天票价），天气接 Open-Meteo 实时预报。LLM 不编造数据。
- **ReAct 共享工具池**：智能体在一个流式循环里自主决定调用哪些工具、传什么参数；
  多需求一句话（查航班+天气）在一段回答里自然完成。
- **确认卡片**：订票/退票/改签等写操作，LLM 只负责收集参数与解释，
  通过伪工具产生确认卡片，用户点击后由 REST 接口在代码层执行——写库路径不经过 LLM。
- **工具层硬安全**：登录身份经 ContextVar 受信通道注入工具执行上下文，
  仓库层校验数据归属——提示词诱导无法越权查询/操作他人数据。
- **订单状态机**：`待支付 → 已出票 ⇄ 已改签 →（起飞自动）已使用`，自愿退即时 `已退款`，
  特殊退经 `退票中` 由管理端审批；每一步状态校验 + 归属校验。
  值机独立于状态机（checkins 表记录座位/登机口），改签/退票自动取消值机释放座位。
- **确认对话双记忆**：会话历史由 LangGraph 线程 checkpoint 承载（多轮追问可续），
  业务数据以 SQLite 为事实源。

## 快速开始

```bash
# 1. 安装依赖（建议 Python 3.10+，可用 venv）
pip install -r requirements.txt

# 2. 构建 Vue 3 前端（一次性；开发时可用 npm run dev 热更新）
cd frontend
npm install
npm run build
cd ..

# 3. 配置环境变量
cp .env.example .env        # 填入你的 OPENAI_API_KEY（DeepSeek/SiliconFlow 均可）

# 4. 启动 LangGraph 服务（智能体运行时，端口 2024）
langgraph dev --no-browser --no-reload

# 5. 启动 Web 服务（端口 5000；同时拉起订单生命周期后台任务）
python -c "import web_app; web_app.app.run(host='0.0.0.0', port=5000, debug=False)"
```

首次启动自动建库并生成种子数据。Windows 下若 langgraph dev 文件监听崩溃，
改文件后需手动重启。

> 📖 更详细的分步启动指南（环境要求、.env 配置、启动验证、30 秒功能体验路线、
> 常用操作与故障排查表）见 [GETTING_STARTED.md](GETTING_STARTED.md)。

## 测试

三层测试，按 token 成本分层使用：

| 层级 | 命令 | 耗时 | Token | 何时跑 |
|---|---|---|---|---|
| 单元测试 | `python -m pytest test_unit.py -q` | ~5秒 | **0** | **每次改代码后随便跑**（费率/状态机/注册/越权/通知/搜索解析/标题/趋势聚合/值机选座/JWT/支付幂等 等 138 用例，独立临时库） |
| 管理端回归 | `python test_admin.py` | ~1分钟 | ≈0（纯 REST+DB） | 涉及管理端/订单流改动时 |
| 客户端回归 | `python test_regression.py --go` | ~10分钟 | **高**（20+ 轮真实 LLM 对话） | 仅在演示前/里程碑，且经明确确认后 |

**Token 纪律**：客户端回归默认被门禁阻止，需显式 `--go`（或 `REGRESSION_GO=1`）才会运行；
不带参数执行只打印零 token 替代方案提示。日常迭代循环：改代码 → `pytest test_unit.py`
秒级验证 → 手测界面 → 提交；`test_regression.py` 留到关键节点，且跑之前先问一句。

## 支付

渠道可插拔，一份编排层适配三种模式（代码在 `services/payment/`）：

| `PAY_PROVIDER` | 渠道 | 流程 |
|---|---|---|
| `mock`（默认） | 模拟 | 站内一步付讫，零外部依赖，跑测试/演示用 |
| `alipay_sandbox` | 支付宝沙箱 | 真实收银台 → 异步通知 → 落账 |
| `alipay` | 支付宝生产 | 同上，仅网关与密钥不同 |

**分层**：`base.py` 定义渠道接口 → `mock_provider.py` / `alipay_provider.py` 实现 →
`payment_service.py` 编排（不依赖 Flask request，可直接单测）→ `payment_repo.py` 落库。

**幂等是这块的重点**：异步通知会被重投最多 8 次，落账唯一出口 `mark_paid()`
用 `UPDATE ... WHERE status='待支付'` 的 `rowcount` 做乐观锁，
只有首次翻转返回 True，调用方据此才推进订单——重投多少次都只出票一次。
金额与 `app_id` 在验签通过后还要再比对一次，防止他人商户号伪造"已支付"。

**支付场景**：支付宝侧用 `ALIPAY_SCENE` 切换两个接口，无需改代码——
`page`（默认，`alipay.trade.page.pay` / `FAST_INSTANT_TRADE_PAY`）走 PC 浏览器收银台；
`wap`（`alipay.trade.wap.pay` / `QUICK_WAP_PAY`）走手机网站支付，
手机浏览器 / 安卓 WebView / 小程序 web-view 用这个。
沙箱环境目前设为 `wap`——沙箱的 `page` 收银台页面引用了蚂蚁内网资源
（`stable.alipay.net`），公网 DNS 解析失败会导致「能进收银台但点付款请求失败」。
联调时可看启动日志里渠道的 `label`（`wap` 会显示「支付宝沙箱（手机网站支付）」）确认。

**密钥**：环境变量 `ALIPAY_PRIVATE_KEY` / `ALIPAY_PUBLIC_KEY` 优先（生产推荐，不落盘），
未设置时回退到 `keys/*.txt`。`keys/` 只有 README 入库，密钥文件已被 `.gitignore` 排除。
详见 [keys/README.md](keys/README.md)。

**本地回调需要公网地址**（支付宝要能访问到你的机器）：用 NATAPP 之类的内网穿透，
把 `ALIPAY_NOTIFY_URL` 配成 `http://<你的域名>/api/pay/notify/alipay`。

**多端接入**：支付接入文档按端分开——
**安卓**看 [docs/PAYMENT_ANDROID.md](docs/PAYMENT_ANDROID.md)（Kotlin 完整代码、
WebView 五类坑、真机设备约束），**微信小程序**与通用认知看
[docs/PAYMENT_HANDOFF.md](docs/PAYMENT_HANDOFF.md)；
接口字段速查在 [docs/API.md](docs/API.md) 的 4.4.3。

## 多端接入（安卓 / 微信小程序）

> **给多端开发同学的完整接口文档见 [docs/API.md](docs/API.md)**（认证方式、全部接口字段、
> 订票/值机/退改三大业务流程教程、常见踩坑），接前必读。

公网只暴露 Flask（5000）一个端口，LangGraph 服务（2024）无鉴权，必须留在内网。
公网固定地址：`http://flightagent.nat100.top`（NATAPP 付费隧道 → 127.0.0.1:5000）。

### 认证：JWT Bearer Token（与 Web Cookie 并存）

1. 登录拿 token：`POST /api/login`（或 `/api/register`、`/admin/api/login`），响应体含 `token`；
2. 之后每个请求带请求头 `Authorization: Bearer <token>`；
3. Web 端 Cookie 会话照常可用，两通道等价互为回退；token 有效期 7 天，收到 401 重新登录即可。

### 常用接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 非流式对话（小程序推荐），响应含 `response` / `pending_action` |
| POST | `/api/chat/stream` | SSE 流式对话（`text/event-stream`，安卓用 OkHttp 流式读） |
| GET / DELETE | `/api/sessions`、`/api/sessions/<id>` | 会话列表 / 详情（含 `conversation_history`）/ 删除；另有 `/clear`、`/api/new_session` |
| POST | `/api/book` `/api/pay` `/api/change` `/api/refund` | 确认卡片对应的下单 / 支付 / 退改写接口 |
| GET / POST | `/api/checkin/seats` `/api/checkin` `/api/checkin/boardpass` | 座位图 / 值机改座 / 登机牌 |
| GET | `/api/my/orders` `/api/my/complaints` `/api/my/notifications` | 我的数据（拉取式，无推送） |

- `pending_action` 非空即表示智能体发起了确认卡片（订票/退改/选座），客户端展示卡片，
  用户确认后调用对应写接口；SSE 通道在流结束时以同名事件下发，语义一致；
- 微信小程序不支持流式读取，直接用非流式 `/api/chat` 即可拿全量结果。

## 项目结构

```
├── web_app.py                    # Flask 入口：会员/管理端路由、登录门禁、安全上下文绑定
├── chat_web_service.py           # LangGraph 通信层：SSE 流式、线程管理、确认卡片事件
├── multi_agent_customer_service.py # LangGraph 图：守卫→分类→5智能体→最终响应（含本地兜底）
├── config.py                     # LLM 连接配置（环境变量驱动）
├── langgraph.json                # LangGraph CLI 配置（图入口 customer_service）
├── agents/                       # 5 个业务智能体 + 基类（ReAct 循环/身份注入/工具钩子）
│                                 #   + 敏感词词库与 AC 自动机
├── skills/                       # 意图分类器（单次 LLM）+ 敏感词守卫
├── services/                     # 数据与业务层
│   ├── db.py / db_seed.py        #   SQLite 建表/幂等迁移/种子数据
│   ├── flight_repo.py            #   航班/订单/退改签/投诉（含归属校验、费率规则）
│   ├── admin_repo.py             #   管理端数据操作
│   ├── security.py               #   受信身份通道 + 归属硬校验
│   ├── token_auth.py             #   JWT 签发/校验（多端 Bearer 通道）
│   ├── lifecycle.py              #   起飞→「已使用」后台任务（超时订单顺带关闭支付流水）
│   ├── payment/                  #   支付渠道抽象：base 接口 + mock / alipay 实现
│   ├── payment_repo.py           #   支付流水仓储（幂等落账唯一出口 mark_paid）
│   ├── payment_service.py        #   支付编排：下单/回调/确认/查单（不依赖 Flask request）
│   └── tools.py                  #   @tool 注册表（智能体可调用的全部工具）
├── frontend/                     # Vue 3 + Vite 工程化前端（客户端 / 管理端两个入口）
│   ├── src/client/              #   会员端：登录、AI客服、机票预订、值机、登机牌、我的数据
│   ├── src/admin/               #   管理端：工作台、退款、投诉、航班、订单、会员
│   └── dist/                    #   npm run build 产物，Flask 自动优先服务
├── docs/API.md                   # 多端接入接口文档（安卓/小程序同学必读）
├── docs/PAYMENT_ANDROID.md       # 安卓端支付接入专项（Kotlin + WebView）
├── docs/PAYMENT_HANDOFF.md       # 支付接入通用交接（含微信小程序）
├── test_regression.py            # 客户端回归
├── test_admin.py                 # 管理端回归
└── data/flight_system.db         # 本地数据库（自动生成）
```

## 路线图

- ✅ 会话自动起标题（LLM 生成，失败兜底首问截断）
- ✅ 管理端工作台图表（7 天订单/退款趋势 + 热门航线 Top5，纯 CSS）
- ✅ 功能C：值机选座 + 电子登机牌（座位图/值机窗口/原子占座/改座/联动释放）
- ⬜ 下一轮候选：不正常航班（延误/取消）主动服务包——管理端事件台 + 批量通知 +
  智能体代办免费改签/特殊退 + 补偿规则（现有特殊退票通道即为其一环）

## 已知边界

- 会话线程按登录会员隔离（session_owners 表登记归属）：列表/详情/删除/清空仅限本人线程，
  聊天传入他人线程 ID 会自动落到自己的新线程；存量无主线程不再对任何会员暴露；
- 会话历史由 langgraph dev 持久化到本地（.langgraph_api/），**重启进程后聊天记录保留**（超过30天不活跃的会话由 TTL 策略清理）；业务数据在 SQLite 不受影响；
- 目的地天气预报最多未来 7 天，行程规划中超期日期会明确提示；
- 登录凭证为演示级（手机尾号），退款为全额可改金额（未含分档退票费之外的差旅政策）。
