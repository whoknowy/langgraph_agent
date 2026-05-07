"""
多智能体客服系统
基于 LangGraph StateGraph 的重构版本
保留所有现有功能，使用真正的图结构
"""

import os
import json
import requests
import time
import uuid
from typing import Dict, List, Any, Optional, TypedDict, Annotated
from dotenv import load_dotenv
from operator import add

# 加载环境变量
load_dotenv()

# 导入配置
from config import *

# 导入模块
from skills import register_all_skills, skill_registry, skill_dispatcher
from tools import mcp_tool_registry
from agents import initialize_agents
from memory import (
    LangChainSessionManager, 
    default_session_manager, 
    get_enhanced_context, 
    start_session_monitor
)

# 尝试导入 LangGraph
try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    print("警告: LangGraph 未安装，将使用模拟实现")


# ========== 状态定义 ==========

class AgentState(TypedDict):
    """工作流状态定义"""
    customer_query: str
    session_id: str
    messages: Annotated[List[Dict], add]
    
    # 敏感词过滤结果
    has_sensitive: Optional[bool]
    sensitivity_level: Optional[int]
    matched_words: Optional[List[str]]
    mood_tag: Optional[str]
    ticket_id: Optional[str]
    task_id: Optional[str]
    filter_response: Optional[str]
    filter_action: Optional[str]
    
    # 意图识别结果
    intent: Optional[str]
    is_rule_matched: Optional[bool]
    confidence: Optional[float]
    target_agent: Optional[str]
    tools_needed: Optional[List[str]]
    
    # 工具执行结果
    tool_results: Optional[Dict[str, Any]]
    
    # Agent 处理结果
    agent_response: Optional[str]
    current_agent: Optional[str]
    
    # 最终响应
    response: Optional[str]
    
    # 增强记忆
    enhanced_context: Optional[str]


# ========== 节点定义 ==========

def sensitive_word_filter_node(state: AgentState) -> AgentState:
    """敏感词过滤节点"""
    print(f"[节点] 执行敏感词过滤")
    
    query = state.get("customer_query", "")
    session_id = state.get("session_id", str(uuid.uuid4()))
    
    # 使用现有的 SensitiveWordFilterSkill
    from skills.sensitive_word_filter import sensitive_word_filter_skill
    
    context = {
        "query": query,
        "session_id": session_id
    }
    
    result = sensitive_word_filter_skill.execute(context)
    
    new_state = state.copy()
    new_state["session_id"] = session_id
    
    if result.success and result.data:
        data = result.data
        new_state["has_sensitive"] = data.get("has_sensitive", False)
        new_state["sensitivity_level"] = data.get("level", 0)
        new_state["matched_words"] = data.get("matched_words", [])
        new_state["mood_tag"] = data.get("mood_tag", "")
        new_state["ticket_id"] = data.get("ticket_id")
        new_state["task_id"] = data.get("task_id")
        new_state["filter_response"] = data.get("response", "")
        new_state["filter_action"] = data.get("action", "")
        print(f"  - 敏感词等级: {data.get('level', 0)}, 情绪标签: {data.get('mood_tag', '')}")
    else:
        new_state["has_sensitive"] = False
        new_state["sensitivity_level"] = 0
    
    return new_state


def intent_router_node(state: AgentState) -> AgentState:
    """意图路由节点 - 包含工具调用"""
    print(f"[节点] 执行意图识别和路由")
    
    query = state.get("customer_query", "")
    session_id = state.get("session_id", "")
    
    # 使用现有的 IntentRoutingSkill
    from skills.intent_router import intent_routing_skill
    
    context = {
        "query": query,
        "session_id": session_id
    }
    
    result = intent_routing_skill.execute(context)
    
    new_state = state.copy()
    
    if result.success and result.data:
        data = result.data
        new_state["intent"] = data.get("intent", "general_inquiry")
        new_state["is_rule_matched"] = data.get("is_rule_matched", False)
        new_state["confidence"] = data.get("confidence", 0.0)
        new_state["target_agent"] = data.get("agent", "general_agent")
        new_state["tools_needed"] = data.get("tools", [])
        new_state["tool_results"] = data.get("tool_results", {})
        print(f"  - 识别意图: {data.get('intent')} -> Agent: {data.get('agent')}")
        if data.get("tool_results"):
            print(f"  - 工具执行完成: {list(data.get('tool_results').keys())}")
    
    return new_state


