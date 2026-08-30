# 拼装计划书：customer-service-ai-agent 前端 × Customer-Agent 后端

> 本文档自包含，执行者无需其他上下文。请从头到尾按顺序执行，**所有"验收标准"必须逐条通过**才算完成。

## 1. 背景与目标

本地有两个同源演化的多智能体客服项目：

| 项目 | 路径 | 特点 |
|---|---|---|
| customer-service-ai-agent | `D:\langgraph\customer-service-ai-agent` | 前端 UI 好（单文件 `templates/index.html`，2134 行，无 Jinja 变量依赖，纯静态）；Flask 层（`web_app.py` + `chat_web_service.py`）是旧版的增强超集 |
| Customer-Agent | `D:\langgraph\Customer-Agent` | 前端差；后端（LangGraph 图 + agents/skills/memory 等）功能更强（敏感词过滤、多意图路由等），**要保留** |

两个项目的 Flask 层对 LangGraph 服务（127.0.0.1:2024，图名 `customer_service`）的 REST 调用方式完全一致，HTTP 契约基本同构，因此拼装成本很低。

**目标**：用方案 B——把 customer-service-ai-agent 的 Flask 层 + 前端模板整体搬入 Customer-Agent，替换其旧 `web_app.py` 和旧模板；Customer-Agent 的图（`multi_agent_customer_service.py`、`agents/`、`skills/`、`memory/`、`tools/` 等）全部保留不动（除第 5 节的一个 bug 修复）；并保留旧版独有的"本地回退"能力。

**明确不要做的事**：
- 不要把 customer-service-ai-agent 的 `multi_agent_customer_service.py`、`multi_agents/`、`tools/`、`session_manager.py`、`config.py`、`langgraph.json` 复制过来。
- 不要安装新依赖（flask / requests / python-dotenv 双方都在用，Customer-Agent 环境已装好，目录里有 pip_install.log 表明跑过）。
- 不要改动 Customer-Agent 的 `.env`、`langgraph.json`。

## 2. 前置确认

1. 确认两个目录都存在且结构如上表。源模板 `D:\langgraph\customer-service-ai-agent\templates\index.html` 应为约 2134 行的单文件。
2. 确认 2024 端口当前没有运行**别的项目的** `langgraph dev`。两个项目的 `langgraph.json` 注册的图都叫 `customer_service`，如果 2024 上跑的是 customer-service-ai-agent 的服务端，接口会"看似正常"但背后是错误的智能体。必要时先杀掉再从 Customer-Agent 目录重启。
3. 确认 Customer-Agent 的 `multi_agent_customer_service.py` 中存在 `process_customer_query`（约 733 行）和 `final_response_node`（约 422 行）——第 4、5 节要用。

## 3. 文件操作（先备份，后复制）

在 `D:\langgraph\Customer-Agent` 下执行：

```bash
cd /d/langgraph/Customer-Agent
cp web_app.py web_app.py.bak
cp templates/index.html templates/index.html.bak
cp multi_agent_customer_service.py multi_agent_customer_service.py.bak

cp /d/langgraph/customer-service-ai-agent/web_app.py ./web_app.py
cp /d/langgraph/customer-service-ai-agent/chat_web_service.py ./chat_web_service.py
cp /d/langgraph/customer-service-ai-agent/templates/index.html ./templates/index.html
```

复制后快速自检：
- 新 `web_app.py` 顶部 `from chat_web_service import (...)` 能解析（同目录）。
- 新 `web_app.py` 末尾有 `from config import *`，Customer-Agent 自己的 `config.py` 不动，旧 `web_app.py` 同样写法且能跑，风险为零。
- `python -c "import web_app"` 不报错（在 Customer-Agent 目录、其原有 Python 环境下执行）。

## 4. 修改一：把"本地回退"并入新 /api/chat（必做）

旧 `web_app.py.bak` 里有一个新层没有的能力：LangGraph 服务未启动时，`/api/chat` 回退到进程内直接调图（`_local_chat_response`，约 150–165 行）。把它移植进新 `web_app.py`：

1. 在新 `web_app.py` 中（路由定义之前的合适位置）加入从备份抄来的函数：

```python
def _local_chat_response(user_message: str, session_id: str):
    """LangGraph 服务未启动时，回退到本地多智能体流程。"""
    try:
        from multi_agent_customer_service import process_customer_query
        result = process_customer_query(user_message, session_id)
        return jsonify({
            'response': result.get('response', ''),
            'session_id': session_id,
            'thread_id': session_id,
            'local_fallback': True
        })
    except Exception as e:
        print(f"❌ 本地客服处理失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'本地客服处理失败: {str(e)}'}), 500
```

