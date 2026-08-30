"""
投诉处理专家智能体：投诉查询、新投诉登记、安抚与解决方案。

投诉记录存于本地 SQLite complaints 表；用户表达新投诉时由模型调用
create_complaint 工具落库并返回投诉单号（替代旧版模拟工单系统）。
"""

from typing import Dict, List, Any

from .base_agent import BaseAgent


class ComplaintAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="投诉处理专家",
            role="客户投诉和建议处理",
            expertise=["投诉查询", "投诉登记", "安抚沟通", "补偿方案"],
        )

    def _react_tools(self) -> list:
        # 投诉专家独享 create_complaint（登记新投诉落库）
        from services.tools import all_tools
        return all_tools()

    def _react_system_prompt(self, identity: str = "") -> str:
        return (
            f"你是{self.name}，专门负责{self.role}。"
            f"你的专业领域包括：{', '.join(self.expertise)}。\n\n"
            "处理规范：\n"
            "1. 用户询问已有投诉的处理进度时，调用 query_complaint 查询（需会员号或投诉单号，缺少时先追问）；\n"
            "2. 用户表达对本次服务/订单的**新投诉**并希望正式反馈时：先致歉安抚，"
            "**立即调用 create_complaint 登记投诉**（会员号直接用登录身份，订单号可留空，"
            "**不要因为缺少订单号或其他细节而追问、拖延登记**，细节可在登记后补充询问），"
            "登记完成后在回复中告知投诉单号与后续处理时限；"
            "用户明确要求\"正式提交/登记投诉\"时必须调用 create_complaint，"
            "即使名下已有历史投诉记录也不要只查询了事；\n"
            "3. 用户只是情绪宣泄、未要求登记时，以安抚和解决方案沟通为主，不要强行落库；\n"
            "4. 工具返回的内容如实引用，不得虚构单号或处理结果；\n"
            "5. 用真诚、耐心、专业的中文回复。"
            + self._identity_suffix(identity)
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
