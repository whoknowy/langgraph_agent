# 多智能体客服系统

## 项目概述

这是一个基于Skill和MCP架构的多智能体客服系统，专门针对机票订单系统的客服场景。系统支持机票预订咨询、价格构成分析、目的地天气查询、航班延误预测、价格波动预测等功能，采用模块化设计，通过Skill流水线执行处理流程。

## 项目结构

```
customer-service-ai-agent/
├── agents/ # 智能体模块
│ ├── __init__.py # 智能体包初始化
│ ├── base_agent.py # 基础智能体类
│ ├── product_agent.py # 机票专家智能体
│ ├── billing_agent.py # 账单专家智能体
│ ├── complaint_agent.py # 投诉处理专家智能体
│ └── general_agent.py # 综合客服智能体
├── skills/ # Skill模块
│ ├── __init__.py # Skill包初始化
│ ├── skill_base.py # Skill基类
│ ├── sensitive_word_filter.py # 敏感词过滤Skill
│ ├── intent_router.py # 意图路由Skill
│ └── tool_dispatcher.py # 工具调度Skill
├── tools/ # MCP工具模块
│ ├── __init__.py # 工具包初始化
│ ├── mcp_tools.py # MCP工具实现
│ └── query_tools.py # 查询分类工具
├── memory/ # 记忆管理包
│ ├── __init__.py # 记忆包初始化
│ ├── session_manager.py # 会话管理器（LangChain标准接口）
│ ├── enhanced_memory.py # 增强记忆管理（BM25+向量检索）
│ └── session_monitor.py # 会话监控服务（10分钟无活动自动总结）
├── templates/ # Web界面模板
│ └── index.html # 主页面HTML模板
├── config.py # 基础配置文件
├── multi_agent_customer_service.py # 主程序文件（Skill流水线）
├── web_app.py # Web应用（Flask + LangGraph API）
├── langgraph.json # LangGraph工作流配置
├── requirements.txt # 项目依赖
├── .env # 环境变量配置
├── README.md # 项目说明文档
└── README_LangGraph_CLI.md # LangGraph CLI使用指南
```

## 主要特性

### 1. 分层架构设计

- **表现层**: 命令行界面，用户交互和结果展示
- **业务逻辑层**: Skill系统，负责业务流程协调和意图识别
- **功能执行层**: MCP工具，负责具体功能实现
- **智能体层**: 专业Agent，处理领域知识和生成个性化回复
- **记忆层**: 记忆管理，负责会话管理和短期/长期记忆

### 2. Skill系统

- **敏感词过滤Skill**: 毫秒级敏感词检测，支持三级敏感词分级处理
- **意图路由Skill**: 两阶段意图识别（规则匹配+Embedding匹配）
- **Agent调度Skill**: 根据意图调用相应专业Agent
- **工具调度**: 内嵌工具调度逻辑，自动调用MCP工具

### 3. MCP工具系统

- **航班搜索**: 查询航班信息和价格
- **价格构成分析**: 分析机票价格组成明细
- **目的地天气查询**: 获取目的地天气信息
- **航班延误预测**: 预测航班延误概率
- **价格波动预测**: 分析价格趋势和最佳预订时机

### 4. 智能体系统

- **机票专家Agent**: 处理机票相关查询和推荐
- **投诉处理Agent**: 处理客户投诉和建议，自动存储投诉记录
- **账单专家Agent**: 处理账单相关问题
- **综合客服Agent**: 处理一般咨询

### 5. 增强记忆系统

- **短期记忆**: 滑动窗口(5条消息) + 自动摘要
- **长期记忆**: BM25+向量检索融合，支持语义相似度搜索
- **自动存储机制**:
  - 投诉处理后自动存储到长期记忆
  - 会话结束(10分钟无活动)自动总结存储
- **会话监控**: 后台线程定期检查，确保记忆及时存储

### 6. 智能意图识别

- **两阶段识别**: 规则匹配(快) + Embedding匹配(准)
- **支持8种核心意图**: 机票预订、价格构成、天气查询、延误预测、价格趋势、账单问题、投诉建议、一般咨询
- **置信度计算**: 确保识别准确性

### 7. 系统监控

- **会话监控**: 10分钟无活动自动总结
- **详细日志**: 完整的处理流程日志
- **容错机制**: API调用重试、错误处理

## 安装和配置

### 1. 安装依赖

```bash
pip install -r requirements.txt
pip install langgraph-cli
pip install -U "langgraph-cli[inmem]"
```

在win环境中，langgraph-cli下载后，需要将`langgraph.exe`路径加入PATH环境变量。或者使用时直接带全路径，例`C:\Users\<yourName>\AppData\Roaming\Python\Python313\Scripts\langgraph.exe`

### 2. 环境变量配置

复制 `env_example.txt` 为 `.env` 文件并配置。

### 3. 运行系统

```bash
python multi_agent_customer_service.py
```

## 🚀 运行说明

### 方式1：使用Studio UI访问langgraph服务

```
langgraph dev
```

启动后，会自动拉起LangSmith服务，包含LangStudio UI，默认2024端口。使用浏览器访问`https://smith.langchain.com/studio/thread?render=interact&baseUrl=http://127.0.0.1:2024`

### 方式2：使用Web服务调用langgraph api

```
## 终端1：启动LangGraph服务
langgraph dev

## 终端2：启动自定义Web服务
python ./web_app.py
```

使用浏览器访问`http://localhost:5000`，界面功能：

