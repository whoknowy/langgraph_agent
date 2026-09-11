"""支付渠道抽象层。

所有渠道（模拟 / 支付宝沙箱 / 支付宝生产 / 微信）实现同一组接口，
业务编排层只依赖本抽象，切换渠道不改动业务代码。

职责边界：
- provider 只负责「与外部支付网关打交道」（签名、拼链接、验签、查单）；
- provider 不碰本地库，落账一律交给 services/payment_repo.py；
- create_payment 绝不产生本地副作用，只返回给前端的跳转/直付信息。

本模块保持零重依赖（不导入 config / LLM 栈），便于单测直接实例化。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

# 支付流水的状态取值（落 payments.status）
STATUS_PENDING = "待支付"
STATUS_PAID = "支付成功"
STATUS_CLOSED = "已关闭"

# 渠道标识
PROVIDER_MOCK = "mock"
PROVIDER_ALIPAY_SANDBOX = "alipay_sandbox"
PROVIDER_ALIPAY = "alipay"

# 给前端的支付方式：redirect=跳转外部收银台（需轮询）；direct=站内直接确认
MODE_REDIRECT = "redirect"
MODE_DIRECT = "direct"


class PaymentProvider(ABC):
    """支付渠道需要实现的四个动作。"""

    name = PROVIDER_MOCK
    label = "未命名渠道"
    mode = MODE_DIRECT

    @abstractmethod
    def create_payment(self, *, pay_no: str, subject: str, amount: float,
                       return_url: str, notify_url: str) -> Dict[str, Any]:
        """发起支付。

        返回 {"ok": True, "mode": "redirect"|"direct", "pay_url": str|None, "raw": ...}；
        失败返回 {"ok": False, "error": "..."}。不得产生任何本地副作用。
        """

    @abstractmethod
    def verify_notify(self, data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """校验异步回调。

        返回 (是否可信, 标准化字段)，标准化字段至少含
        out_trade_no / trade_no / amount / buyer_id / trade_status。
        任何未通过验签的数据一律返回 False，调用方不得据此改单。
        """

    @abstractmethod
    def query_payment(self, pay_no: str) -> Dict[str, Any]:
        """主动查单（本地收不到异步通知时的兜底）。

        返回 {"found": bool, "paid": bool, "trade_no": str|None, "amount": float|None}。
        """

    def close_payment(self, pay_no: str) -> bool:
        """关闭未支付交易（订单超时取消时调用）。渠道不支持时返回 False。"""
        return False

    # --- 工具 ---

    @staticmethod
    def fmt_amount(amount) -> str:
        """金额格式化为网关要求的两位小数字符串。"""
        return f"{float(amount):.2f}"
