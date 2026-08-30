"""
基础智能体类

设计要点：
- 工具：默认绑定共享工具池（services.tools 全量，含 create_complaint 由投诉专家覆写），
  工具选择与参数解析完全由模型在 ReAct 循环中自主决定；
- 历史：不再自建会话记录，直接使用 LangGraph 线程 checkpoint 累积的
  state["messages"]（由图节点注入），进程内 SessionManager 已移除；
- 降级：ReAct 失败时退化为一次无工具的普通调用，不再引用内置 mock 数据。
"""

from typing import Dict, List, Any
from abc import ABC, abstractmethod

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


class BaseAgent(ABC):
    # 多轮历史最多注入的最近消息条数（防止 token 随轮数无限增长）
    HISTORY_LIMIT = 8

    def __init__(self, name: str, role: str, expertise: List[str]):
        self.name = name
        self.role = role
        self.expertise = expertise
        self.llm = None  # 由 initialize_agents 注入
        self._react_cache: Dict[Any, Any] = {}

    def set_llm(self, llm):
        self.llm = llm

    # ------------------------------------------------------------------
    # 工具与提示词
    # ------------------------------------------------------------------

    def _react_tools(self) -> list:
        """默认共享工具池（不含 create_complaint，仅投诉专家可登记新投诉）。"""
        from services.tools import all_tools
        return [t for t in all_tools() if t.name != "create_complaint"]

    def _react_system_prompt(self) -> str:
        from datetime import date as _date
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            f"今天的日期是 {_date.today().isoformat()}。"
            "用户提到\"明天/后天/下周X\"等相对日期时，先据此换算为具体日期（YYYY-MM-DD）再传给工具。\n\n"
            "回答规范：\n"
            "1. 需要数据时**主动调用工具**查询（航班/价格/趋势/延误/天气/订单/投诉），不要凭记忆编造；\n"
            "2. 工具返回的数值（票价、概率、温度等）如实引用，不得虚构；\n"
            "3. 若信息不足（如缺少会员号/日期），先礼貌追问，不要猜测；\n"
            "4. 一条消息里包含多个需求时，逐一向用户说明清楚；\n"
            "5. 用简洁、专业、友好的中文回复。"
        )

    # ------------------------------------------------------------------
    # ReAct 循环（模型自主 function calling，保持逐 token 流式回调）
    # ------------------------------------------------------------------

    def _react_answer(self, user_query: str, history: List[Dict] = None) -> str:
        """运行模型自主工具调用循环，返回最终答复文本。

        无绑定工具或调用失败时返回 ""（由调用方走无工具降级链路）。
        """
        tools = self._react_tools()
        if not tools or self.llm is None:
            return ""

        import json

        try:
            from services.tools import tools_by_name

            key = tuple(sorted(t.name for t in tools))
            if key not in self._react_cache:
                self._react_cache[key] = self.llm.bind_tools(tools)
            llm_with_tools = self._react_cache[key]

            messages = [SystemMessage(content=self._react_system_prompt())]
            messages.extend(self._history_messages(history))
            messages.append(HumanMessage(content=user_query))

            for _round in range(5):
                full_text, tool_calls = self._stream_with_tools(llm_with_tools, messages)
                if not tool_calls:
                    return full_text
                messages.append(AIMessage(content=full_text, tool_calls=tool_calls))
                for tc in tool_calls:
                    name = tc.get("name") or ""
                    args = tc.get("args") or {}
                    tool = tools_by_name().get(name)
                    if not tool:
                        result = {"error": f"工具不存在: {name}"}
                    else:
                        try:
                            raw = tool.invoke(args)
                            result = json.loads(raw) if isinstance(raw, str) else raw
                        except Exception as e:
                            result = {"error": f"工具执行失败: {e}"}
                    print(f"[{self.name}] 模型自主调用工具: {name} -> "
                          f"{json.dumps(result, ensure_ascii=False)[:150]}")
                    messages.append(ToolMessage(
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=tc.get("id"),
                    ))

            return messages[-1].content or ""
        except Exception as e:
            print(f"{self.name} ReAct 调用失败，降级为常规链路: {e}")
            return ""

    def _plain_answer(self, user_query: str, history: List[Dict] = None) -> str:
        """无工具降级：一次普通调用（保持人设与上下文）。"""
        if self.llm is None:
            return "抱歉，系统暂时无法处理您的请求，请稍后重试。"
        try:
            messages = [SystemMessage(content=self._react_system_prompt())]
            messages.extend(self._history_messages(history))
            messages.append(HumanMessage(content=user_query))
            return self.llm.invoke(messages).content or ""
        except Exception as e:
            print(f"{self.name} 降级调用失败: {e}")
            return "抱歉，处理您的请求时遇到技术问题，请稍后重试。"

    @staticmethod
    def _history_messages(history: List[Dict] = None) -> list:
        """把 checkpoint 历史消息（dict）转成 LangChain 消息，取最近 HISTORY_LIMIT 条。"""
        converted = []
        for msg in (history or [])[-BaseAgent.HISTORY_LIMIT:]:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg.get("role") == "user":
                converted.append(HumanMessage(content=content))
            else:
                converted.append(AIMessage(content=content))
        return converted

    @staticmethod
    def _stream_with_tools(llm, messages):
        """流式调用并累积正文与 tool_calls 分片（保持逐 token 回调）。"""
        import json
        full_text = ""
        tc_slots: dict = {}
        order: list = []
        for chunk in llm.stream(messages):
            if chunk.content:
                full_text += chunk.content
            for tcc in getattr(chunk, "tool_call_chunks", None) or []:
                idx = tcc.get("index", 0)
                slot = tc_slots.setdefault(idx, {"name": "", "args": "", "id": ""})
                if tcc.get("name"):
                    slot["name"] = tcc["name"]
                if tcc.get("id"):
                    slot["id"] = tcc["id"]
                if tcc.get("args"):
                    slot["args"] += tcc["args"]
                if idx not in order:
                    order.append(idx)

        tool_calls = []
        for idx in order:
            slot = tc_slots[idx]
            args_raw = slot.get("args") or ""
            if args_raw.strip():
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
            else:
                args = {}
            if slot.get("name"):
                tool_calls.append({"name": slot["name"], "args": args,
                                   "id": slot.get("id"), "type": "tool_call"})
        return full_text, tool_calls

    # ------------------------------------------------------------------
    # 子类入口
    # ------------------------------------------------------------------

    @abstractmethod
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """处理客户查询。state 至少包含 customer_query 与 messages（checkpoint 历史）。"""

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """process 的公共骨架：取历史 → ReAct → 降级 → 返回增量。"""
        query = state["customer_query"]
        history = list(state.get("messages") or [])
        # 历史末尾是本轮用户消息本身（由图入口的 reducer 追加），剔除后传入
        if history and history[-1].get("role") == "user" and history[-1].get("content") == query:
            history = history[:-1]

        try:
            response = self._react_answer(query, history)
        except Exception as e:
            print(f"{self.name} ReAct 异常: {e}")
            response = ""
        if not response:
            response = self._plain_answer(query, history)

        return {
            "agent_response": response,
            "current_agent": self.name,
        }

    def get_info(self) -> Dict[str, Any]:
        return {"name": self.name, "role": self.role, "expertise": self.expertise}