- 实时聊天: 输入问题，获得智能回复
- 智能体信息: 显示当前处理问题的专家和查询类型
- 历史管理: 查看、清除对话历史
- 数据导出: 导出对话记录用于分析

### 方式3：直接API调用

接口文档访问地址默认`http://127.0.0.1:2024/docs`，内嵌了js需要挂梯子。也可参考`https://langchain-ai.github.io/langgraph/cloud/reference/api/api_ref.html`

需要`langgraph dev`启动LangGraph服务。

## 工作流程

### 处理流程图

```
用户输入 → 敏感词过滤 → 意图识别 → 工具调度 → Agent处理 → 响应生成
    ↓           ↓           ↓           ↓           ↓           ↓
  安全检查    意图分类     执行工具     专业处理     生成回复
                                ↓
                            记忆存储
```

### 详细处理流程

1. **输入处理**: 用户输入问题
2. **敏感词过滤**:
   - 识别敏感词并分级(L1/L2/L3)
   - L1: 放行+安抚话术
   - L2/L3: 创建工单+外呼任务+继续处理
3. **意图识别**:
   - 规则匹配: 关键词匹配（快速）
   - Embedding匹配: 语义相似度匹配（准确）
   - 确定意图ID和对应Agent
4. **工具调度**:
   - 根据意图调用相应MCP工具
   - 解析参数并执行工具
   - 获取工具执行结果
5. **Agent处理**:
   - 调用专业Agent处理
   - 整合工具执行结果
   - 生成个性化回复
6. **响应生成**:
   - 整合敏感词安抚话术和Agent回复
   - 添加情绪标签提示
   - 返回最终响应
7. **记忆存储**:
   - 投诉处理后自动存储到长期记忆
   - 会话结束(10分钟无活动)自动总结存储

### 状态管理

系统使用 `AgentState` 来管理整个工作流的状态：

- `customer_query`: 客户查询内容
- `session_id`: 会话唯一标识
- `current_agent`: 当前处理智能体
- `response`: 智能体回复
- `tools_used`: 使用的工具列表
- `intent`: 识别的意图类型
- `mood_tag`: 客户情绪标签
- `filter_action`: 敏感词过滤动作
- `ticket_id`: 工单编号（如果创建）
- `task_id`: 外呼任务编号（如果创建）
- `tool_results`: 工具执行结果

<br />

## 技术架构

### 核心技术栈

- **LangChain**: 会话管理、Memory接口
- **OpenAI兼容API**: 大语言模型调用（硅基流动）
- **Python 3.8+**: 主要开发语言
- **模块化设计**: 高内聚、低耦合的架构

### 技术实现

#### 1. 分层架构

- **表现层**: 命令行界面，提供用户交互
- **业务逻辑层**: Skill系统，处理业务流程
- **功能执行层**: MCP工具，执行具体功能
- **智能体层**: 专业Agent，处理领域知识
- **记忆层**: 记忆管理，管理会话和记忆

#### 2. 核心组件

- **SkillPipelineExecutor**: 管理Skill执行顺序和流程
- **MCPToolRegistry**: 管理和执行MCP工具
- **EnhancedMemoryManager**: 管理短期和长期记忆
- **SessionMonitor**: 监控会话状态，自动总结

#### 3. 记忆系统

- **短期记忆**: 滑动窗口(5条消息) + 自动摘要
- **长期记忆**: BM25+向量检索融合
- **自动存储**: 投诉处理后和会话结束时自动存储

#### 4. 意图识别

- **两阶段识别**: 规则匹配 + Embedding匹配
- **意图类型**: 8种核心意图
- **置信度计算**: 确保识别准确性

#### 5. 系统监控

- **会话监控**: 10分钟无活动自动总结
- **日志记录**: 详细的处理流程日志
- **容错机制**: API调用重试、错误处理

## 系统优势

1. **智能化**: 两阶段意图识别，理解用户需求
2. **个性化**: 基于记忆系统提供个性化服务
3. **高效性**: 工具自动调度，快速响应
4. **可靠性**: 多级错误处理，系统稳定运行
5. **可扩展性**: 模块化设计，易于扩展功能
6. **安全性**: 敏感词过滤，保障内容安全
7. **记忆增强**: BM25+向量检索融合，智能存储和检索

## 应用场景

- **机票预订咨询**: 航班查询、价格比较
- **价格分析**: 价格构成、价格趋势
- **出行规划**: 目的地天气、航班延误预测
- **问题处理**: 投诉建议、账单问题
- **个性化服务**: 基于历史记录提供定制化建议

## 运行说明

### 启动系统

```bash
python multi_agent_customer_service.py
```

### 系统命令

- `exit`: 退出系统
- `status`: 查看工作台状态
- `tickets`: 查看工单状态
- `tools`: 查看可用工具

### 示例对话

**用户**: 北京到上海的机票多少钱？
**系统**: 【机票专家】北京到上海的航班价格在650-720元之间，具体取决于日期和航空公司。

**用户**: 明天的呢？
**系统**: 【机票专家】明天北京到上海的航班价格约为680元左右，建议提前预订以获得更好的价格。

**用户**: 上海天气怎么样？
**系统**: 【机票专家】上海当前天气晴朗，温度在18-25度之间，适合出行。

## 相关文档

- [README\_LangGraph\_CLI.md](README_LangGraph_CLI.md) - LangGraph CLI使用指南
- [langgraph.json](langgraph.json) - 工作流配置文件
- [LangGraph API服务搭建](https://docs.langchain.com/langgraph-platform/cli#configuration-file)
- [LangGraph MCP适配器](https://github.com/langchain-ai/langchain-mcp-adapters)

