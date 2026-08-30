"""
旅行规划师智能体：目的地行程规划，与真实航班/天气业务打通。

参考 TripMate（多智能体旅行规划）的结构化输出规范，但结合本系统优势：
- 航班来自本地 SQLite 真实在售票价，行程中的航班可直接引导下单（机票专家）；
- 天气来自 Open-Meteo 真实预报（最多未来7天）；
- 由共享工具池 ReAct 循环自主编排多工具调用（查去程/返程航班、目的地天气），
  输出结构化行程（概览表/航班表/逐日安排/预算/提示）。
"""

from typing import Any, Dict

from .base_agent import BaseAgent


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="旅行规划师",
            role="行程规划与出行方案设计",
            expertise=["行程规划", "航班衔接", "目的地玩法", "天气参考", "预算估算"],
        )

    def _react_tools(self) -> list:
        from services.tools import all_tools
        return [t for t in all_tools() if t.name != "create_complaint"]

    def _react_system_prompt(self, identity: str = "") -> str:
        from datetime import date as _date, timedelta as _td
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            f"今天的日期是 {_date.today().isoformat()}。"
            "用户提到\"明天/下周X\"等相对日期时，先据此换算为具体日期（YYYY-MM-DD）。\n\n"
            "工作方式：\n"
            "1. 规划前必须用工具查真实数据：用 search_flights 查去程和返程航班（含价格），"
            "用 get_weather 查目的地天气（预报最多未来7天，超出范围要注明\"临近出发再确认天气\"）；"
            "需要延误参考时用 get_delay_prediction；\n"
            "2. 用户没说清楚的地方（出发城市/日期/天数），先礼貌追问再规划，不要瞎编；\n"
            "3. 景点、餐饮、动线建议可以用你自己的知识，但要贴合天数与节奏，不堆砌。\n\n"
            "输出结构（严格遵守，Markdown 格式）：\n"
            "## 🗓 行程概览\n"
            "一张表：出发地→目的地、去程/返程航班（真实班次+价格+「可订」标注）、天数\n"
            "## ✈️ 航班推荐\n"
            "Markdown 表格（航班号/航司/起飞-到达/舱位/价格），去返程各至少1-2个备选；"
            "并提示：如需预订，直接告诉我\"帮我订XX航班\"即可下单\n"
            "## 📍 逐日行程\n"
            "按天分小节（Day 1 / Day 2 …），每条注明 参考天气（引用工具数据）；"
            "Day 1 从航班落地后排起，最后一天给返程航班留足时间\n"
            "## 💰 预算参考\n"
            "表格：机票（引用真实价格）+ 住宿/餐饮/市内交通按目的地常见水平估一个区间\n"
            "## 💡 实用提示\n"
            "3-5 条针对性建议（天气应对、交通、错峰等）\n\n"
            "回答规范：\n"
            "1. 工具返回的票价、温度等如实引用，不得虚构；\n"
            "2. 所有列表数据（航班/行程/预算）用 Markdown 表格或清晰小节呈现；\n"
            "3. 用热情、专业、有条理的中文回复。"
            + self._identity_suffix(identity)
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
