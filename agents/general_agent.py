"""
综合客服智能体：问候、闲聊与一般咨询兜底，无工具。
"""

from typing import Dict, List, Any

from .base_agent import BaseAgent


class GeneralAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="综合客服",
            role="一般咨询处理",
            expertise=["问候接待", "闲聊答疑", "引导转接"],
        )

    def _react_tools(self) -> list:
        return []

    def _react_system_prompt(self) -> str:
        return (
            f"你是{self.name}，一家航空公司的客服专员，负责接待与一般咨询。\n\n"
            "回答规范：\n"
            "1. 语气友好、自然、简洁，不啰嗦；\n"
            "2. 涉及具体航班、价格、订单、投诉等业务问题时，礼貌引导用户补充信息，"
            "或说明可以为其转接相应专员；\n"
            "3. 不要编造航班、价格等具体数据；\n"
            "4. 结合对话历史连贯回复。"
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
