# 航班客服系统真实化改造计划
### SQLite 模拟真实数据 × LLM 自主工具调用（ReAct）× 前端工具动画

> 文档定位：把 2026-08-29 与用户讨论收敛的需求整理成可执行计划。
> 参考项目：`D:\langgraph\03-project-TripMate-AI-...`（多 Agent 旅行规划、create_react_agent、ChatOpenAI、SQLite checkpointer）
> 　　　　　`D:\langgraph\02-Agentic-Chatbot-using-LangGraph`（Streamlit 前端、st.status 工具动画、HITL）

---

## 0. 背景与问题（已确认）

1. **工具全是硬编码模拟数据**（`tools/mcp_tools.py`）：
   - `flight_search`：静态航线表（北京→上海固定 3 班、固定价格）
   - `weather_query`：静态"气候介绍"表，**忽略 date 参数**，不是天气
   - `delay_prediction`：固定公式（基础 15% + 航司系数）
   - `price_trend`/`price_composition`：固定建议文本 / 固定比例公式
2. **LLM 从不真正调用工具**（`skills/intent_router.py`）：规则+Embedding 判意图 → 意图硬编码映射工具 → 正则抠参数 → 直接执行 → 结果塞 prompt 让 LLM 复述。无 function calling，模型无决策权。
3. **机会**：（a）参考项目 03 证明了 `create_react_agent` + 官方 `ChatOpenAI` 的成熟路径；（b）参考项目 02 证明了"工具调用动画"= 工具事件流 + 状态组件，成本很低。
4. **用户目标**：航班背景的多 Agent 客服系统；**机票信息存本地 DB 模拟真实数据**；LLM **真正调用工具**；前端**保留现有界面**（方案 B，不换 Streamlit）并**增加工具调用动画**。

---

## 1. 总体架构：保留与替换

### ✅ 保留（存量资产，不动）
| 模块 | 说明 |
|---|---|
| LangGraph 主图 | `multi_agent_customer_service.py`：sensitive_word_filter → intent_router → 条件路由 → agents → final_response |
| 路由面 | 敏感词过滤、多意图识别、情绪标签/工单 |
| Flask 层 | `web_app.py` + `chat_web_service.py`（含本地回退、runs/stream Token 流） |
| 前端 | `templates/index.html`（2138 行单文件：会话侧栏/删除/Markdown/Token 打字机） |
| 会话/记忆 | session_manager、enhanced_memory |

### 🔄 替换（本次改造对象）
| 模块 | 现状 | 目标 |
|---|---|---|
| LLM 客户端 | `agents/__init__.py` 自定义 `OpenAICompatibleLLM(BaseChatModel)` | 切换官方 `langchain_openai.ChatOpenAI`（`deepseek-chat`，原生 function calling + 流式）；保留一个工厂函数供替换 |
| 数据层 | 类内静态字典 | **SQLite 本地库** `data/flight_system.db` + 种子生成器 + Repository 层 |
| 工具层 | `MCPTool` + 硬编码执行 | **按 function-calling schema 注册的工具**（`@tool` 包装），参数结构化 |
| Agent 内部 | `process()` 一次性 invoke | **ReAct 工具循环**：模型自主决定调用工具/填参数，工具结果回灌，直至最终回复（上限轮数 + 规则兜底） |
| 意图路由 | 唯一决策者（决定工具+参数） | 降级为 **Agent 选择与兜底**：模型优先调用工具，路由负责"该走哪个 Agent"与参数解析兜底 |

---

## 2. 数据层设计（Phase 1）

**位置**：`data/flight_system.db`（SQLite，首次启动自动构建）
**文件**：`services/db.py`（连接/建表）、`services/db_seed.py`（种子生成）、`services/flight_repo.py`（查询封装）

### 2.1 Schema（模拟真实民航系统口径）
| 表 | 字段要点 | 说明 |
|---|---|---|
| `airports` | iata3, icao4, city_cn, city_en, lat, lon, timezone | 20+ 国内城市机场 |
| `airlines` | code(CA/MU/CZ/9C/HO…), name_cn | 含廉航/全服务分类 |
| `flights` | flight_no, airline, dep_iata, arr_iata, dep_time, arr_time, aircraft, freq_days | 班期（每周几执飞） |
| `flight_prices` | flight_no, flight_date, cabin(经济/商务), price, currency | **每日每舱价格**（支撑真实感与趋势） |
| `delay_stats` | airline, route, time_bucket, mean_delay_min, delay_prob | 延误统计维度（供预测） |
| `customers` | member_id, name, phone, email, level | 会员表 |
| `orders` | order_no, member_id, flight_no, flight_date, cabin, amount, status(已出票/已退款/改签/退票中), created_at | **订单表 → billing 真实化** |
| `complaints` | ticket_no, member_id, order_no, content, status, created_at | **投诉表 → complaint 真实化** |
| `city_coords` | city, lat, lon | 天气工具用（Open-Meteo 坐标） |