2. 修改 `/api/chat` 路由：`run_chat_sync` 返回的这两个错误码意味着 LangGraph 不可达，此时走本地回退而不是报 500：

```python
        ai_text, err_msg, http_code = run_chat_sync(user_message, client_session_id)
        if err_msg in ('无法创建或找到助手', '无法创建线程'):
            return _local_chat_response(user_message, client_session_id)
        if err_msg:
            return jsonify({'error': err_msg}), http_code or 500
```

注意（告知用户即可，不必处理）：本地回退不经过 LangGraph 线程，这类对话不会出现在会话列表里，刷新即丢；它只是"服务挂了还能聊"的降级通道。

## 5. 修改二：修复 Customer-Agent 图的 messages 重复累积 bug（先验证，确认后修）

### 5.1 问题

`multi_agent_customer_service.py` 中：

- `AgentState.messages` 声明为 `Annotated[List[Dict], add]`（约 54 行，reducer 是累加）；
- `final_response_node`（约 449–461 行）却读取**全量** `state["messages"]`，再 append 本轮 user + assistant 后整表返回。

按 LangGraph reducer 语义（input 消息累加 + 节点返回值再累加），历史会滚雪球式重复：第一轮 user 消息就可能重复 3 次，之后每轮近似翻倍。后果：会话列表 `message_count` 虚高、"加载会话历史"出现重复气泡。

### 5.2 验证方法（不要跳过）

按第 7 节启动服务后，用 curl 发一轮对话，然后查线程 state：

```bash
curl -s http://127.0.0.1:2024/threads/<thread_id>/state | python -m json.tool
```

查看 `values.messages`：若同一句用户消息出现多于 1 次，则确认 bug，执行 5.3。

### 5.3 修复

把 `final_response_node` 末尾"添加到消息历史"段（原 449–461 行）替换为**只返回本轮增量**，并防呆处理"input 未带 messages"的情况（本地直调 `process_customer_query` 可能不带）：

```python
    # 只返回本轮增量：input 中的用户消息已由 add reducer 累加进通道，
    # 这里仅在缺失时补一条用户轮，避免整段历史被重复累加
    messages_update = []
    existing = state.get("messages") or []
    if not any(
        m.get("role") == "user" and m.get("content") == state.get("customer_query", "")
        for m in existing
    ):
        messages_update.append({"role": "user", "content": state.get("customer_query", "")})
    messages_update.append({"role": "assistant", "content": final_response})

    new_state = state.copy()
    new_state["response"] = final_response
    new_state["messages"] = messages_update
```

（即删掉原来的 `messages = state.get("messages", [])` + 两次 `messages.append(...)` + `new_state["messages"] = messages` 整段。）

同时检查 `process_customer_query`（约 733 行）构造图 input 的方式：若它没有把用户消息放进 `input["messages"]`，无需改动——上面的防呆分支已覆盖；若放了，也正确（不会重复，因为分支会跳过）。

### 5.4 验收

新开一个线程连发两轮对话后，`GET /threads/{tid}/state` 的 `values.messages` 应**恰好 4 条**：user1, assistant1, user2, assistant2，无任何重复。

## 6. 修改三：前端采纳服务端返回的 thread_id（必做，一行补丁）

新前端"新建会话"时本地生成 `web_<时间戳>` 作为 session_id，它不是合法 LangGraph 线程 ID；而 `sendMessage()` 成功后**没有**把响应里返回的真实 `session_id` 写回 `currentSessionId`，导致会话与线程的绑定依赖服务端全局变量缓存（重启即断、多用户互串）。

修法：在 `templates/index.html` 的 `sendMessage()` 中，`else if (data.response) {` 分支开头（约 1914 行）插入：

```javascript
                    if (data.session_id && data.session_id !== currentSessionId) {
                        currentSessionId = data.session_id;
                    }
```

（该函数末尾已有 `refreshSessionList()`，无需重复调用。）

## 7. 启动与验收清单

启动顺序（都在 Customer-Agent 目录、其原有 Python 环境）：

```bash
# 终端 1：LangGraph 服务端（必须从 Customer-Agent 目录启动，确保跑的是本项目的图）
langgraph dev --no-browser     # 默认 127.0.0.1:2024

# 终端 2：Flask Web 层
python web_app.py              # 0.0.0.0:5000
```

逐项验收（curl 全部通过后，再做浏览器验收）：

