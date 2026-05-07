# Skill系统文档

## 1. 概述

Skill系统是一个模块化的处理框架，用于处理用户请求并协调不同的处理组件。它是多智能体客服系统的核心执行引擎，负责请求的过滤、路由、工具调用和Agent调度。

### 核心功能
- 标准化的Skill接口和执行流程
- 基于优先级的Skill执行顺序
- 灵活的路由机制
- 与MCP工具的无缝集成
- 可扩展的模块化设计

### 与其他模块的关系
- **Agent系统**：Skill系统通过AgentDispatchSkill调用具体的Agent
- **MCP工具**：Skill系统通过ToolDispatchSkill执行各种工具
- **敏感词处理**：通过SensitiveWordFilterSkill进行内容安全过滤
- **意图识别**：通过IntentRoutingSkill识别用户意图并路由

## 2. 架构设计

### 2.1 核心组件

#### Skill基类
- 所有Skill的抽象基类
- 定义了`can_handle`和`execute`方法
- 包含优先级、类型、关键词等属性

#### SkillResult
- 统一的结果格式
- 包含成功状态、数据、错误信息、元数据等
- 支持不同的下一步动作（继续、停止、路由）

#### SkillRegistry
- Skill注册表，管理所有注册的Skill
- 提供基于关键词和意图的Skill查找
- 支持按类型和优先级获取Skill

#### SkillDispatcher
- Skill调度器，协调多个Skill的执行
- 支持按顺序执行Skill流水线
- 处理Skill之间的路由关系

### 2.2 执行流程

```
客户查询 → 敏感词过滤 → 工具调度 → 意图路由 → Agent调度 → 最终响应
    ↓          ↓          ↓          ↓           ↓           ↓
  输入     内容安全     工具执行     意图识别     专业处理     格式化输出
```

### 2.3 数据流向

1. **输入阶段**：用户查询进入系统
2. **过滤阶段**：敏感词过滤Skill检查内容安全性
3. **工具阶段**：工具调度Skill执行相关MCP工具
4. **路由阶段**：意图路由Skill识别意图并确定处理Agent
5. **执行阶段**：Agent调度Skill调用具体Agent处理
6. **输出阶段**：返回最终响应给用户

### 2.4 优先级机制

- **敏感词过滤**：优先级 100（最高）
- **意图路由**：优先级 90
- **工具调度**：优先级 85
- **Agent调度**：优先级 80

## 3. 核心Skill实现

### 3.1 敏感词过滤Skill (SensitiveWordFilterSkill)

**功能**：识别用户输入中的敏感词并分级处理

**处理逻辑**：
- **L3（高危）**：创建工单和外呼任务，终止对话并转入投诉专员
- **L2（中危）**：创建工单和外呼任务，转入普通客服列表
- **L1（低危）**：放行并添加安抚话术，继续后续处理

**输入**：
```python
{
    "query": "用户输入",
    "session_id": "会话ID",
    "customer_info": "客户信息"（可选）
}
```

**输出**：
```python
{
    "success": true/false,
    "data": {
        "has_sensitive": true/false,
        "level": 0/1/2/3,
        "matched_words": ["敏感词1", "敏感词2"],
        "action": "处理动作",
        "response": "回复内容",
        "mood_tag": "情绪标签",
        "proceed": true/false
    },
    "next_action": "continue"/"stop"
}
```

### 3.2 意图路由Skill (IntentRoutingSkill)

**功能**：识别用户意图并路由到相应的Agent

**支持的意图**：
- 机票预订咨询（product_info）
- 机票价格构成（price_composition）
- 目的地天气查询（destination_weather）
- 航班延误预测（delay_prediction）
- 价格波动预测（price_trend）
- 账单问题（billing）
- 投诉建议（complaint）
- 一般咨询（general_inquiry）

**输入**：
```python
{
    "query": "用户输入",
    "session_id": "会话ID",
    "use_llm": true/false（可选）
}
```

