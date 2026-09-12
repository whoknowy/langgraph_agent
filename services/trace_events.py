"""一轮对话的执行链路跟踪（SSE node/tool 事件 + done 汇总）。

为什么需要它：验收项⑤「状态机可观测」要求前端实时展示 LangGraph 节点与
工具调用链。后端在 chat_web_service.stream_chat_tokens 里已经从 messages
事件的 metadata 解析到 langgraph_node，但只用于过滤意图分类输出、从未下发
（审计缺口 G8）。本模块把"节点切换 / 工具起止"收敛成可单测的纯逻辑，
chat_web_service 只做接线。

节点事件语义：
- sensitive_guard / final_response 是图结构里必然执行的节点，但它们不调
  LLM、不产生 messages 事件，因此由调用方在流的头 / 尾合成（守卫是否拦截
  由线程最终状态 guard_blocked 佐证）；
- intent_classifier 与各 Agent 节点产生 LLM 消息，从消息 metadata 的
  langgraph_node 字段实时观测。

工具事件语义（messages 流里没有工具完成事件，ReAct 工具在 Agent 节点内执行）：
- 模型在消息 X 上发出 tool_calls → 工具 running；
- 下一条消息 Y（≠X）开始流出（工具结果已被模型消费成新消息）→ X 上的工具 done；
- 流结束时仍在 running 的工具统一收口为 done（duration 为近似值）。

事件格式（与既有 SSE 事件对齐，前端按 data.node / data.tool 消费）：
    {"node": {"name", "label", "status", "duration_ms", "note"}}
    {"tool": {"name", "label", "node", "args", "status", "duration_ms"}}
"""

import time
from typing import Any, Dict, List, Optional

# 图节点 → 中文标签（与 multi_agent_customer_service.build_workflow 的节点一一对应，
# local_fallback 为 LangGraph 不可达时的本地兜底链路）
NODE_LABELS = {
    "sensitive_guard": "敏感词守卫",
    "intent_classifier": "意图分类",
    "product_agent": "机票专家",
    "billing_agent": "账单专家",
    "complaint_agent": "投诉处理专家",
    "general_agent": "综合客服",
    "trip_planner_agent": "旅行规划师",
    "final_response": "最终响应",
    "local_fallback": "本地兜底链路",
}

# 工具 → 中文标签（与前端 TOOL_LABELS 保持一致；后端下发，前端兜底）
TOOL_LABELS = {
    "search_flights": "航班搜索", "get_price_trend": "价格趋势", "get_delay_prediction": "延误预测",
    "get_weather": "天气查询", "get_flight_price_detail": "票价构成", "get_order_bill": "账单查询",
    "query_complaint": "投诉查询", "create_complaint": "投诉登记", "submit_booking_request": "订票确认",
    "refund_request": "退票确认", "open_seat_map": "值机选座",
    "baggage_allowance": "行李额度", "web_search": "联网搜索", "query_notifications": "站内通知",
}


def node_label(name: str) -> str:
    return NODE_LABELS.get(name, name)


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def _ms(started_at: float, ended_at: float) -> int:
    return max(0, int(round((ended_at - started_at) * 1000)))


def _public_node(node: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "name": node["name"],
        "label": node["label"],
        "status": node["status"],
        "duration_ms": node.get("duration_ms"),
    }
    if node.get("note"):
        out["note"] = node["note"]
    return out


def _public_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": tool["name"],
        "label": tool["label"],
        "node": tool.get("node"),
        "args": tool.get("args") or {},
        "status": tool["status"],
        "duration_ms": tool.get("duration_ms"),
    }


class RunTrace:
    """跟踪一轮对话经过的图节点与工具调用。

    线程安全说明：单个实例只服务一轮 SSE 流（chat_web_service 每次调用
    新建），不做跨线程共享；纯内存操作，无 IO。
    """

    def __init__(self) -> None:
        self.nodes: List[Dict[str, Any]] = []
        self.tools: List[Dict[str, Any]] = []
        self._open_node: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------ 节点

    def start_node(self, name: str, note: Optional[str] = None) -> List[Dict[str, Any]]:
        """开启一个节点（与当前节点相同则忽略）。返回需要下发的 SSE 事件。"""
        if not name:
            return []
        if self._open_node and self._open_node["name"] == name:
            return []
        events = self._finish_open_node()
        node = {
            "name": name, "label": node_label(name), "status": "running",
            "started_at": time.time(), "note": note,
        }
        self.nodes.append(node)
        self._open_node = node
        events.append({"node": _public_node(node)})
        return events

    def _finish_open_node(self, status: str = "done",
                          note: Optional[str] = None) -> List[Dict[str, Any]]:
        node = self._open_node
        self._open_node = None
        if not node:
            return []
        node["status"] = status
        node["duration_ms"] = _ms(node["started_at"], time.time())
        if note:
            node["note"] = note
        return [{"node": _public_node(node)}]

    def observe_message(self, node_name: Optional[str],
                        msg_id: Optional[str]) -> List[Dict[str, Any]]:
        """messages 流里观测到一条消息：收口上一条消息上的工具，必要时切换节点。"""
        events = self._finish_tools_for_msg(msg_id)
        if node_name:
            events.extend(self.start_node(node_name))
        return events

    # ------------------------------------------------------------ 工具

    def observe_tool(self, name: str, args: Optional[Dict[str, Any]],
                     msg_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """模型在消息 msg_id 上发起工具调用（同一工具名去重，参数滚动更新）。"""
        existing = next((t for t in self.tools if t["name"] == name), None)
        if existing is not None:
            if args:
                existing["args"] = args
            return []
        tool = {
            "name": name, "label": tool_label(name), "args": args or {},
            "status": "running", "started_at": time.time(),
            "msg_id": msg_id,
            # 工具挂在当前执行节点下（前端据此把工具嵌进节点链）
            "node": self._open_node["name"] if self._open_node else None,
        }
        self.tools.append(tool)
        return [{"tool": _public_tool(tool)}]

    def _finish_tools_for_msg(self, msg_id: Optional[str]) -> List[Dict[str, Any]]:
        """新消息开始流出 → 之前消息上的工具已执行完，统一收口。"""
        if not msg_id:
            return []
        events = []
        for t in self.tools:
            if t["status"] == "running" and t.get("msg_id") and t["msg_id"] != msg_id:
                events.append(self._close_tool(t))
        return events

    def _close_tool(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        tool["status"] = "done"
        tool["duration_ms"] = _ms(tool["started_at"], time.time())
        return {"tool": _public_tool(tool)}

    # ------------------------------------------------------------ 收口

    def finish(self, blocked: bool = False) -> List[Dict[str, Any]]:
        """流结束：收口所有未完成节点与工具；未拦截时合成 final_response 节点。"""
        events: List[Dict[str, Any]] = []
        for t in list(self.tools):
            if t["status"] == "running":
                events.append(self._close_tool(t))

        if blocked and self._open_node and self._open_node["name"] == "sensitive_guard":
            # 守卫拦截路径：链路止于守卫，不经过 Agent 与最终响应
            events.extend(self._finish_open_node(note="输入已拦截"))
        else:
            events.extend(self._finish_open_node())
            events.extend(self.start_node("final_response"))
            events.extend(self._finish_open_node())
        return events

    def summary(self) -> Dict[str, Any]:
        """done 事件里的链路汇总（节点 + 工具全量，含最终参数与耗时）。"""
        return {
            "nodes": [_public_node(n) for n in self.nodes],
            "tools": [_public_tool(t) for t in self.tools],
        }
