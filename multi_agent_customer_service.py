"""
多智能体航空客服系统（LangGraph StateGraph）

图结构（精简重构版）:

    START
      ↓
    sensitive_guard   ──L3高危──→ final_response（拦截话术）→ END
      ↓ 放行
    intent_classifier（单次 LLM 分类，4 选 1）
      ↓ 条件路由
    product_agent / billing_agent / complaint_agent / general_agent
      ↓
    final_response → END

设计要点:
- 工具调用全部由各 Agent 内部的 ReAct 循环完成（共享工具池，本地 SQLite + Open-Meteo），
  路由层不再预执行工具、不再解析参数；
- 多意图消息由主 Agent 的 ReAct 循环自然处理（依次调多个工具，一段流式回答）；
- 对话历史由 LangGraph 线程 checkpoint 承载，Agent 从 state["messages"] 读取；
- messages 通道带 add reducer：各节点只返回本轮增量，严禁整段历史回写
  （见 _copy_state 注释）。
"""

import uuid
from typing import Dict, List, Any, Optional, TypedDict, Annotated
from operator import add

from dotenv import load_dotenv
load_dotenv()

from skills import run_sensitive_guard, classify_intent
from agents import initialize_agents

try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    print("警告: LangGraph 未安装，无法构建工作流")


# ========== 状态定义 ==========

class AgentState(TypedDict):
    """工作流状态定义（精简版）"""
    customer_query: str
    session_id: str
    # LangGraph 线程 checkpoint 累积的对话历史（add reducer，节点只返回增量）
    messages: Annotated[List[Dict], add]

    # 输入守卫结果
    guard_blocked: Optional[bool]
    guard_response: Optional[str]

    # 登录会员身份（由 web 层传入）
    member_id: Optional[str]

    # 确认卡片请求（伪工具钩子产生，前端确认后经 REST 执行）
    pending_action: Optional[Dict[str, Any]]

    # 路由结果
    target_agent: Optional[str]

    # Agent 处理结果
    agent_response: Optional[str]
    current_agent: Optional[str]

    # 最终响应
    response: Optional[str]


def _copy_state(state: AgentState) -> AgentState:
    """复制节点输入状态，但不携带 messages 通道。

    messages 是带 add reducer 的累加通道：input 中已包含用户消息，各节点只应
    返回本轮增量（由 final_response_node 追加 assistant 轮）；若每个节点都把
    整段历史原样返回，reducer 会随每个节点反复累加，造成滚雪球式重复。
    """
    return {k: v for k, v in state.items() if k != "messages"}


# ========== 节点定义 ==========

def sensitive_guard_node(state: AgentState) -> AgentState:
    """输入守卫节点：敏感词匹配，L3 高危拦截。"""
    query = state.get("customer_query", "")
    result = run_sensitive_guard(query)

    new_state = _copy_state(state)
    new_state["guard_blocked"] = result["blocked"]
    new_state["guard_response"] = result["response"]
    if result["blocked"]:
        print(f"[守卫] 高危输入已拦截: {result['matched_words']}")
    elif result["has_sensitive"]:
        print(f"[守卫] 低危敏感词放行: {result['matched_words']}")
    return new_state


def _history_without_current(state: AgentState) -> List[Dict]:
    """取 checkpoint 历史，剔除末尾的本轮用户消息（由调用方单独传递）。"""
    history = list(state.get("messages") or [])
    query = state.get("customer_query", "")
    if history and history[-1].get("role") == "user" and history[-1].get("content") == query:
        history = history[:-1]
    return history


def intent_classifier_node(state: AgentState) -> AgentState:
    """意图分类节点：单次 LLM 调用，4 选 1。"""
    query = state.get("customer_query", "")
    result = classify_intent(query, _history_without_current(state))

    agent_map = {
        "product": "product_agent",
        "billing": "billing_agent",
        "complaint": "complaint_agent",
        "general": "general_agent",
    }
    target = agent_map.get(result["agent"], "general_agent")

    new_state = _copy_state(state)
    new_state["target_agent"] = target
    print(f"[分类] '{query[:30]}...' -> {target} ({result.get('note', '')})")
    return new_state


def route_after_guard(state: AgentState) -> str:
    if state.get("guard_blocked", False):
        return "final_response"
    return "intent_classifier"


def route_to_agent(state: AgentState) -> str:
    target = state.get("target_agent", "general_agent")
    return target if target in AGENT_NODES else "general_agent"


def _execute_agent_node(state: AgentState, agent_name: str) -> AgentState:
    agents = initialize_agents()
    target_agent = agents.get(agent_name)

    if not target_agent:
        new_state = _copy_state(state)
        new_state["agent_response"] = "抱歉，暂无法处理您的请求。"
        new_state["current_agent"] = "system"
        return new_state

    result = target_agent.process(state)

    new_state = _copy_state(state)
    new_state["agent_response"] = result.get("agent_response", "")
    new_state["current_agent"] = result.get("current_agent", agent_name)
    # 每轮都覆盖写，避免上一轮的确认卡片残留
    new_state["pending_action"] = result.get("pending_action")
    print(f"[Agent] {agent_name} 执行完成")
    return new_state


