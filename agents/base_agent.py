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

    # ---- 确认卡片一致性守卫（子类按需声明）----
    # 伪工具名：调用后由 _on_tool_call 拦截并写入 _pending_action（卡片才会出现）
    _card_pseudo_tools: tuple = ()
    # 卡片按钮名：子类声明自己卡片上的按钮文案（如 "确认预订"）
    _card_button_hints: tuple = ()
    # 全站已知的卡片按钮文案（与 PendingActionCard.vue 的 TITLES 对齐）。
    # 用途：**没有发卡能力的 agent**（general_agent 无工具、complaint_agent 只有
    # 投诉工具）也可能顺着上一轮的话术说"请点击确认预订"——它连伪工具都没有，
    # 这句一定是假的。没有这份全站清单就检不出来（子类自己的 _card_button_hints 为空）。
    _known_card_buttons: tuple = ("确认预订", "确认退票", "确认改签", "确认值机",
                                  "确认改座", "值机选座")
    # 声称"卡片已经在这里"的话术特征（与按钮名同时出现才算"在引导用户点卡片"）
    _card_claim_hints: tuple = ("已生成", "已发起", "已为您生成", "已为您准备",
                                "请点击", "点击页面", "点击下方", "请在页面", "请点页面")
    # 补调时告诉模型各伪工具需要哪些参数
    _card_param_hints: str = ""

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
    # 确认卡片一致性守卫
    #
    # 背景（真实踩到的多轮 bug）：模型有时**只在文字里说**"已生成订票确认，请点击
    # 「确认预订」"，却根本没调用 submit_booking_request —— 后端 pending_action 为 null，
    # 页面上不会出现任何新卡片；而上一轮那张旧卡片还在，用户一点就操作到**上一轮的目标**。
    # 提示词里已写了"严禁光说不调"，但模型在多轮历史里会顺着上一轮的话术继续编，
    # 所以这里做**代码层兜底**：检出这种不一致 → 自动补调一次 → 仍不行就换成诚实话术。
    # ------------------------------------------------------------------

    def _claims_card(self, text: str) -> bool:
        """文本是否在"告诉用户卡片已就绪、去点按钮"（即声称本轮发起了卡片）。

        注意：**不要求本 agent 有发卡能力**——正因为它可能没有，才更需要检出来。
        没有自己的按钮清单时退回全站清单（_known_card_buttons）。
        """
        if not text:
            return False
        buttons = self._card_button_hints or self._known_card_buttons
        button = any(h in text for h in buttons)
        claim = any(h in text for h in self._card_claim_hints)
        return button and claim

    def _card_retry_instruction(self) -> str:
        """补调指令：明确告诉模型它漏调了，并给出参数要求。"""
        tools = "、".join(self._card_pseudo_tools)
        params = f"（参数：{self._card_param_hints}）" if self._card_param_hints else ""
        buttons = "、".join(f"「{b}」" for b in self._card_button_hints) or "确认"
        return (
            f"你刚才的回复里在引导用户点击{buttons}按钮，但你**没有调用任何工具**，"
            f"所以页面上不会出现卡片，用户只会去点上一轮遗留的旧卡片，从而操作到错误的目标上。\n"
            f"请立即调用 {tools}{params}（参数值从用户本轮消息与上文提取）；"
            f"若确实缺少必要信息，就改为向用户追问所缺的那一项，不要再复述卡片话术。"
        )

    def _no_card_instruction(self) -> str:
        """无发卡能力时的纠正指令：别提卡片，直接给答案或把需求问细。"""
        return (
            "你刚才的回复在引导用户点击卡片按钮，但**你这一轮没有任何卡片**"
            "（你手上也没有能发起卡片的工具），页面上不会出现新卡片，"
            "用户只会去点上一轮遗留的旧卡片，从而操作到错误的目标上。\n"
            "请重新组织回复：不要提卡片、不要提按钮；"
            "直接回答用户的问题；若这属于订票/退票/改签/值机等需要卡片确认的业务，"
            "就请用户把需求说得更具体（例如航班号与日期、订单号）以便继续办理，"
            "并说明可以为他转接对应专员。"
        )

    def _card_guard_instruction(self) -> str:
        """纠正指令：有发卡工具就要求补调，没有就要求别再说卡片。"""
        if self._card_pseudo_tools:
            return self._card_retry_instruction()
        return self._no_card_instruction()

    def _no_card_fallback_text(self) -> str:
        """补调仍失败时的诚实兜底：不谎称有卡片，让用户重说一次。"""
        return ("抱歉，这次我没能为您的操作生成确认卡片。"
                "请再说一遍要办理的事项（例如「订 2026-09-18 上海到北京的 3U1155 经济舱 1 人」"
                "或「退掉订单 O1234567」），我立刻为您重新发起。")

    # ------------------------------------------------------------------
    # ReAct 循环（模型自主 function calling，保持逐 token 流式回调）
    # ------------------------------------------------------------------

    def _react_answer(self, user_query: str, history: List[Dict] = None, identity: str = "",
                      obs_handler=None) -> str:
        """运行模型自主工具调用循环，返回最终答复文本。

        无绑定工具或调用失败时返回 ""（由调用方走无工具降级链路）。
        obs_handler 由 _run 注入：Langfuse 启用时挂到 llm.stream
        与 tool.invoke 的 callbacks，使一整段 ReAct 循环聚成一条 trace。
        """
        tools = self._react_tools()
        if not tools or self.llm is None:
            return ""

        import json
        from services import langfuse_setup

        try:
            from services.tools import tools_by_name

            key = tuple(sorted(t.name for t in tools))
            if key not in self._react_cache:
                self._react_cache[key] = self.llm.bind_tools(tools)
            llm_with_tools = self._react_cache[key]
            tool_config = langfuse_setup.llm_config(obs_handler)  # 合并上下文后的 config 或 None

            messages = [SystemMessage(content=self._react_system_prompt(identity))]
            messages.extend(self._history_messages(history))
            messages.append(HumanMessage(content=user_query))

            card_retried = False
            for _round in range(5):
                full_text, tool_calls = self._stream_with_tools(llm_with_tools, messages,
                                                                obs_handler=obs_handler)
                if not tool_calls:
                    if self._pending_action is None and self._claims_card(full_text):
                        # 话术称卡片已就绪、但本轮没调用伪工具 → 卡片其实不存在
                        if not card_retried:
                            card_retried = True
                            how = ("自动补调一次" if self._card_pseudo_tools
                                   else "本 agent 无发卡工具，要求其别提卡片")
                            print(f"[{self.name}] ⚠️ 话术称卡片已生成但未调用伪工具，{how}")
                            messages.append(AIMessage(content=full_text))
                            messages.append(HumanMessage(content=self._card_guard_instruction()))
                            continue
                        # 纠正也没成功：换成诚实话术，绝不让用户去点上一轮的旧卡片
                        print(f"[{self.name}] ❌ 纠正后仍在谎称卡片，改用诚实兜底话术")
                        return self._no_card_fallback_text()
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
                                # tool.invoke 同样挂 callback，让工具调用嵌套到同一 trace
                                raw = tool.invoke(args, config=tool_config) if tool_config else tool.invoke(args)
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

    def _plain_answer(self, user_query: str, history: List[Dict] = None, identity: str = "",
                      obs_handler=None) -> str:
        """无工具降级：一次普通调用（保持人设与上下文）。

        这条链路**没有任何工具**（general_agent 常态走这里，其余 agent 是 ReAct
        异常后的降级），所以它若声称"卡片已生成、请点击"，一定是假话——同样过一遍
        卡片一致性守卫（见 _claims_card）：先纠正一次，仍不改则退回诚实话术。
        """
        from services import langfuse_setup
        if self.llm is None:
            return "抱歉，系统暂时无法处理您的请求，请稍后重试。"
        try:
            messages = [SystemMessage(content=self._react_system_prompt(identity))]
            messages.extend(self._history_messages(history))
            messages.append(HumanMessage(content=user_query))
            cfg = langfuse_setup.llm_config(obs_handler)

            def _call(msgs):
                resp = self.llm.invoke(msgs, config=cfg) if cfg else self.llm.invoke(msgs)
                return resp.content or ""

            text = _call(messages)
            if self._pending_action is None and self._claims_card(text):
                print(f"[{self.name}] ⚠️ 无工具链路却在话术里称卡片已生成，要求其别提卡片")
                messages.append(AIMessage(content=text))
                messages.append(HumanMessage(content=self._card_guard_instruction()))
                text2 = _call(messages)
                if not self._claims_card(text2):
                    return text2
                print(f"[{self.name}] ❌ 纠正后仍在谎称卡片，改用诚实兜底话术")
                return self._no_card_fallback_text()
            return text
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
    def _stream_with_tools(llm, messages, obs_handler=None):
        """流式调用并累积正文与 tool_calls 分片（保持逐 token 回调）。"""
        import json
        from services import langfuse_setup
        full_text = ""
        tc_slots: dict = {}
        order: list = []
        cfg = langfuse_setup.llm_config(obs_handler)
        for chunk in (llm.stream(messages, config=cfg) if cfg else llm.stream(messages)):
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
        可观测性：包一层 Langfuse trace（session_id=线程、user_id=会员），
        未配置时为 no-op。
        """
        from services import langfuse_setup, security

        query = state["customer_query"]
        history = list(state.get("messages") or [])
        # 历史末尾是本轮用户消息本身（由图入口的 reducer 追加），剔除后传入
        if history and history[-1].get("role") == "user" and history[-1].get("content") == query:
            history = history[:-1]

        identity = self._identity_context(state)
        self._pending_action = None

        with langfuse_setup.trace(
            name=f"agent:{self.name}",
            session_id=state.get("session_id"),
            user_id=state.get("member_id"),
            tags=["react", self.name],
        ) as obs_handler:
            token = security.set_current_member(state.get("member_id"))
            try:
                try:
                    response = self._react_answer(query, history, identity, obs_handler=obs_handler)
                except Exception as e:
                    print(f"{self.name} ReAct 异常: {e}")
                    response = ""
                if not response:
                    response = self._plain_answer(query, history, identity, obs_handler=obs_handler)
            finally:
                security.reset_current_member(token)

        return {
            "agent_response": response,
            "current_agent": self.name,
            "pending_action": self._pending_action,
        }

    def get_info(self) -> Dict[str, Any]:
        return {"name": self.name, "role": self.role, "expertise": self.expertise}