**输出**：
```python
{
    "success": true/false,
    "data": {
        "intent": "识别的意图类型",
        "agent": "路由到的Agent",
        "tools": ["可用的工具列表"],
        "description": "意图描述"
    },
    "next_action": "route_to",
    "route_to": "Agent名称"
}
```

### 3.3 工具调度Skill (ToolDispatchSkill)

**功能**：根据意图选择并执行相应的MCP工具

**支持的工具**：
- 航班搜索（flight_search）
- 价格构成分析（price_composition）
- 天气查询（weather_query）
- 延误预测（delay_prediction）
- 价格趋势（price_trend）

**特点**：
- 自动解析用户输入中的参数
- 支持多个工具的并行执行
- 整合工具执行结果

**输入**：
```python
{
    "intent": "意图类型",
    "query": "用户输入",
    "tools": ["工具名称列表"]（可选）,
    "params": "工具参数字典"（可选）
}
```

**输出**：
```python
{
    "success": true/false,
    "data": {
        "tool_results": {"工具名称": "执行结果"},
        "executed_tools": ["执行的工具列表"]
    },
    "next_action": "continue"
}
```

### 3.4 Agent调度Skill (AgentDispatchSkill)

**功能**：调用具体的Agent处理用户请求，整合工具执行结果

**处理流程**：
1. 获取Agent实例
2. 准备Agent输入（包含用户查询、会话ID、情绪标签等）
3. 调用Agent处理
4. 整合结果并返回

**输入**：
```python
{
    "agent": "Agent名称或对象",
    "query": "用户输入",
    "session_id": "会话ID",
    "mood_tag": "情绪标签"（可选）,
    "filter_result": "过滤结果"（可选）,
    "tool_results": "工具执行结果"（可选）
}
```

**输出**：
```python
{
    "success": true/false,
    "data": {
        "response": "Agent响应内容",
        "agent": "处理的Agent名称",
        "result": "Agent完整结果"
    },
    "next_action": "continue"
}
```

## 4. 扩展指南

### 4.1 创建自定义Skill

1. **继承Skill基类**：
   ```python
   from skills.skill_base import Skill, SkillType, SkillResult
   
   class MyCustomSkill(Skill):
       def __init__(self):
           super().__init__(
               name="my_custom_skill",
               description="自定义Skill",
               skill_type=SkillType.TOOL,
               keywords=["关键词1", "关键词2"],
               priority=70
           )
       
       def can_handle(self, context):
           # 实现判断逻辑
           return True
       
       def execute(self, context):
           # 实现执行逻辑
           return SkillResult.success_result(data={"result": "处理结果"})
   ```

2. **注册Skill**：
   ```python
   from skills import skill_registry
   
   my_skill = MyCustomSkill()
   skill_registry.register(my_skill)
   ```

### 4.2 注册和管理Skill

**全局注册**：
- 在`skills/__init__.py`中的`register_all_skills`函数中添加新Skill

**动态注册**：
- 使用`skill_registry.register(skill_instance)`动态注册
- 使用`skill_registry.unregister(skill_name)`注销

**查询Skill**：
- `skill_registry.get_skill(name)` - 获取指定Skill
- `skill_registry.find_skills_by_keyword(keyword)` - 根据关键词查找
- `skill_registry.find_skills_by_intent(query)` - 根据意图查找

### 4.3 最佳实践

1. **单一职责**：每个Skill应该只负责一个特定功能
2. **优先级设置**：根据处理顺序设置合理的优先级
3. **错误处理**：在`execute`方法中捕获并处理异常
4. **结果格式**：使用`SkillResult`的静态方法创建标准结果
5. **参数验证**：在处理前验证输入参数的有效性

## 5. MCP工具集成

### 5.1 工具调度机制

