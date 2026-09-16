"""支付渠道抽象层。

所有渠道（模拟 / 支付宝沙箱 / 支付宝生产 / 微信）实现同一组接口，
业务编排层只依赖本抽象，切换渠道不改动业务代码。

职责边界：
- provider 只负责「与外部支付网关打交道」（签名、拼链接、验签、查单、退款）；
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

# 退款结果状态（落 refunds.channel_status）
REFUND_OK = "成功"                  # 渠道明确退款成功
REFUND_FAILED = "失败"              # 渠道明确拒绝（金额超限/交易不存在/余额不足…）
REFUND_PENDING = "处理中"           # 结果未知（网络超时/响应不可解析），必须对账确认
REFUND_UNSUPPORTED = "渠道不支持"    # 渠道未实现退款
REFUND_NOT_NEEDED = "无需渠道退款"   # 订单没有已支付的渠道流水（种子订单/线下支付）

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
    def verify_notify(self, data: Dict[str, Any] = None, *,
                      raw_body: Any = None) -> Tuple[bool, Dict[str, Any]]:
        """校验异步回调。

        返回 (是否可信, 标准化字段)，标准化字段至少含
        out_trade_no / trade_no / amount / buyer_id / trade_status。
        任何未通过验签的数据一律返回 False，调用方不得据此改单。

        data：框架解析好的参数 dict（如 Flask 的 request.form.to_dict()）；
        raw_body：回调的原始报文 bytes，渠道可优先据此自行解码，
        避免框架解析带来的字符集/转义差异。两者都给时由渠道决定用哪个。
        """

    @abstractmethod
    def query_payment(self, pay_no: str) -> Dict[str, Any]:
        """主动查单（本地收不到异步通知时的兜底）。

        返回 {"found": bool, "paid": bool, "trade_no": str|None, "amount": float|None}。
        """

    def close_payment(self, pay_no: str) -> bool:
        """关闭未支付交易（订单超时取消时调用）。渠道不支持时返回 False。"""
        return False

    def refund(self, *, pay_no: str, amount: float, out_request_no: str,
               reason: str = "", trade_no: str = None) -> Dict[str, Any]:
        """发起退款（同步接口）。

        - `pay_no`：我们的支付流水号，即下单时用的 `out_trade_no`；
        - `amount`：本次退款金额（**支持部分退款**，可小于原支付金额）；
        - `out_request_no`：退款请求号，**渠道侧的幂等键**。部分退款必传且必须唯一；
          同一个 交易号 + out_request_no 重复请求，渠道只退一次并返回首次结果
          ——「重复退款」这一层防护靠的就是它，业务层必须传稳定值（见调用方）；
        - `reason`：退款原因（部分渠道有长度上限，本层负责截断）。

        返回统一结构（**不得抛异常**，任何异常都转成字典）：
        ```
        {"ok": bool,
         "channel_status": "成功"|"失败"|"处理中"|"渠道不支持",
         "duplicate": bool,        # 渠道判定为重复请求（未再次扣钱）
         "retryable": bool,        # 失败但值得原样重试（如渠道余额不足）
         "unknown": bool,          # 结果未知（超时），须用 query_refund 对账
         "refund_fee": str|None,   # 渠道实退金额
         "channel_refund_no": str|None,
         "error": str|None, "code": str|None, "sub_code": str|None,
         "raw": Any}
        ```
        本层不碰本地库：退款额度占用与流水落账由 services/payment_service.py 负责。
        """
        return {"ok": False, "channel_status": REFUND_UNSUPPORTED, "duplicate": False,
                "retryable": False, "unknown": False,
                "error": f"{self.label} 未实现退款能力"}

    def query_refund(self, *, pay_no: str, out_request_no: str,
                     trade_no: str = None) -> Dict[str, Any]:
        """查询某笔退款在渠道侧的结果（对账兜底）。

        用途：`refund()` 返回「处理中/结果未知」时（网络超时等），
        隔一段时间用同一个 out_request_no 查一次，确认钱到底退没退。

        返回 {"queried", "found", "refunded", "refund_amount", "refund_status",
        "raw", "error"}：
        - `queried=True` 表示**渠道明确回答了**（哪怕是"查无此退款"）——
          此时 `refunded=False` 意味着这笔退款没有成功，可以安全重试；
        - `queried=False` 表示渠道没给出可用回答（网络故障/接口不支持），
          此时**不能**推断退款没发生，应稍后重查或转人工；
        - `found` 表示渠道侧**有没有这笔退款的记录**（注意：支付宝查不到时也返回
          成功码，别只看 code，见 alipay_provider.query_refund 的实测说明）。
        """
        return {"queried": False, "found": False, "refunded": False,
                "unsupported": True, "error": f"{self.label} 未实现退款查询"}


    def build_checkout_form(self, *, pay_no: str, subject: str, amount: float,
                            return_url: str, notify_url: str):
        """可选能力：构造自动提交到网关的表单字段。

        返回 {"gateway": "https://...", "fields": {k: v}}；渠道不支持则返回 None，
        调用方回退到 create_payment 给出的 pay_url 跳转。

        电脑网站支付的官方接入方式就是表单 POST。相比直接 GET 跳转，表单方式下
        Referer 恒为本站，可规避网关（尤其沙箱）的 Referer 校验拦截。
        """
        return None

    # --- 工具 ---

    @staticmethod
    def fmt_amount(amount) -> str:
        """金额格式化为网关要求的两位小数字符串。"""
        return f"{float(amount):.2f}"