def get_next_agent_node(state: AgentState) -> str:
    """
    条件路由函数 - 根据意图确定下一个节点
    返回下一个节点的名称
    """
    intent = state.get("intent", "general_inquiry")
    
    intent_node_mapping = {
        "product_info": "product_agent_node",
        "price_composition": "product_agent_node",
        "destination_weather": "product_agent_node",
        "delay_prediction": "product_agent_node",
        "price_trend": "product_agent_node",
        "billing": "billing_agent_node",
        "complaint": "complaint_agent_node",
        "general_inquiry": "general_agent_node"
    }
    
    next_node = intent_node_mapping.get(intent, "general_agent_node")
    print(f"[条件路由] 意图 '{intent}' -> 下一个节点: {next_node}")
    return next_node


def product_agent_node(state: AgentState) -> AgentState:
    """机票专家 Agent 节点"""
    print(f"[节点] 执行机票专家 Agent")
    return _execute_agent_node(state, "product_agent")


def billing_agent_node(state: AgentState) -> AgentState:
    """账单专家 Agent 节点"""
    print(f"[节点] 执行账单专家 Agent")
    return _execute_agent_node(state, "billing_agent")


def complaint_agent_node(state: AgentState) -> AgentState:
    """投诉处理 Agent 节点"""
    print(f"[节点] 执行投诉处理 Agent")
    new_state = _execute_agent_node(state, "complaint_agent")
    
    # 投诉处理完成后，存储到长期记忆
    session_id = new_state.get("session_id")
    if session_id:
        try:
            from memory import default_session_manager
            from memory.enhanced_memory import long_term_memory
            query = new_state.get("customer_query", "")
            response = new_state.get("agent_response", "")
            long_term_memory.add_memory(
                session_id=session_id,
                memory_type="complaint",
                content=f"用户: {query}\n回复: {response}",
                metadata={"intent": "complaint"}
            )
            print(f"  - 投诉记录已保存到长期记忆")
        except Exception as e:
            print(f"  - 保存长期记忆失败: {e}")
    
    return new_state


def general_agent_node(state: AgentState) -> AgentState:
    """综合客服 Agent 节点"""
    print(f"[节点] 执行综合客服 Agent")
    return _execute_agent_node(state, "general_agent")


def _execute_agent_node(state: AgentState, agent_name: str) -> AgentState:
    """执行 Agent 的通用函数"""
    query = state.get("customer_query", "")
    session_id = state.get("session_id", "")
    mood_tag = state.get("mood_tag", "")
    filter_response = state.get("filter_response", "")
    filter_action = state.get("filter_action", "")
    tool_results = state.get("tool_results", {})
    
    # 获取增强上下文
    enhanced_context = ""
    if session_id:
        try:
            enhanced_context = get_enhanced_context(session_id, query)
        except Exception as e:
            print(f"  - 获取增强上下文失败: {e}")
    
    # 准备 Agent 输入
    agent_input = {
        "customer_query": query,
        "session_id": session_id,
        "mood_tag": mood_tag,
        "filter_action": filter_action,
        "filter_response": filter_response,
        "enhanced_context": enhanced_context,
        "tool_results": tool_results
    }
    
    # 调用 Agent
    agents = initialize_agents()
    target_agent = agents.get(agent_name)
    
    if not target_agent:
        new_state = state.copy()
        new_state["agent_response"] = "抱歉，暂无法处理您的请求。"
        new_state["current_agent"] = "system"
        return new_state
    
    result = target_agent.process(agent_input)
    
    # 构建响应
    response = result.get("response", "")
    current_agent = result.get("current_agent", agent_name)
    
    # 如果有敏感词安抚话术，先添加
    if mood_tag == "dissatisfied" and filter_response:
        response = f"{filter_response}\n\n{response}"
    
    new_state = state.copy()
    new_state["agent_response"] = response
    new_state["current_agent"] = current_agent
    
    print(f"  - Agent 执行完成: {current_agent}")
    return new_state


