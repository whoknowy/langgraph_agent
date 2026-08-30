"""
账单专家智能体：订单查询、账单明细、支付/退款/发票问题。

订单数据来自本地 SQLite（get_order_bill 工具），由共享 ReAct 循环驱动。
"""

from typing import Dict, List, Any

from .base_agent import BaseAgent


class BillingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="账单专家",
            role="订单账单与支付问题处理",
            expertise=["订单查询", "账单明细", "支付问题", "退款处理", "发票"],
        )

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(state)