| # | 验收项 | 方法 | 通过标准 |
|---|---|---|---|
| 1 | Web 存活 | `curl http://localhost:5000/api/health` | `{"status":"healthy",...}` |
| 2 | LangGraph 连通 | `curl http://localhost:5000/api/test` | `health_check`、`threads_search`、`assistants_search` 三项都是 2xx 数字 |
| 3 | 聊天主链路 | `curl -X POST http://localhost:5000/api/chat -H "Content-Type: application/json" -d '{"message":"帮我查下本月账单","session_id":"default"}'` | 返回 `response` 非空 + `session_id`/`thread_id`（UUID 格式）；终端 1 日志显示 sensitive_word_filter → intent_router → … → final_response 节点执行 |
| 4 | 消息不重复 | 用上一步 `thread_id` 再发第二轮，然后 `curl http://localhost:5000/api/sessions/<thread_id>` | `conversation_history` 恰好 4 条，无重复 |
| 5 | 会话列表预览 | `curl http://localhost:5000/api/sessions` | 该会话 `last_user_question` 等于第二条用户消息原文；`message_count` 为 4 |
| 6 | 删除会话 | `curl -X DELETE http://localhost:5000/api/sessions/<thread_id> -i` | HTTP 200 **或 204** 都算通过且返回成功 JSON（新层已兼容 204；若仍失败检查是否真的换成了新 web_app.py） |
| 7 | 浏览器-发送 | 打开 http://localhost:5000，发两条消息 | 气泡正常；Network 面板确认第二条请求的 `session_id` 已是服务端返回的 UUID（验证第 6 节补丁生效） |
| 8 | 浏览器-侧栏 | 观察左侧会话列表 | 新会话预览显示最后一条用户消息而非"新对话"；消息数正确 |
| 9 | 浏览器-加载历史 | 点击侧栏该会话 | 历史气泡按 user/assistant 正确分侧渲染，无重复 |
| 10 | 浏览器-删除 | 会话项 `...` 菜单 → 删除此对话 → 确认 | 会话从列表消失；若删的是当前会话，自动进入新会话 |
| 11 | 本地回退 | 停掉终端 1 的 langgraph dev，再发一条消息 | 能收到回复且响应含 `"local_fallback": true`；重启 langgraph dev 后恢复正常 |

已知可接受的小瑕疵（不要求修复）：
- 助手回复文本自带 Customer-Agent 图 `final_response_node` 拼的 `【系统提示】` / `⚠️ 注意` 前后缀（敏感词场景），属后端文案设计，不是拼装问题。
- 前端有渲染 `data.agent` / `data.query_type` 徽标的防御性代码，后端不返回这两个字段，不显示，无害。
- `/api/chat/stream` 仍是伪流式（轮询完成后一次性吐全文），前端未调用，不动。

## 8. 回滚方案

任一步失败且无法修复时：

```bash
cd /d/langgraph/Customer-Agent
cp web_app.py.bak web_app.py
cp templates/index.html.bak templates/index.html
cp multi_agent_customer_service.py.bak multi_agent_customer_service.py
rm chat_web_service.py
```

即完全恢复原状。`.bak` 文件在整体验收通过、用户确认后再清理。

## 9. 附：接口契约速查（拼装后生效）

前端（templates/index.html）→ Flask（web_app.py + chat_web_service.py），前端实际调用 5 个：

| 接口 | 方法 | 请求 | 响应要点 |
|---|---|---|---|
| `/api/chat` | POST | `{message, session_id}` | `{response, session_id, thread_id}`；LangGraph 挂时 `{response, local_fallback:true}` |
| `/api/sessions` | GET | — | `{sessions:[{session_id, created_at, message_count, last_user_question}]}` |
| `/api/sessions/<id>` | GET | — | `{session:{session_id, created_at, conversation_history:[{is_user, content, role, timestamp?}]}}` |
| `/api/sessions/<id>` | DELETE | — | 2xx 即成功 |
| `/api/test` | GET | — | `{health_check, threads_search, assistants_search}`（数字状态码） |

Flask → LangGraph（127.0.0.1:2024）：`POST /assistants/search`、`POST /assistants`、`POST /threads`、`GET /threads/{id}`、`POST /threads/{id}/runs`（input=`{messages:[{role,content}], customer_query, session_id}`）、`GET /threads/{tid}/runs/{rid}`（0.5s 轮询，上限 120s）、`GET /threads/{id}/state`、`POST /threads/search`、`DELETE /threads/{id}`。

历史解析优先级（chat_web_service.conversation_history_from_state_data）：`values.persisted_dialogue` → `values.conversation_history` → `values.messages`（Customer-Agent 走第三级，`role` 字段判别 user/assistant）；`values.response` 仅在轮次列表为空时兜底。
