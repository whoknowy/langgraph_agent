"""
机票专家智能体：航班查询/比价、价格构成、目的地天气、延误预测、价格走势。

数据一律来自本地 SQLite（services/ 工具池）与 Open-Meteo 实时接口，
由 BaseAgent 的共享 ReAct 循环驱动，本文件只定义人设与提示词。
"""

from typing import Dict, List, Any

from .base_agent import BaseAgent


class ProductAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="机票专家",
            role="机票信息咨询和推荐",
            expertise=["航班查询", "价格比较", "价格构成", "目的地天气", "延误预测", "价格走势"],
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
