"""
账单专家智能体：订单查询、账单明细、支付/退款/发票、退票申请、值机选座。

订单数据来自本地 SQLite（get_order_bill 工具），由共享 ReAct 循环驱动。
退票/改签走确认卡片，值机选座走座位图卡片：伪工具被 _on_tool_call 拦截产生
pending_action，由前端卡片 + REST 接口真正写库。
"""

from typing import Any, Dict

from .base_agent import BaseAgent


class BillingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="账单专家",
            role="订单账单、支付问题、退改与值机服务",
            expertise=["订单查询", "账单明细", "支付问题", "退款处理", "退票申请", "发票", "值机选座", "登机牌"],
        )

    def _react_tools(self) -> list:
        from services.tools import all_tools, refund_request, change_request, open_seat_map
        return ([t for t in all_tools() if t.name != "create_complaint"]
                + [refund_request, change_request, open_seat_map])

    def _on_tool_call(self, name: str, args: dict):
        if name == "refund_request":
            refund_type = "special" if str((args or {}).get("refund_type", "")).strip() == "special" else "voluntary"
            self._pending_action = {"type": "refund", "refund_type": refund_type,
                                    **{k: v for k, v in (args or {}).items() if k not in ("", "refund_type")}}
            tip = ("已生成自愿退票确认请求，卡片会显示手续费与预计到账金额。"
                   "请复述订单信息，提示用户点击页面上的\"确认退票\"按钮，不要自称已退票。"
                   if refund_type == "voluntary" else
                   "已生成特殊退票确认请求（非自愿，需人工审核，可争取全额退款）。"
                   "请复述订单信息与用户所述原因，提示用户点击\"确认退票\"提交审核，不要自称已退票。")
            return (True, {"status": "awaiting_user_confirmation", "message": tip})
        if name == "change_request":
            self._pending_action = {"type": "change_flight",
                                    **{k: v for k, v in (args or {}).items() if k != ""}}
            return (True, {"status": "awaiting_user_confirmation",
                           "message": "已生成改签确认请求，卡片会显示新旧航班与差价明细。"
                                      "请复述改签方案（原航班→新航班/日期/舱位，免改签费、差价多退少补），"
                                      "提示用户点击\"确认改签\"，不要自称已改签成功。"})
        if name == "open_seat_map":
            from services import checkin_repo, security
            info = checkin_repo.checkin_info((args or {}).get("order_no", ""),
                                             security.get_current_member())
            if info.get("error"):
                return (True, info)
            if not info.get("window_open"):
                return (True, {"error": info.get("window_reason") or "当前不在值机窗口内，不能值机"})
            self._pending_action = {"type": "seat_map",
                                    "order_no": info["order_no"],
                                    "flight_no": info["flight_no"],
                                    "flight_date": info["flight_date"]}
            return (True, {"status": "awaiting_user_confirmation",
                           "message": "已向用户展示座位图选座卡片。请提示用户点击座位并按\"确认值机\"，"
                                      "值机成功后系统会生成电子登机牌；不要自称已值机。"})
        return (False, None)

    def _react_system_prompt(self, identity: str = "") -> str:
        from datetime import date as _date
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            f"今天的日期是 {_date.today().isoformat()}。"
            "用户提到\"明天/后天/下周X\"等相对日期时，先据此换算为具体日期（YYYY-MM-DD）再传给工具。\n\n"
            "回答规范：\n"
            "1. 订单/账单问题先用 get_order_bill 查询真实数据，如实引用金额与状态；\n"
            "2. 若信息不足，先礼貌追问，不要猜测；\n"
            "3. 用简洁、专业、友好的中文回复。\n\n"
            "改签流程（严格遵守）：\n"
            "a. 用户要求改签时，先用 get_order_bill 确认订单存在且状态为\"已出票/已改签\"；\n"
            "b. 用 search_flights 查询用户想要的新日期、同一航线（出发到达相同，任意航司）的在售航班，"
            "并向用户推荐可改方案；新日期必须是今天之后；\n"
            "c. 用户选定后调用 change_request（订单号/新航班号/新日期/新舱位），系统会向用户展示改签确认卡片"
            "（免改签费，票价差多退少补，卡片显示差价明细）；\n"
            "d. 在回复中复述改签方案，引导用户点击\"确认改签\"按钮；不要声称已改签成功。\n\n"
            "退票流程（严格遵守）：\n"
            "a. 用户要求退票时，先用 get_order_bill 确认订单存在且状态为\"已出票/已改签\""
            "（改签后的票同样可以退，费用按新航班距起飞时间计算；"
            "待支付订单提醒其先支付或直接放弃，其他状态说明无法退票）；\n"
            "b. 判定退票类型并调用 refund_request：用户主动要求退票、无特殊原因 → "
            "refund_type=\"voluntary\"（系统按距起飞时间自动计算手续费并即时退款，卡片会显示明细）；"
            "用户说明是航班延误/取消等航空公司原因导致 → refund_type=\"special\""
            "（特殊通道，进入人工审核，可争取全额退款）；\n"
            "c. 然后在回复中复述订单信息与退票方式，引导用户点击\"确认退票\"按钮；不要声称已退票成功。\n\n"
            "值机流程（严格遵守）：\n"
            "a. 用户要求值机/选座/看登机牌时，先用 checkin_info 查询值机状态"
            "（用户没给订单号时先用 get_order_bill 查到订单号）；\n"
            "b. 已值机 → 直接复述座位/登机口/登机时间；未值机且窗口开放（起飞前24小时~45分钟）→ "
            "调用 open_seat_map 展示座位图，提示用户点座位并\"确认值机\"，不要自称已值机；\n"
            "c. 窗口未开放或订单状态不符时，如实告知原因与开放时间；\n"
            "d. 提醒：改签或退票会自动取消值机，之后需要重新选座。"
            + self._identity_suffix(identity)
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
