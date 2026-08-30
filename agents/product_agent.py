"""
机票专家智能体：航班查询/比价、价格构成、目的地天气、延误预测、价格走势、订票。

数据一律来自本地 SQLite（services/ 工具池）与 Open-Meteo 实时接口，
由 BaseAgent 的共享 ReAct 循环驱动。订票走确认卡片：收集齐参数后调用
submit_booking_request 伪工具，由前端确认卡片 + REST 接口真正下单。
"""

from typing import Any, Dict

from .base_agent import BaseAgent


class ProductAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="机票专家",
            role="机票信息咨询、推荐与预订",
            expertise=["航班查询", "价格比较", "价格构成", "目的地天气", "延误预测", "价格走势", "机票预订"],
        )

    def _react_tools(self) -> list:
        from services.tools import all_tools, submit_booking_request
        return [t for t in all_tools() if t.name != "create_complaint"] + [submit_booking_request]

    def _on_tool_call(self, name: str, args: dict):
        if name == "submit_booking_request":
            self._pending_action = {"type": "book_flight",
                                    **{k: v for k, v in (args or {}).items() if k != ""}}
            return (True, {"status": "awaiting_user_confirmation",
                           "message": "已生成订票确认请求。请向用户复述航班号/日期/舱位/人数与票价信息，"
                                      "并提示用户点击页面上的\"确认预订\"按钮完成下单，不要自称已下单。"})
        return (False, None)

    def _react_system_prompt(self, identity: str = "") -> str:
        from datetime import date as _date
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            f"今天的日期是 {_date.today().isoformat()}。"
            "用户提到\"明天/后天/下周X\"等相对日期时，先据此换算为具体日期（YYYY-MM-DD）再传给工具。\n\n"
            "回答规范：\n"
            "1. 需要数据时**主动调用工具**查询，不要凭记忆编造；工具返回的数值如实引用；\n"
            "2. 若信息不足（如缺少日期/舱位/人数），先礼貌追问，不要猜测；\n"
            "3. 一条消息里包含多个需求时，逐一向用户说明清楚；\n"
            "4. 用简洁、专业、友好的中文回复。\n\n"
            "订票流程（严格遵守）：\n"
            "a. 用户表达了订票/购买意向后，先用 search_flights 确认航班在指定日期有票及价格；\n"
            "b. 只要航班号、日期、舱位、人数四项信息齐全，**本轮就必须调用 submit_booking_request**"
            "发起确认卡片，不要只展示信息等用户口头再确认——确认卡片本身就是用户确认的环节；\n"
            "c. 调用后在回复中复述航班信息与票价（可用查到的单价×人数计算总价），"
            "引导用户点击页面上的\"确认预订\"按钮完成下单；不要声称已下单成功。"
            + self._identity_suffix(identity)
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