def final_response_node(state: AgentState) -> AgentState:
    """最终响应节点 - 整合所有结果"""
    print(f"[节点] 生成最终响应")
    
    agent_response = state.get("agent_response", "")
    filter_response = state.get("filter_response", "")
    mood_tag = state.get("mood_tag", "")
    
    # 构建完整响应
    full_response_parts = []
    
    if filter_response and mood_tag in ["medium_risk", "high_risk"]:
        full_response_parts.append(f"【系统提示】{filter_response}")
    
    if agent_response:
        full_response_parts.append(f"{agent_response}")
    
    if not full_response_parts:
        full_response_parts.append("感谢您的咨询，如有其他问题请随时提问。")
    
    final_response = "\n\n".join(full_response_parts)
    
    # 添加情绪标签提示（内部使用）
    if mood_tag in ["medium_risk", "high_risk"]:
        final_response += f"\n\n⚠️ 注意：此会话已被标记为{mood_tag}"
    
    # 添加到消息历史
    messages = state.get("messages", [])
    messages.append({
        "role": "user",
        "content": state.get("customer_query", "")
    })
    messages.append({
        "role": "assistant",
        "content": final_response
    })
    
    new_state = state.copy()
    new_state["response"] = final_response
    new_state["messages"] = messages
    
    print(f"  - 最终响应生成完成")
    return new_state


# ========== 图构建 ==========

def build_workflow():
    """
    构建 LangGraph 工作流图
    
    图结构:
    START 
      ↓
    sensitive_word_filter 
      ↓
    intent_router
      ↓
    [条件路由] → product_agent
             ↘ billing_agent
             ↘ complaint_agent
             ↘ general_agent
               ↓
            final_response
               ↓
              END
    """
    if not HAS_LANGGRAPH:
        return None
    
    # 创建图
    graph = StateGraph(AgentState)
    
    # 添加节点
    graph.add_node("sensitive_word_filter", sensitive_word_filter_node)
    graph.add_node("intent_router", intent_router_node)
    graph.add_node("product_agent", product_agent_node)
    graph.add_node("billing_agent", billing_agent_node)
    graph.add_node("complaint_agent", complaint_agent_node)
    graph.add_node("general_agent", general_agent_node)
    graph.add_node("final_response", final_response_node)
    
    # 设置入口点
    graph.set_entry_point("sensitive_word_filter")
    
    # 添加边
    graph.add_edge("sensitive_word_filter", "intent_router")
    
    # 添加条件边 - 根据意图路由到不同的 Agent
    graph.add_conditional_edges(
        "intent_router",
        get_next_agent_node,
        {
            "product_agent_node": "product_agent",
            "billing_agent_node": "billing_agent",
            "complaint_agent_node": "complaint_agent",
            "general_agent_node": "general_agent"
        }
    )
    
    # 所有 Agent 节点连接到最终响应节点
    graph.add_edge("product_agent", "final_response")
    graph.add_edge("billing_agent", "final_response")
    graph.add_edge("complaint_agent", "final_response")
    graph.add_edge("general_agent", "final_response")
    
    # 最终响应连接到结束
    graph.add_edge("final_response", END)
    
    # 编译图
    return graph.compile()


# ========== 向后兼容的执行器 ==========