### 2.2 种子策略（"看着像真的"且可复现）
- **固定随机种子**（如 `SEED=42`），全量可复现；
- 航班：主要航线（京沪/京广/沪深…）× 每航线每天 3-6 班 × **未来 30 天**（按 `freq_days` 生效）；
- 价格：基准价 × 提前期折扣(30-60 天最优→临期上涨) × 季节系数 × 时段波动，生成波动自然的价格序列 → `price_trend` 有真数据可查；
- 延误：按航司×航线的统计分布生成（全服务航司概率低、廉航高）→ `delay_prediction` 有统计口径；
- 订单：30-50 个模拟会员 × 随机航班/舱位/状态（含已退款、待退款）；
- **幂等**：`db_seed` 检查表已存在数据则跳过（或按日期增量补足"今天起 30 天"）。

### 2.3 天气数据
- `weather_query` 走 **Open-Meteo 真实 API**（免费无 key）：`city_coords` 城市→经纬度 → 实时+7 天预报；可选结果落库缓存（`weather_cache` 表，30 分钟 TTL）。

---

## 3. 工具层设计（Phase 2）

### 3.1 工具清单（function calling schema）
| 工具 | 参数（结构化） | 数据来源 | 服务意图 |
|---|---|---|---|
| `search_flights` | departure, destination, date, passengers | flights+flight_prices | product_info |
| `get_price_trend` | from_city, to_city, days_ahead | flight_prices 日期序列统计 | price_trend |
| `get_delay_prediction` | route, airline, date | delay_stats+（可选真实天气） | delay_prediction |
| `get_weather` | city, date | Open-Meteo API | destination_weather |
| `get_flight_price_detail` | flight_no, date | flight_prices 拆分 | price_composition |
| `get_order_bill` | member_id 或 order_no | orders | billing |
| `query_complaint` | ticket_no / member_id | complaints | complaint |

### 3.2 调用策略（混合，参考 03 的模式）
- **主路径**：模型 function calling 自主调用（参数由模型填，语义最准）；
- **保底路径**：① 工具实现内部做宽松解析/校验（缺参时用意图路由预解析的槽位补全）；② 模型调用失败或超轮次 → 直接以意图路由（现 `_parse_*_params`）解析的参数执行工具并把结果交给模型复述（即现有行为，作为兜底保留）；
- 工具返回统一结构 `{success, data, error}`，错误信息友好化（**不向模型泄漏 SQL/内部细节**）。

---

## 4. Agent ReAct 化（Phase 3）

### 4.1 结构
```
现有图不变：sensitive_word_filter → intent_router → 条件路由：
   product_agent / billing_agent / complaint_agent / general_agent → final_response
每个 Agent 节点内部 → ReAct 工具循环（本方案唯一大改点）
```

### 4.2 工具集分配
| Agent | 绑定工具 |
|---|---|
| product_agent（航班专家） | search_flights / get_price_trend / get_delay_prediction / get_weather / get_flight_price_detail |
| billing_agent（订单账单） | get_order_bill |
| complaint_agent（投诉） | query_complaint / get_order_bill |
| general_agent（综合） | 无工具（或仅 weather） |

### 4.3 实现路线（待实测二选一）
- **路线 A（推荐先试）**：`langchain.prebuilt.create_react_agent(ChatOpenAI, tools, prompt)` 子图，嵌入现有节点函数（`agent.process` → `react_agent.invoke(消息)`）。代码量最小、官方维护、流式（`stream()`）与 tool_calls 全原生。
- **路线 B（备选）**：自实现循环：`llm.invoke(messages, tools=...)` → 有 `tool_calls` 则执行工具、追加 `role:"tool"` 消息、再调 → 直到最终回复。依赖 OpenAI 兼容 `tools/tool_calls` 协议；需要时可控性最强。
- 统一约束：**最多 3-5 轮工具循环**；循环内不重复调用同一工具参数组合超过 2 次；异常 → 降级 4.4 兜底。

### 4.4 兜底链路（保证任何情况下都有回复）
模型调用失败 / 超轮次 / 无可用工具 → 沿用现有 `_execute_agent_node` 逻辑：
意图路由工具结果 + 无工具直接生成（与当前行为一致）→ final_response。

### 4.5 流式兼容
- `ChatOpenAI` 的 `.stream()` 与现有 LangGraph `runs/stream + stream_mode="messages"` **天然兼容**（`on_chat_model_stream` 回调路径一致），现有 Token 打字机无需改动；
- 多意图并行（ThreadPoolExecutor 线程）下 token 流可能交错：**本轮先降级**（多意图路径工具结果非流式、最终答复流式），串行化列入后续优化。