def product_agent_node(state: AgentState) -> AgentState:
    print("[节点] 机票专家")
    return _execute_agent_node(state, "product_agent")


def billing_agent_node(state: AgentState) -> AgentState:
    print("[节点] 账单专家")
    return _execute_agent_node(state, "billing_agent")


def complaint_agent_node(state: AgentState) -> AgentState:
    print("[节点] 投诉处理专家")
    return _execute_agent_node(state, "complaint_agent")


def general_agent_node(state: AgentState) -> AgentState:
    print("[节点] 综合客服")
    return _execute_agent_node(state, "general_agent")


AGENT_NODES = ("product_agent", "billing_agent", "complaint_agent", "general_agent")


def final_response_node(state: AgentState) -> AgentState:
    """最终响应节点：组装守卫话术与 Agent 回答，追加本轮 assistant 消息。"""
    guard_blocked = state.get("guard_blocked", False)
    guard_response = state.get("guard_response", "") or ""
    agent_response = state.get("agent_response", "") or ""

    if guard_blocked:
        final_response = guard_response or "抱歉，您的消息无法处理。"
    else:
        final_response = agent_response or "感谢您的咨询，如有其他问题请随时提问。"

    # 拦截路径不经过 Agent 节点，清掉可能残留的上一轮确认卡片
    pending_action = None if guard_blocked else state.get("pending_action")

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
    new_state["pending_action"] = pending_action
    new_state["messages"] = messages_update

    print("[节点] 最终响应生成完成")
    return new_state


# ========== 图构建 ==========

def build_workflow():
    """构建 LangGraph 工作流图。"""
    if not HAS_LANGGRAPH:
        return None

    graph = StateGraph(AgentState)

    graph.add_node("sensitive_guard", sensitive_guard_node)
    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("product_agent", product_agent_node)
    graph.add_node("billing_agent", billing_agent_node)
    graph.add_node("complaint_agent", complaint_agent_node)
    graph.add_node("general_agent", general_agent_node)
    graph.add_node("final_response", final_response_node)

    graph.set_entry_point("sensitive_guard")

    graph.add_conditional_edges(
        "sensitive_guard",
        route_after_guard,
        {"intent_classifier": "intent_classifier", "final_response": "final_response"},
    )

    graph.add_conditional_edges(
        "intent_classifier",
        route_to_agent,
        {name: name for name in AGENT_NODES},
    )

    for name in AGENT_NODES:
        graph.add_edge(name, "final_response")

    graph.add_edge("final_response", END)

    return graph.compile()


# ========== 向后兼容的执行器（本地兜底路径） ==========

class SkillPipelineExecutor:
    """进程内执行器：langgraph 服务不可用时由 web 层调用。"""

    def __init__(self):
        self.workflow = build_workflow() if HAS_LANGGRAPH else None
        if self.workflow is not None:
            print("✅ 使用 LangGraph 工作流（进程内）")
        else:
            print("⚠️ LangGraph 未安装，无法处理请求")

    def execute(self, query: str, session_id: str = None, customer_info: Dict = None) -> Dict[str, Any]:
        if session_id is None:
            session_id = str(uuid.uuid4())
        member_id = (customer_info or {}).get("member_id")
        return self._execute_with_langgraph(query, session_id, member_id)

    def _execute_with_langgraph(self, query: str, session_id: str, member_id: str = None) -> Dict[str, Any]:
        if self.workflow is None:
            return {"success": False, "session_id": session_id,
                    "response": "系统组件缺失，请检查 LangGraph 安装。", "terminated": False}

        initial_state: AgentState = {
            "customer_query": query,
            "session_id": session_id,
            "member_id": member_id,
            "messages": [{"role": "user", "content": query}],
        }

        try:
            result = self.workflow.invoke(initial_state)
            return {
                "success": True,
                "session_id": session_id,
                "response": result.get("response", ""),
                "current_agent": result.get("current_agent", ""),
                "target_agent": result.get("target_agent", ""),
                "messages": result.get("messages", []),
                "terminated": False,
            }
        except Exception as e:
            print(f"❌ 工作流执行失败: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "session_id": session_id,
                    "response": f"处理出错: {e}", "terminated": False}


# ========== 全局初始化与入口 ==========

pipeline_executor = SkillPipelineExecutor()


def process_customer_query(customer_query: str, session_id: str = None) -> Dict[str, Any]:
    """处理客户查询的入口函数（本地兜底路径）。"""
    return pipeline_executor.execute(customer_query, session_id)


def make_graph():
    """创建工作流图 - 供 LangGraph 服务使用。"""
    return build_workflow()
