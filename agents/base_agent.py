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
        self._pending_action: Any = None  # 确认卡片请求（由伪工具钩子写入）

    def set_llm(self, llm):
        self.llm = llm

    # ------------------------------------------------------------------
    # 工具与提示词
    # ------------------------------------------------------------------

    def _react_tools(self) -> list:
        """默认共享工具池（不含 create_complaint，仅投诉专家可登记新投诉）。"""
        from services.tools import all_tools
        return [t for t in all_tools() if t.name != "create_complaint"]

    def _react_system_prompt(self, identity: str = "") -> str:
        from datetime import date as _date
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            f"今天的日期是 {_date.today().isoformat()}。"
            "用户提到\"明天/后天/下周X\"等相对日期时，先据此换算为具体日期（YYYY-MM-DD）再传给工具。\n\n"
            "回答规范：\n"
            "1. 需要数据时**主动调用工具**查询（航班/价格/趋势/延误/天气/联网搜索/订单/投诉），不要凭记忆编造；\n"
            "2. 工具返回的数值（票价、概率、温度等）如实引用，不得虚构；\n"
            "3. 若信息不足（如缺少日期），先礼貌追问，不要猜测；\n"
            "4. 一条消息里包含多个需求时，逐一向用户说明清楚；\n"
            "5. 展示航班、价格等列表信息时，一律用 Markdown 表格（表头：航班号/航司/时间/舱位/价格），"
            "不要用大段文字罗列；\n"
            "6. 用简洁、专业、友好的中文回复。"
            + self._identity_suffix(identity)
        )

    @staticmethod
    def _identity_suffix(identity: str) -> str:
        return f"\n\n{identity}" if identity else ""

    def _identity_context(self, state: Dict[str, Any]) -> str:
        """从 state 取登录会员身份并查库，生成注入提示词的身份说明。"""
        member_id = state.get("member_id")
        if not member_id:
            return ""
        try:
            from services import flight_repo
            cust = flight_repo.get_customer(member_id)
            if cust.get("error"):
                return f"当前登录会员：{member_id}（档案校验失败，涉及该身份的写操作请谨慎）"
            base = (
                f"当前登录会员：{cust['member_id']} {cust['name']}"
                f"（{cust['level']}会员，手机尾号{str(cust['phone'])[-4:]}）。"
                "涉及该会员的订单/账单/投诉查询与订票操作可直接使用此身份，无需向用户追问会员号；"
                "若用户提供的会员号与登录身份不一致，请礼貌提醒并以登录身份为准。"
                "系统已启用越权防护：查询或操作其他会员的数据时，工具层会直接拒绝并返回无权限，"
                "此时请如实向用户说明，并引导其操作本人数据，不要重试他人会员号。"
            )
            try:
                from services import notification_repo
                unread = notification_repo.unread_count(cust["member_id"])
            except Exception:
                unread = 0
            if unread > 0:
                base += (
                    f"\n注意：该会员有 {unread} 条未读站内通知（退款审核结果/订单超时取消/投诉回复等）。"
                    "当通知内容与本轮话题相关，或用户询问审批进度/处理结果时，"
                    "先调用 query_notifications 查看并如实转告，不要虚构处理结果。"
                )
            return base
        except Exception as e:
            print(f"{self.name} 身份注入失败: {e}")
            return ""

    def _on_tool_call(self, name: str, args: dict):
        """工具调用钩子。返回 (True, result) 表示已拦截（不执行真实工具），result 作为工具返回值。"""
        return (False, None)

    # ------------------------------------------------------------------
    # ReAct 循环（模型自主 function calling，保持逐 token 流式回调）
    # ------------------------------------------------------------------

    def _react_answer(self, user_query: str, history: List[Dict] = None, identity: str = "") -> str:
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

            messages = [SystemMessage(content=self._react_system_prompt(identity))]
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
                    handled, hook_result = self._on_tool_call(name, args)
                    if handled:
                        result = hook_result
                    else:
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

    def _plain_answer(self, user_query: str, history: List[Dict] = None, identity: str = "") -> str:
        """无工具降级：一次普通调用（保持人设与上下文）。"""
        if self.llm is None:
            return "抱歉，系统暂时无法处理您的请求，请稍后重试。"
        try:
            messages = [SystemMessage(content=self._react_system_prompt(identity))]
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
        """process 的公共骨架：取历史与身份 → ReAct → 降级 → 返回增量。

        执行期间把登录身份写入受信上下文（services.security），工具层据此做
        归属硬校验——身份来自图输入，不经过 LLM 的工具参数，无法被诱导越权。
        """
        from services import security

        query = state["customer_query"]
        history = list(state.get("messages") or [])
        # 历史末尾是本轮用户消息本身（由图入口的 reducer 追加），剔除后传入
        if history and history[-1].get("role") == "user" and history[-1].get("content") == query:
            history = history[:-1]

        identity = self._identity_context(state)
        self._pending_action = None

        token = security.set_current_member(state.get("member_id"))
        try:
            try:
                response = self._react_answer(query, history, identity)
            except Exception as e:
                print(f"{self.name} ReAct 异常: {e}")
                response = ""
            if not response:
                response = self._plain_answer(query, history, identity)
        finally:
            security.reset_current_member(token)

        return {
            "agent_response": response,
            "current_agent": self.name,
            "pending_action": self._pending_action,
        }

    def get_info(self) -> Dict[str, Any]:
        return {"name": self.name, "role": self.role, "expertise": self.expertise}
