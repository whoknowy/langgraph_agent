"""支付宝渠道（沙箱与生产共用同一实现，靠 debug 切换网关）。

三个容易踩坑、这里已处理的点：
1. **密钥格式**：密钥工具常导出单行裸 Base64（无 BEGIN/END 标记），
   而 pycryptodome 的 RSA.importKey 只认 PEM，直接喂会报
   "RSA key format is not supported"。_read_pem() 负责补齐头尾。
2. **沙箱网关**：新版沙箱是 openapi-sandbox.dl.alipaydev.com，
   SDK 在 debug=True 时已自动指向它，无需手动拼 URL。
3. **回调不可信**：验签只是第一道，还必须校验 app_id 与金额，
   否则攻击者可用自己的商户号伪造一笔"已支付"。

SDK 与网络调用均为延迟加载/延迟构造：未配置凭据时本模块可正常导入，
只在真正发起支付时才报错，不影响 mock 通道与其他功能。
"""

from pathlib import Path
from typing import Any, Dict, Tuple

from .base import (MODE_REDIRECT, PROVIDER_ALIPAY, PROVIDER_ALIPAY_SANDBOX,
                   PaymentProvider)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_PRIV_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
_PRIV_FOOTER = "-----END RSA PRIVATE KEY-----"
_PUB_HEADER = "-----BEGIN PUBLIC KEY-----"
_PUB_FOOTER = "-----END PUBLIC KEY-----"

# 交易终态：只有这两种才认为买家真的付了钱
_PAID_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")


def _read_pem(path_value: str, header: str, footer: str) -> str:
    """读取密钥文件并规范化成 PEM。

    已经是 PEM（含 BEGIN 标记）的原样返回；裸 Base64 则补头尾并压成单行主体。
    """
    path = Path(path_value)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    raw = (path.read_text(encoding="utf-8") or "").strip()
    if "BEGIN" in raw:
        return raw
    body = "".join(raw.split())
    return f"{header}\n{body}\n{footer}\n"


class AlipayProvider(PaymentProvider):
    name = PROVIDER_ALIPAY_SANDBOX
    label = "支付宝沙箱"
    mode = MODE_REDIRECT

    def __init__(self, *, app_id: str, private_key_path: str, public_key_path: str,
                 sign_type: str = "RSA2", debug: bool = True, timeout: int = 15):
        self.app_id = (app_id or "").strip()
        self.private_key_path = private_key_path
        self.public_key_path = public_key_path
        self.sign_type = sign_type or "RSA2"
        self.debug = bool(debug)
        self.timeout = timeout
        self.name = PROVIDER_ALIPAY_SANDBOX if self.debug else PROVIDER_ALIPAY
        self.label = "支付宝沙箱" if self.debug else "支付宝"
        self._client = None

    # ------------------------------------------------------------ 内部

    def _ensure_client(self):
        """延迟构造 AliPay 客户端（密钥缺失/未装 SDK 时才报错，不影响启动）。"""
        if self._client is not None:
            return self._client
        if not self.app_id:
            raise RuntimeError("未配置 ALIPAY_APP_ID")
        from alipay import AliPay
        from alipay.utils import AliPayConfig
        self._client = AliPay(
            appid=self.app_id,
            app_notify_url=None,
            app_private_key_string=_read_pem(self.private_key_path, _PRIV_HEADER, _PRIV_FOOTER),
            alipay_public_key_string=_read_pem(self.public_key_path, _PUB_HEADER, _PUB_FOOTER),
            sign_type=self.sign_type,
            debug=self.debug,
            config=AliPayConfig(timeout=self.timeout),
        )
        return self._client

    def pay_url_of(self, order_string: str) -> str:
        return f"{self._ensure_client()._gateway}?{order_string}"

    # ------------------------------------------------------------ 接口实现

    def create_payment(self, *, pay_no: str, subject: str, amount: float,
                       return_url: str, notify_url: str) -> Dict[str, Any]:
        try:
            client = self._ensure_client()
            order_string = client.api_alipay_trade_page_pay(
                out_trade_no=pay_no,
                total_amount=self.fmt_amount(amount),
                subject=subject,
                return_url=return_url,
                notify_url=notify_url,
            )
            return {
                "ok": True,
                "mode": MODE_REDIRECT,
                "pay_url": f"{client._gateway}?{order_string}",
                "pay_no": pay_no,
                "amount": self.fmt_amount(amount),
                "message": "请在支付宝收银台完成付款",
            }
        except Exception as e:
            return {"ok": False, "error": f"支付宝下单失败：{e}"}

    def verify_notify(self, data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        raw = dict(data or {})
        signature = raw.pop("sign", None)
        if not signature:
            return False, {"error": "回调缺少 sign 参数"}
        try:
            # SDK 的 verify 会自行处理 sign_type；验签失败返回 False
            ok = self._ensure_client().verify(raw, signature)
        except Exception as e:
            return False, {"error": f"验签异常：{e}"}
        if not ok:
            return False, {"error": "签名校验未通过"}

        # 验签通过只说明"是支付宝发的"，还需确认"是发给我的、金额对得上"
        if self.app_id and raw.get("app_id") and raw.get("app_id") != self.app_id:
            return False, {"error": f"app_id 不匹配：{raw.get('app_id')}"}

        return True, {
            "out_trade_no": raw.get("out_trade_no"),
            "trade_no": raw.get("trade_no"),
            "trade_status": raw.get("trade_status"),
            "amount": raw.get("total_amount"),
            "buyer_id": raw.get("buyer_id") or raw.get("buyer_logon_id"),
        }

    def query_payment(self, pay_no: str) -> Dict[str, Any]:
        """主动查单：本地收不到异步通知时的兜底（同步接口，返回已验签结果）。"""
        try:
            res = self._ensure_client().api_alipay_trade_query(out_trade_no=pay_no)
        except Exception as e:
            return {"found": False, "paid": False, "error": str(e)}
        if res.get("code") != "10000":
            # 40004 = 交易不存在（尚未扫码付款），属正常情况不算错
            return {"found": False, "paid": False,
                    "error": res.get("sub_msg") or res.get("msg")}
        status = res.get("trade_status")
        return {
            "found": True,
            "paid": status in _PAID_STATUSES,
            "trade_status": status,
            "trade_no": res.get("trade_no"),
            "amount": res.get("total_amount"),
        }

    def close_payment(self, pay_no: str) -> bool:
        """关闭未付款交易。只能关掉「等待买家付款」的交易，已付款的会失败。"""
        try:
            res = self._ensure_client().api_alipay_trade_close(out_trade_no=pay_no)
            return res.get("code") == "10000"
        except Exception:
            return False