---

## 5. 前端工具动画（Phase 4）

### 5.1 后端事件流（`chat_web_service.py`）
- `POST /threads/{tid}/runs/stream` 的 `stream_mode` 由 `["messages"]` 扩展为 **`["messages","updates"]`**；
- 解析 `updates` 事件（每节点完成时回发 `{node: {...}}`）：从节点输出中的 `tool_results`（或未来 ToolNode 输出）提取本次工具执行信息，新增 SSE 事件：
  ```
  data: {"tool": {"name": "search_flights", "status": "running", "args": {...}}}
  data: {"tool": {"name": "search_flights", "status": "done", "summary": "找到 5 个航班"}}
  ```
- 事件顺序天然先于正文 token（工具先执行、模型后写答案）。

### 5.2 前端渲染（`templates/index.html`，约 100 行 JS/CSS）
- 助手气泡内新增**状态芯片**：CSS 旋转图标（`@keyframes`）+"🔧 正在查询航班信息…"；
- 收到 `tool.running` → 显示并可展开（列出工具名/参数）；`tool.done` → 绿色对勾收拢；`tool.error` → 黄色警告；
- 与现有打字机共存：工具阶段 chip 可见，正文 token 开始后自动收起为完成态；
- 非流式回退路径不作动画（仅提示"已查询"）。

---

## 6. 分阶段实施与验收

| 阶段 | 内容 | 独立验收标准 |
|---|---|---|
| P1 数据层 | SQLite 建库/种子/Repository | `python -c "from services import flight_repo; print(flight_repo.search('北京','上海','2026-09-01'))"` 返回多班真实感数据；重跑幂等 |
| P2 工具层 | DB 工具+Open-Meteo+schema 注册 | `python -c "from services.tools import registry; ..."` 逐工具手动调用通过；天气返回真实数据；断网时天气有兜底文案 |
| P3 Agent ReAct | 官方 ChatOpenAI+工具循环+兜底 | curl `/api/chat` 问"查明天北京到上海航班"：服务端日志可见模型发起 `search_flights` 调用（而非规则预执行）；参数正确 |
| P4 流式+动画 | updates 解析+SSE 扩展+前端芯片 | curl `-N /api/chat/stream` 可见 `tool` 事件先于正文；浏览器可见旋转芯片→勾选收起 |
| P5 全量回归 | 本计划全部+既有功能 | 既有 11 项验收（Web/连通/聊天/去重/列表/删除/浏览器/回退）+ 流式+动画全部通过 |

### P5 重点回归用例
1. "我想查明天北京到上海的航班" → 模型自主调 `search_flights`（日志确认参数来自模型），回复含真实票价，前端见动画；
2. "上海明天天气怎么样" → `get_weather` 真实 Open-Meteo 数据；
3. "这个月账单" → `get_order_bill` 返回订单明细（billing 真实化）；
4. "我要投诉工作人员" → `query_complaint` + 工单流转；
5. 敏感词场景、多意图场景、会话列表/删除/历史、本地回退（停 lg dev）、断 DB 降级——全部不回归。

---

## 7. 风险与开放问题（实施前需最终拍板）

| # | 问题 | 现状/倾向 |
|---|---|---|
| 1 | LLM 客户端切换 | **拟切官方 `ChatOpenAI`**（`OPENAI_BASE_URL` 已指向 deepseek；`deepseek-chat` 支持 function calling）。需验证其在流式 runs/stream 下 token 行为与现有 BaseChatModel 一致（实测决定 A/B 路线） |
| 2 | 多意图并行流 | 本轮接受"并行路径非流式"降级；纯流式串行化列入后续 |
| 3 | 天气真实 API | Open-Meteo 无 key 免费；需处理网络失败兜底（离线时返回模拟文案） |
| 4 | 价格/延误"真实感"口径 | 无免费真实数据源，统计口径为"模拟但科学"（菜单：未来可换成供应商 API） |
| 5 | HITL 人工审批 | **不在本次范围**；`interrupt()` 审批流后续单独立项（参考 02 的 HITL，做进原前端） |
| 6 | Electron/前端动画细节 | CSS 芯片样式与 UI 融合；不引入新前端框架 |
| 7 | 既有测试脚本 | `test_system.py`/`test_langgraph.py` 可能依赖旧工具行为，P3 后需同步修/标注 |

---

## 8. 明确不做（防止蔓延）
- ❌ 不换 Streamlit/不上新前端框架（方案 B 已定）；
- ❌ 不接付费航班 API（Amadeus/Skyscanner 等）；
- ❌ 不做真实支付/出票/退改业务（订单仅为数据层）；
- ❌ 不改主图结构、会话管理、前端既有交互；
- ❌ 不引入新的第三方依赖（除 `langchain-openai`；SQLite/requests 均为既有）。
