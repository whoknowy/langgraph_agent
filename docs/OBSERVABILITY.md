# 可观测性 · 节点链 + Langfuse 接入指南

> 阶段三交付物：覆盖验收项 **④ Langfuse 链路可视化** 与 **⑤ 状态机可观测**。

## ① 前端"执行链路"面板（验收⑤）

聊天页右上角悬浮按钮 **⛓ 链路** 打开右侧面板，实时展示本轮对话经过的图节点与工具调用：

```
┌─ 执行链路 ────────────────┐
│ 路由 → 账单专家           │
│ ● 敏感词守卫       12ms    │
│ ● 意图分类          87ms    │
│ ● 账单专家         1.2s     │
│   ├ ✓ 账单查询    420ms    │  ← 点"▸ 参数"展开 JSON
│   ├ ✓ 退票确认    180ms    │
│ ● 最终响应         15ms    │
│ ● 实时执行中…             │
└───────────────────────────┘
```

- **实时点亮**：每个节点进入时立刻出现转圈 spinner，完成后变绿勾 + 显示 `ms`；
- **工具嵌套**：工具调用按"它发生在哪个 Agent 节点下"嵌进对应行；
- **历史回看**：助手消息右下角点 **⛓ 链路** 按钮回看该轮链路（在内存里；刷新后清空 — 见 [T3.4 后续工作](#t34-后续工作)）；
- **守卫拦截提示**：若输入被敏感词守卫拦截，链路止步于守卫，并显示红字 "⛔ 输入已被拦截"。

实现位置：`frontend/src/client/components/TracePanel.vue`（面板）+ `ChatView.vue`（事件消费）。

后端 SSE 额外下发了 `{"node": ...}` 与 `{"tool": ...}` 事件，并在最后一条 `done` 事件里带 `trace` 汇总（含节点耗时 / 工具入参 / 守卫拦截 / 路由结果）。新增/修改：

| 文件 | 变更 |
| --- | --- |
| `services/trace_events.py` | 新增：节点 / 工具跟踪纯逻辑（`RunTrace`），可单测 |
| `chat_web_service.py` | `stream_chat_tokens` 下发 node/tool 事件；done 带 trace；本地兜底链路也带 |
| `multi_agent_customer_service.py` | （无侵入，只通过 `_fetch_thread_final` 多读 `guard_blocked` / `target_agent`） |

## ② Langfuse 接入（验收④）

### 一次性准备

```bash
# 1. 启动自建 Langfuse（postgres + clickhouse + redis + minio + langfuse-web/worker）
docker compose up -d

# 等待约 1~2 分钟；UI 入口：http://localhost:3000
# 用预设账号登录：admin@demo.local / demo123456
# （compose 已通过 LANGFUSE_INIT_* 自动建好组织 / 项目 / 管理员 / 密钥，
#  首次启动后 Settings → Projects 能直接看到 pk-lf-airline-demo / sk-lf-airline-demo-secret）

# 2. 把密钥写进 .env
#   LANGFUSE_PUBLIC_KEY=pk-lf-airline-demo
#   LANGFUSE_SECRET_KEY=sk-lf-airline-demo-secret
#   LANGFUSE_HOST=http://localhost:3000      （默认已写好）
# 取消 .env 里 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 两行的注释

# 3. 重启 LangGraph dev（langgraph 进程不热加载，重启才生效）
#    先找到旧进程，再起新的：
ps -W | grep langgraph    # Windows 下找 .venv/Scripts/langgraph.exe
.venv/Scripts/langgraph.exe dev --no-browser --no-reload > lg_dev.log 2>&1 &

# Flask 端会自动重载（debug=True），无需手动重启。
```

### 在 Langfuse UI 能看到什么

- **Sessions 视图**：按 `session_id`（= LangGraph 线程）分组，每个会话一条 trace 链；
- **Trace 详情**：展开后看到完整调用链——`intent_classifier` → `agent:billing_agent`（一个 Span）→ 子观测 `ChatOpenAI generation`（Prompt + 模型 + token + 耗时）→ 子观测 `tool:get_order_bill` / `tool:refund_request`（入参 + 出参）；
- **成本与耗时**：每次 LLM 调用的 prompt token、completion token、duration 都会列出来（DeepSeek 等 OpenAI 兼容接口都会带 usage）；
- **与 LangSmith 并存**：两个观测系统同时上报不冲突，看哪边顺手都行。

### 关闭 / 降级

- 把 .env 里 `LANGFUSE_PUBLIC_KEY` 注释掉（或置空）→ `is_enabled()` 返 False，
  所有观测相关代码立即降级为 no-op，主业务完全不受影响；
- 服务启动日志会显示：
  ```
  🔭 Langfuse 轨迹观测未启用（docker compose up -d 启 Langfuse 后在 .env 填 LANGFUSE_*）
  ```

## ③ 设计要点

- **按图节点聚 trace**：`BaseAgent._run` 把整个 ReAct 循环包在
  `langfuse_setup.trace(name="agent:billing_agent", session_id=..., user_id=...)` 里，
  配合 `start_as_current_span` 的 OTEL 上下文，让该 Agent 节点下多次 LLM 调用与
  工具调用全部嵌套为同一 trace 的子 observation——直观展示"一张图 = 一条 trace"。
- **LangSmith 共存**：LangSmith 通过环境变量 `LANGSMITH_*` 启用（既存功能），
  两套 callback 同时挂着，不冲突。
- **生产凭据**：自建 Langfuse 用本 compose 内嵌的 MinIO / Postgres 等；
  生产建议改用托管版（langfuse.com），把 `LANGFUSE_HOST` 指过去即可，
  `.env` 换成托管版的密钥。

## T3.4 后续工作

历史会话回看链路（持久化到 checkpoint）尚未实现——目前链路只在内存里，
刷新页面后只保留有 trace 的当前会话。补全方式：把工具调用写入图 state
（`agents/base_agent.py` 的 ReAct 循环），恢复会话时从 `state["messages"]`
里读出。已记入 `docs/ACCEPTANCE_AUDIT.md` P1 清单。