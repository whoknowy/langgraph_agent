"""
基础智能体类
所有专门智能体的基类
"""

from typing import Dict, List, Any
from abc import ABC, abstractmethod
from langchain_core.messages import AIMessage, HumanMessage
from memory import LangChainSessionManager

class BaseAgent(ABC):
    def __init__(self, name: str, role: str, expertise: List[str], session_manager: LangChainSessionManager = None):
        self.name = name
        self.role = role
        self.expertise = expertise
        self.llm = None  # 将在运行时注入
        self.session_manager = session_manager or LangChainSessionManager()
        self._react_cache: Dict[Any, Any] = {}

    def set_llm(self, llm):
        """设置LLM客户端"""
        self.llm = llm

    def set_session_manager(self, session_manager: LangChainSessionManager):
        """设置会话管理器"""
        self.session_manager = session_manager

    # ------------------------------------------------------------------
    # ReAct 工具调用基础设施（模型自主 function calling）
    # ------------------------------------------------------------------

    def _react_tools(self) -> list:
        """子类覆写：返回本 Agent 绑定的工具（services.tools 中的 @tool 实例）。"""
        return []

    def _react_system_prompt(self) -> str:
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            "回答规范：\n"
            "1. 需要数据时**主动调用工具**查询（航班/价格/趋势/延误/天气/订单/投诉），不要凭记忆编造；\n"
            "2. 工具返回的数值（票价、概率、温度等）如实引用，不得虚构；\n"
            "3. 若信息不足（如缺少会员号/日期），先礼貌追问，不要猜测；\n"
            "4. 用简洁、专业、友好的中文回复。"
        )

    def _react_answer(self, user_payload: str) -> str:
        """运行模型自主工具调用循环（create_react_agent），返回最终答复文本。

        无绑定工具或调用失败时返回 ""（由调用方走常规降级链路）。
        """
        tools = self._react_tools()
        if not tools or self.llm is None:
            return ""

        try:
            import json
            from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

            from services.tools import tools_by_name

            key = tuple(sorted(t.name for t in tools))
            if key not in self._react_cache:
                self._react_cache[key] = self.llm.bind_tools(tools)
            llm_with_tools = self._react_cache[key]

            messages = [
                SystemMessage(content=self._react_system_prompt()),
                HumanMessage(content=user_payload),
            ]

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
    # 会话上下文
    # ------------------------------------------------------------------

    @abstractmethod
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """处理客户查询的抽象方法"""
        pass

    def _get_conversation_context(self, session_id: str, max_messages: int = 6) -> str:
        """从会话管理器获取对话历史上下文"""
        try:
            # 使用会话管理器获取对话上下文
            conversation_context = self.session_manager.get_conversation_context(session_id, max_messages)

            if not conversation_context:
                return ""

            # 格式化对话历史
            context_lines = []
            for msg in conversation_context:
                role = "用户" if msg.get("is_user", True) else "AI"
                content = msg.get("content", "")
                timestamp = msg.get("timestamp", "")
                context_lines.append(f"[{timestamp}] {role}: {content}")

            return "\n".join(context_lines)
        except Exception as e:
            print(f"获取对话上下文时出错: {e}")
            return ""

    def _add_message_to_session(self, session_id: str, message: str, is_user: bool = True):
        """添加消息到会话历史"""
        try:
            self.session_manager.add_message(session_id, message, is_user)
        except Exception as e:
            print(f"添加消息到会话时出错: {e}")

    def _enhance_system_prompt_with_context(self, base_prompt: str) -> str:
        """增强系统提示，添加对话上下文说明"""
        context_instruction = """

重要：请结合对话历史上下文，理解客户之前的问题和需求，提供连贯、个性化的回答。
如果这是多轮对话，请参考之前的对话内容，避免重复信息，并基于客户的新问题提供补充信息。
保持对话的连贯性和自然性，让客户感受到你理解他们的完整需求。"""

        return base_prompt + context_instruction

    def get_info(self) -> Dict[str, Any]:
        """获取智能体信息"""
        return {
            "name": self.name,
            "role": self.role,
            "expertise": self.expertise
        }