**意图到工具的映射**：
```python
self.intent_tool_mapping = {
    "product_info": ["flight_search"],
    "price_composition": ["price_composition"],
    "destination_weather": ["weather_query"],
    "delay_prediction": ["delay_prediction"],
    "price_trend": ["price_trend"]
}
```

**工具执行**：
```python
for tool_name in tools:
    result = mcp_tool_registry.execute_tool(tool_name, **tool_params.get(tool_name, {}))
    tool_results[tool_name] = result
```

### 5.2 参数解析方法

**航班搜索参数**：
- 提取出发地和目的地
- 解析日期
- 提取乘客数量

**天气查询参数**：
- 提取城市名称

**延误预测参数**：
- 提取航线信息

**价格趋势参数**：
- 提取航线信息

### 5.3 工具执行结果处理

- 工具执行结果会被存储在`tool_results`字典中
- 结果会传递给Agent进行进一步处理
- Agent可以基于工具结果生成更准确的响应

## 6. 示例用法

### 6.1 基本使用示例

```python
from skills import skill_dispatcher

# 处理用户查询
context = {
    "query": "北京到上海的航班",
    "session_id": "12345"
}

result = skill_dispatcher.dispatch(context)
print(result.to_dict())
```

### 6.2 执行Skill流水线

```python
from skills import skill_dispatcher

# 按顺序执行Skill
skill_names = ["sensitive_word_filter", "tool_dispatcher", "intent_router", "agent_dispatcher"]
context = {
    "query": "北京到上海的航班价格构成",
    "session_id": "12345"
}

results = skill_dispatcher.dispatch_pipeline(context, skill_names)
for i, result in enumerate(results):
    print(f"Skill {skill_names[i]}: {result.to_dict()}")
```

### 6.3 高级使用场景

**自定义处理流程**：
```python
from skills import skill_registry

# 获取特定Skill
sensitive_filter = skill_registry.get_skill("sensitive_word_filter")
intent_router = skill_registry.get_skill("intent_router")

# 手动执行Skill
context = {"query": "用户输入", "session_id": "12345"}
filter_result = sensitive_filter.execute(context)

if filter_result.success and filter_result.data.get("proceed", True):
    # 继续处理
    context["filter_result"] = filter_result.data
    route_result = intent_router.execute(context)
    print(route_result.to_dict())
else:
    # 处理敏感词情况
    print("敏感词处理结果:", filter_result.to_dict())
```

## 7. 故障排除

### 7.1 常见问题和解决方案

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Skill未执行 | 优先级设置不当 | 检查Skill优先级，确保按正确顺序执行 |
| 意图识别错误 | 关键词匹配失败 | 调整意图分类规则，添加更多关键词 |
| 工具执行失败 | 参数解析错误 | 检查参数解析逻辑，确保能正确提取参数 |
| Agent调用失败 | Agent未注册 | 确保Agent已在`agents/__init__.py`中注册 |
| 敏感词过滤误判 | 敏感词库不完善 | 更新敏感词知识库，调整匹配规则 |

### 7.2 调试技巧

1. **启用详细日志**：在Skill执行前后添加日志
2. **检查上下文**：确保传递给Skill的上下文包含所有必要信息
3. **验证工具参数**：检查工具执行前的参数是否正确
4. **测试单个Skill**：单独测试每个Skill的执行结果
5. **使用模拟数据**：创建测试用例验证Skill的处理逻辑

## 8. 总结

Skill系统是多智能体客服系统的核心执行引擎，通过标准化的接口和灵活的执行流程，实现了用户请求的高效处理。它的模块化设计和可扩展性使得系统能够轻松适应不同的业务场景和需求变化。

通过Skill系统，我们实现了：
- 统一的请求处理流程
- 基于优先级的执行顺序
- 与MCP工具的无缝集成
- 智能的意图识别和路由
- 灵活的扩展机制

Skill系统为多智能体客服系统提供了强大的执行能力，是系统能够高效处理各种客户请求的关键组件。