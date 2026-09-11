"""模拟支付渠道：站内一步付讫，不触达任何外部网关。

存在的意义：
1. 未配置支付宝凭据时，订票→支付的完整业务链路依然可演示；
2. 现有单测与回归脚本依赖「一步付讫」语义，切到真实渠道会全挂，
   因此 mock 是默认渠道（PAY_PROVIDER=mock）。

刻意不支持异步回调：mock 没有真实网关会发通知，
开放伪造回调接口等于给系统留一个「不改单也能改单」的后门。
"""

from typing import Any, Dict, Tuple

from .base import MODE_DIRECT, PROVIDER_MOCK, PaymentProvider


class MockProvider(PaymentProvider):
    name = PROVIDER_MOCK
    label = "模拟支付"
    mode = MODE_DIRECT

    def create_payment(self, *, pay_no: str, subject: str, amount: float,
                       return_url: str, notify_url: str) -> Dict[str, Any]:
        return {
            "ok": True,
            "mode": MODE_DIRECT,
            "pay_url": None,
            "pay_no": pay_no,
            "amount": self.fmt_amount(amount),
            "message": "模拟支付：请点击确认完成付款",
        }

    def verify_notify(self, data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        return False, {"error": "模拟渠道不接收异步通知，请使用站内确认支付"}

    def query_payment(self, pay_no: str) -> Dict[str, Any]:
        return {"found": False, "paid": False, "trade_no": None, "amount": None}

    def close_payment(self, pay_no: str) -> bool:
        return True