class SkillPipelineExecutor:
    """
    向后兼容的执行器
    同时支持旧的 Skill Pipeline 和新的 LangGraph
    """

    def __init__(self):
        register_all_skills()
        self.workflow = build_workflow() if HAS_LANGGRAPH else None
        self.use_langgraph = self.workflow is not None
        
        if self.use_langgraph:
            print("✅ 使用 LangGraph 工作流")
        else:
            print("⚠️ 使用 Skill Pipeline (LangGraph 未安装)")

    def execute(self, query: str, session_id: str = None, customer_info: Dict = None) -> Dict[str, Any]:
        """
        执行处理流程
        
        Args:
            query: 用户输入
            session_id: 会话ID
            customer_info: 客户信息
            
        Returns:
            处理结果
        """
        if session_id is None:
            session_id = str(uuid.uuid4())
        
        if self.use_langgraph:
            return self._execute_with_langgraph(query, session_id, customer_info)
        else:
            return self._execute_with_pipeline(query, session_id, customer_info)
    
    def _execute_with_langgraph(self, query: str, session_id: str, customer_info: Dict) -> Dict[str, Any]:
        """使用 LangGraph 执行"""
        initial_state: AgentState = {
            "customer_query": query,
            "session_id": session_id,
            "messages": []
        }
        
        try:
            # 执行工作流
            result = self.workflow.invoke(initial_state)
            
            # 构建响应
            return {
                "success": True,
                "session_id": session_id,
                "response": result.get("response", ""),
                "current_agent": result.get("current_agent", ""),
                "intent": result.get("intent", ""),
                "mood_tag": result.get("mood_tag", ""),
                "filter_action": result.get("filter_action", ""),
                "tool_results": result.get("tool_results", {}),
                "ticket_id": result.get("ticket_id"),
                "task_id": result.get("task_id"),
                "terminated": False,
                "messages": result.get("messages", [])
            }
        except Exception as e:
            print(f"❌ LangGraph 执行失败: {e}")
            import traceback
            traceback.print_exc()
            # 回退到旧的 pipeline
            return self._execute_with_pipeline(query, session_id, customer_info)
    
    def _execute_with_pipeline(self, query: str, session_id: str, customer_info: Dict) -> Dict[str, Any]:
        """使用旧的 Skill Pipeline 执行（向后兼容）"""
        context = {
            "query": query,
            "session_id": session_id,
            "customer_info": customer_info or {},
            "pipeline_results": {}
        }
        
        print(f"\n🚀 (Pipeline) 处理查询: {query}")
        print("=" * 60)
        
        pipeline = [
            {"skill": "sensitive_word_filter", "name": "敏感词过滤"},
            {"skill": "intent_router", "name": "意图路由(含工具调度)"},
            {"skill": "agent_dispatcher", "name": "Agent调度"}
        ]
        
        state_data = {
            "session_id": session_id,
            "customer_query": query
        }
        
        for step in pipeline:
            skill_name = step["skill"]
            skill = skill_registry.get_skill(skill_name)
            
            if not skill:
                print(f"❌ Skill未找到: {skill_name}")
                continue
            
            print(f"\n📍 执行Skill: {step['name']} ({skill_name})")
            
            result = skill.execute(context)
            
            if result.success:
                print(f"✅ Skill执行成功")
                if hasattr(result, 'data') and result.data:
                    context[f"{skill_name}_result"] = result.data
                    context["pipeline_results"][skill_name] = result.data
                    context.update(result.data if isinstance(result.data, dict) else {})
                
                if skill_name == "sensitive_word_filter" and result.data:
                    filter_data = result.data
                    state_data["mood_tag"] = filter_data.get("mood_tag", "")
                    state_data["filter_action"] = filter_data.get("action", "")
                    if filter_data.get("ticket_id"):
                        state_data["ticket_id"] = filter_data.get("ticket_id")
                    if filter_data.get("task_id"):
                        state_data["task_id"] = filter_data.get("task_id")
                    print(f"   敏感词等级: {filter_data.get('level', 0)}")
                
                if skill_name == "intent_router" and result.data:
                    intent_data = result.data
                    state_data["intent"] = intent_data.get("intent", "")
                    state_data["agent_name"] = intent_data.get("agent", "")
                    state_data["tool_results"] = intent_data.get("tool_results", {})
                    print(f"   意图: {state_data['intent']} -> Agent: {state_data['agent_name']}")
                
                if skill_name == "agent_dispatcher" and result.data:
                    state_data["response"] = result.data.get("response", "")
                    state_data["current_agent"] = result.data.get("agent", state_data.get("agent_name"))
            else:
                print(f"❌ Skill执行失败: {result.error}")
                state_data["response"] = f"处理出错: {result.error}"
                state_data["current_agent"] = "system"
        
        print("=" * 60)
        
        return self._build_final_response_from_state(state_data, context)
    
    def _build_final_response_from_state(self, state: Dict, context: Dict) -> Dict[str, Any]:
        """从状态构建最终响应"""
        filter_response = ""
        if state.get("filter_action") and state.get("filter_action") != "放行":
            filter_result = context.get("pipeline_results", {}).get("sensitive_word_filter", {})
            filter_response = filter_result.get("response", "")
        
        agent_response = state.get("response", "")
        
        full_response_parts = []
        if filter_response:
            full_response_parts.append(f"【系统提示】{filter_response}")
        if agent_response:
            full_response_parts.append(f"{agent_response}")
        
        if not full_response_parts:
            full_response_parts.append("感谢您的咨询，如有其他问题请随时提问。")
        
        final_message = "\n\n".join(full_response_parts)
        
        mood_tag = state.get("mood_tag", "")
        if mood_tag in ["medium_risk", "high_risk"]:
            final_message += f"\n\n⚠️ 注意：此会话已被标记为{mood_tag}"
        
        return {
            "success": True,
            "session_id": state.get("session_id"),
            "response": final_message,
            "current_agent": state.get("current_agent"),
            "intent": state.get("intent"),
            "mood_tag": mood_tag,
            "filter_action": state.get("filter_action"),
            "tool_results": state.get("tool_results", {}),
            "ticket_id": state.get("ticket_id"),
            "task_id": state.get("task_id"),
            "terminated": False
        }


