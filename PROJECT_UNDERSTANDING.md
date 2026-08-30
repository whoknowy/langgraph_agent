# 多智能体客服系统  项目理解

## 1. 项目定位

这是一个基于 **Skill + MCP 工具 + 多 Agent** 架构的机票客服系统，主要面向以下场景：

- 机票预订咨询
- 价格构成分析
- 目的地天气查询
- 航班延误预测
- 价格波动预测
- 账单问题处理
- 投诉建议处理
- 一般客服咨询
- 多意图并行处理

当前实现已从旧版 Skill Pipeline 重构为 **LangGraph StateGraph**，同时保留旧 Pipeline 作为兼容回退。

---

## 2. 技术栈

- Python 3.12
- LangGraph / LangChain 1.x
- Flask（Web 界面）
- DeepSeek API（OpenAI 兼容接口）
- requests（直接调用 LLM API）
- 本地模拟数据 + 增强记忆
- MCP 风格工具注册表

---

## 3. 目录结构

```text
Customer-Agent/
 agents/
    base_agent.py          # 所有 Agent 的基类
    product_agent.py       # 机票专家
    billing_agent.py       # 账单专家
    complaint_agent.py     # 投诉处理专家
    general_agent.py       # 综合客服
    tech_agent.py          # 技术支持专家
    sensitive_words.py     # 敏感词知识库
    ac_automaton.py        # AC 自动机
    ticket_system.py       # 工单系统
    workstation.py         # 工作台/客服状态
    filter_pipeline.py     # 敏感词过滤管道
 skills/
    sensitive_word_filter.py
    intent_router.py
    embedding_intent_classifier.py
    tool_dispatcher.py
    skill_base.py
 tools/
    mcp_tools.py
    query_tools.py
 memory/
    session_manager.py
    enhanced_memory.py
    session_monitor.py
 templates/
    index.html
 config.py
 multi_agent_customer_service.py
 web_app.py
 langgraph.json
 requirements.txt
 .env
```

---

## 4. 核心工作流

### LangGraph 图结构

```text
START
  
sensitive_word_filter
  
intent_router
  
条件路由
   multi_intent_handler
   product_agent
   billing_agent
   complaint_agent
   general_agent
  
final_response
  
END
```

### 单次查询处理流程

1. 敏感词过滤
   - 识别敏感词等级 L1/L2/L3
   - 生成情绪标签、安抚话术、工单/任务
2. 意图识别
   - 规则匹配 + Embedding 语义匹配
   - 支持多意图识别
3. 工具调度
   - 根据意图调用 MCP 工具：
     - `flight_search`
     - `price_composition`
     - `weather_query`
     - `delay_prediction`
     - `price_trend`
4. Agent 处理
   - 调用对应专业 Agent
   - Agent 将上下文发送给 DeepSeek 生成回答
5. 最终响应
   - 整合敏感词提示、情绪标签、Agent 回复
6. 记忆存储
   - 短期记忆：滑动窗口
   - 长期记忆：BM25 + 向量混合检索

---

## 5. 核心状态字段

`AgentState` 包含：

```python
customer_query
session_id
messages
has_sensitive
sensitivity_level
matched_words
mood_tag
ticket_id
task_id
filter_response
filter_action
intent
intents
is_multi_intent
is_rule_matched
confidence
target_agent
tools_needed
tool_results
tools_used
agent_response
current_agent
response
enhanced_context
```

---

## 6. LLM 接入现状

### 改造前

`BaseAgent.llm = None`，没有实际注入 LLM，Agent 调用时会报：

```text
'NoneType' object has no attribute 'invoke'
```

### 改造后

在 `agents/__init__.py` 中新增：

```python
class OpenAICompatibleLLM:
    ...
```

并实现 `initialize_agents()`：

```python
def initialize_agents():
    llm = OpenAICompatibleLLM()
    agents = {
        "product_agent": ProductAgent(),
        "billing_agent": BillingAgent(),
        "complaint_agent": ComplaintAgent(),
        "general_agent": GeneralAgent(),
        "tech_agent": TechAgent(),
    }
    for agent in agents.values():
        agent.set_llm(llm)
    return agents
```

该 LLM 客户端通过 `requests` 直接调用 OpenAI 兼容接口：

```text
POST {OPENAI_BASE_URL}/chat/completions
```

当前配置使用 DeepSeek：

```env
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

---

## 7. 当前环境兼容性改动

为了使项目能在本机现有 LangChain 1.x 环境运行，做了以下兼容修改：

### 7.1 `memory/session_manager.py`

- 使用 `langchain_core.messages` 替代旧 `langchain.messages`
- 使用 `langchain_classic.memory.ConversationBufferMemory`
- 使用 `langchain_community.chat_message_histories`
- MongoDB 后端改为可选导入，避免默认内存模式被阻塞

### 7.2 `agents/__init__.py`

- 新增 `TechAgent` 导出
- 新增 `OpenAICompatibleLLM`
- 新增 `initialize_agents()`

### 7.3 `memory/enhanced_memory.py`

修复了 MD5 生成向量时，维度大于哈希长度导致的空字符串转换错误。

### 7.4 `agents/billing_agent.py`

修复了匹配账单分类时的 `IndexError`。

### 7.5 `multi_agent_customer_service.py`

- `AgentState` 增加 `tools_used`
- 初始状态和 Agent 输入加入 `tools_used`
- 投诉长期记忆改用 `memory.enhanced_memory.add_long_term_memory`

### 7.6 `skills/intent_router.py`

Agent 调度输入增加 `tools_used`。

### 7.7 `web_app.py`

- `/api/chat` 增加本地回退
  - LangGraph API 未启动时自动调用本地 `process_customer_query`
  - 保留 LangGraph 模式优先
- `/api/test` 对 LangGraph 离线状态做友好返回

---

## 8. 启动方式

### 推荐：虚拟环境

```powershell
cd D:\langgraph\Customer-Agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -U "langgraph-cli[inmem]"
```

### 配置环境变量

复制 `.env` 并配置 DeepSeek：

```env
OPENAI_API_KEY=你的DeepSeek API Key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

### 启动命令行版

```powershell
python multi_agent_customer_service.py
```

### 启动 Web 版

```powershell
python web_app.py
```

访问：

```text
http://localhost:5000
```

### 可选：启动 LangGraph 服务

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

langgraph dev --no-browser
```

访问：

```text
http://127.0.0.1:2024
```

---

## 9. 测试

```powershell
python test_system.py
python test_langgraph.py
```

---

## 10. 当前状态总结

-  命令行客服系统可正常运行
-  DeepSeek API 已接通，能返回真实 AI 回答
-  LangGraph 工作流已接通
-  Web 应用可独立运行
-  LangGraph API 未启动时 Web 自动回退本地客服流程
-  测试脚本可正常跑完