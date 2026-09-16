"""模拟支付渠道：站内一步付讫，不触达任何外部网关。

存在的意义：
1. 未配置支付宝凭据时，订票→支付的完整业务链路依然可演示；
2. 现有单测与回归脚本依赖「一步付讫」语义，切到真实渠道会全挂，
   因此 mock 是默认渠道（PAY_PROVIDER=mock）。

刻意不支持异步回调：mock 没有真实网关会发通知，
开放伪造回调接口等于给系统留一个「不改单也能改单」的后门。

退款：同步成功（演示与单测需要覆盖「渠道退款失败」这条分支，
故提供 `fail_refunds` 开关把渠道置为失败，不需要联网造异常）。
"""

from datetime import datetime
from typing import Any, Dict, Tuple

from .base import (MODE_DIRECT, PROVIDER_MOCK, REFUND_FAILED, REFUND_OK,
                   PaymentProvider)


class MockProvider(PaymentProvider):
    name = PROVIDER_MOCK
    label = "模拟支付"
    mode = MODE_DIRECT

    def __init__(self, *, fail_refunds: bool = False):
        # 仅供演示/测试：置 True 后所有退款请求都返回渠道失败
        self.fail_refunds = bool(fail_refunds)
        # 已受理过的退款请求号 → 退款金额，用来模拟支付宝
        # 「同一 out_trade_no + out_request_no 只退一次」的幂等语义
        self.refunded_requests: Dict[str, str] = {}

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

    def verify_notify(self, data: Dict[str, Any] = None, *,
                      raw_body: Any = None) -> Tuple[bool, Dict[str, Any]]:
        return False, {"error": "模拟渠道不接收异步通知，请使用站内确认支付"}

    def query_payment(self, pay_no: str) -> Dict[str, Any]:
        return {"found": False, "paid": False, "trade_no": None, "amount": None}

    def close_payment(self, pay_no: str) -> bool:
        return True

    def refund(self, *, pay_no: str, amount: float, out_request_no: str,
               reason: str = "", trade_no: str = None) -> Dict[str, Any]:
        """模拟退款：同步成功。

        金额与幂等键按真实渠道语义处理：同一个 `out_request_no` 重复请求
        返回 `duplicate=True` 且**不再产生新的资金变动**——业务层的
        「重复退款」处理逻辑因此可以离线验证。
        """
        if not out_request_no:
            return {"ok": False, "channel_status": REFUND_FAILED, "duplicate": False,
                    "retryable": False, "unknown": False,
                    "error": "缺少退款请求号 out_request_no"}
        if self.fail_refunds:
            return {"ok": False, "channel_status": REFUND_FAILED, "duplicate": False,
                    "retryable": True, "unknown": False,
                    "code": "40004", "sub_code": "MOCK.SELLER_BALANCE_NOT_ENOUGH",
                    "error": "模拟渠道余额不足（演示渠道失败用）",
                    "raw": {"mock": True}}
        key = str(out_request_no)
        if key in self.refunded_requests:
            return {"ok": True, "channel_status": REFUND_OK, "duplicate": True,
                    "retryable": False, "unknown": False,
                    "fund_change": "N",
                    "refund_fee": self.refunded_requests[key],
                    "channel_refund_no": f"MOCKRF{trade_no or pay_no}",
                    "gmt_refund_pay": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "raw": {"mock": True, "out_request_no": key, "duplicate": True}}
        self.refunded_requests[key] = self.fmt_amount(amount)
        return {"ok": True, "channel_status": REFUND_OK, "duplicate": False,
                "retryable": False, "unknown": False,
                "fund_change": "Y",
                "refund_fee": self.fmt_amount(amount),
                "channel_refund_no": f"MOCKRF{trade_no or pay_no}",
                "gmt_refund_pay": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "raw": {"mock": True, "out_request_no": key,
                        "refund_amount": self.fmt_amount(amount), "reason": reason}}

    def query_refund(self, *, pay_no: str, out_request_no: str,
                     trade_no: str = None) -> Dict[str, Any]:
        """查模拟退款记录（对账分支可离线验证）。"""
        amount = self.refunded_requests.get(str(out_request_no))
        if amount is None:
            # 查无此退款 = 渠道明确回答「没退过」→ 可以安全重试
            return {"queried": True, "found": False, "refunded": False,
                    "error": "模拟渠道无此退款记录"}
        return {"queried": True, "found": True, "refunded": True,
                "refund_status": "REFUND_SUCCESS", "refund_amount": amount,
                "out_request_no": str(out_request_no),
                "channel_refund_no": f"MOCKRF{trade_no or pay_no}"}