# ========== 全局初始化 ==========

# 启动会话监控器
start_session_monitor()

# 创建全局执行器
pipeline_executor = SkillPipelineExecutor()


def process_customer_query(customer_query: str, session_id: str = None) -> Dict[str, Any]:
    """
    处理客户查询的入口函数
    
    Args:
        customer_query: 用户输入
        session_id: 会话ID(可选)
        
    Returns:
        处理结果字典
    """
    return pipeline_executor.execute(customer_query, session_id)


def get_workstation_status():
    """获取工作台状态"""
    from agents import workstation
    return workstation.get_workstation_summary()


def get_ticket_status():
    """获取工单状态"""
    from agents import ticket_system
    return ticket_system.get_ticket_summary()


def get_available_tools():
    """获取可用的MCP工具列表"""
    return mcp_tool_registry.get_tools_schema()


def make_graph():
    """创建工作流图 - 供 LangGraph 服务使用"""
    return build_workflow()


# ========== 测试函数 ==========

if __name__ == "__main__":
    print("🚀 多智能体客服系统 V3.0 (LangGraph) 启动成功！")
    print("=" * 60)
    print("架构: LangGraph StateGraph")
    print("=" * 60)
    print("\n系统特性:")
    print("  - 节点: sensitive_word_filter, intent_router, product_agent, billing_agent, complaint_agent, general_agent, final_response")
    print("  - 边: 条件路由 + 直接边")
    print("  - 支持: 敏感词过滤, 两阶段意图识别, MCP工具, 增强记忆")
    print("\n支持的功能:")
    print("  - 机票预订咨询")
    print("  - 价格构成分析")
    print("  - 目的地天气查询")
    print("  - 航班延误预测")
    print("  - 价格波动预测")
    print("  - 账单问题处理")
    print("  - 投诉建议处理")
    print("=" * 60)
    print("\n请输入您的问题，或输入 'exit' 退出系统")
    print("输入 'status' 查看工作台状态")
    print("输入 'tickets' 查看工单状态")
    print("输入 'tools' 查看可用工具")
    print("-" * 60)
    
    while True:
        user_input = input("\n您的问题: ")
        if user_input.lower() == 'exit':
            break
        elif user_input.lower() == 'status':
            status = get_workstation_status()
            print("\n=== 工作台状态 ===")
            print(f"客服总数: {status['total_agents']}")
            print(f"在线客服: {status['online_agents']}")
            print(f"忙碌客服: {status['busy_agents']}")
            print(f"待处理L2任务: {status['pending_l2_tasks']}")
            print(f"待处理L3任务: {status['pending_l3_tasks']}")
            continue
        elif user_input.lower() == 'tickets':
            ticket_summary = get_ticket_status()
            print("\n=== 工单状态 ===")
            print(f"总工单数: {ticket_summary['total']}")
            print(f"待处理: {ticket_summary['pending']}")
            print(f"处理中: {ticket_summary['in_progress']}")
            print(f"已解决: {ticket_summary['resolved']}")
            print(f"L2投诉: {ticket_summary['by_type']['l2_complaint']}")
            print(f"L3投诉: {ticket_summary['by_type']['l3_complaint']}")
            continue
        elif user_input.lower() == 'tools':
            tools = get_available_tools()
            print("\n=== 可用MCP工具 ===")
            for tool in tools:
                print(f"\n工具: {tool['name']}")
                print(f"描述: {tool['description']}")
                params = tool.get('parameters', {})
                if 'properties' in params:
                    print("参数:")
                    for pname, pinfo in params['properties'].items():
                        print(f"  - {pname}: {pinfo.get('description', '')}")
            continue
        
        result = process_customer_query(user_input)
        print("\n" + "=" * 60)
        print(f"处理结果:")
        print(f"  是否终止: {'是' if result.get('terminated') else '否'}")
        print(f"  当前Agent: {result.get('current_agent', 'N/A')}")
        print(f"  意图类型: {result.get('intent', 'N/A')}")
        print(f"  情绪标签: {result.get('mood_tag', '正常')}")
        print(f"  过滤动作: {result.get('filter_action', 'N/A')}")
        if result.get('ticket_id'):
            print(f"  工单编号: {result.get('ticket_id')}")
        if result.get('task_id'):
            print(f"  任务编号: {result.get('task_id')}")
        print("=" * 60)
        print(result.get('response', ''))
        print("=" * 60)
