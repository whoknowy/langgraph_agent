"""支付宝渠道（沙箱与生产共用同一实现，靠 debug 切换网关）。

四个容易踩坑、这里已处理的点：
1. **密钥格式**：密钥工具常导出单行裸 Base64（无 BEGIN/END 标记），
   而 pycryptodome 的 RSA.importKey 只认 PEM，直接喂会报
   "RSA key format is not supported"。_read_pem() 负责补齐头尾。
2. **沙箱网关**：新版沙箱是 openapi-sandbox.dl.alipaydev.com，
   SDK 在 debug=True 时已自动指向它，无需手动拼 URL。
3. **回调不可信**：验签只是第一道，还必须校验 app_id 与金额，
   否则攻击者可用自己的商户号伪造一笔"已支付"。
4. **待验签原文必须自己拼（异步通知恒验签失败的根因）**：
   支付宝网关拼待签名字符串的规则是「剔除 sign / sign_type，
   并**剔除值为空的参数**」，而 python-alipay-sdk 的 AliPay.verify()
   只弹 sign_type，会把空值参数也拼进原文，拼出的原文可能与支付宝不一致。
   因此这里按网关规则自行拼原文验签，SDK 的 verify 仅作兜底，
   详见 build_sign_content() / verify_notify()。
5. **字符集跟通知里的 charset 走（本项目实测踩到的坑）**：
   支付宝异步通知实际会声明 `charset=GBK`，中文参数（如 subject）按 GBK 编码，
   签名也是把原文转成 **GBK 字节**后做的。若按 UTF-8 解码参数、或按 UTF-8
   把原文转字节，原文就对不上 → 验签必然失败。
   （同步回跳之所以能过，是因为它的参数全是 ASCII，GBK 与 UTF-8 字节相同。）
   所以 parse_notify_body() 与 _verify_signature() 都必须带 charset，
   并且必须用**原始报文**解析——框架（Flask/Werkzeug）按 UTF-8 解 GBK 中文会变乱码。
6. **退款是同步接口，但要区分「失败」与「结果未知」**：`alipay.trade.refund`
   返回 code=10000 才算受理成功（fund_change=N 表示重复请求命中幂等）；
   网络超时/响应验签失败属于**结果未知**，既不能当失败也不能盲目重试，
   必须用 alipay.trade.fastpay.refund.query + 同一个 out_request_no 对账，
   详见 refund() / query_refund()。

密钥来源：环境变量 ALIPAY_PRIVATE_KEY / ALIPAY_PUBLIC_KEY 优先，
未设置时回退到 config 指定的密钥文件（见 _load_key）。

SDK 与网络调用均为延迟加载/延迟构造：未配置凭据时本模块可正常导入，
只在真正发起支付时才报错，不影响 mock 通道与其他功能。
"""

import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

from .base import (MODE_REDIRECT, PROVIDER_ALIPAY, PROVIDER_ALIPAY_SANDBOX,
                   REFUND_FAILED, REFUND_OK, REFUND_PENDING, PaymentProvider)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_PRIV_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
_PRIV_FOOTER = "-----END RSA PRIVATE KEY-----"
_PUB_HEADER = "-----BEGIN PUBLIC KEY-----"
_PUB_FOOTER = "-----END PUBLIC KEY-----"

# 交易终态：只有这两种才认为买家真的付了钱
_PAID_STATUSES = ("TRADE_SUCCESS", "TRADE_FINISHED")

# 不参与签名的参数（网关规则第一条）
_SIGN_SKIP = ("sign", "sign_type")

# 支持的签名算法（网关只认这两个）
_SIGN_TYPES = ("RSA", "RSA2")

# GBK 系编码的别名，通知里的 charset 可能是其中任一个
_GBK_ALIASES = ("gbk", "gb2312", "gb18030", "cp936")

# 通知实际用的字符集（实测沙箱为 GBK），探测不到或探测错时逐个回退尝试。
# 每多试一个组合只多一次 RSA 验签，且必须验通真签名，不影响安全。
_CHARSET_FALLBACKS = ("utf-8", "gbk")

# 退款原因长度上限（支付宝 refund_reason 要求不超过 256 字符，留余量）
_REFUND_REASON_MAX = 200

# 退款失败 sub_code 分类：决定「能不能原样重试」。
# - 可重试：渠道侧临时问题（余额不足、系统繁忙），过一会儿用**同一个** out_request_no 再试；
# - 不可重试：语义性失败（交易不存在、超退、状态不允许），重试也不会成功。
_REFUND_RETRYABLE_SUB = (
    "ACQ.SELLER_BALANCE_NOT_ENOUGH",   # 商户可用余额不足
    "ACQ.SYSTEM_ERROR",
    "SYSTEM_ERROR",
    "ACQ.PAYMENT_FAIL",
    "ACQ.TRADE_SETTLE_ERROR",
)
_REFUND_NOT_FOUND_SUB = ("ACQ.TRADE_NOT_EXIST",)   # 支付宝侧无此交易（如本地虚拟支付）

# 退款查询结果里的终态：REFUND_SUCCESS=退款成功，其余（如 REFUND_CLOSED）视为未成功
_REFUND_QUERY_SUCCESS = "REFUND_SUCCESS"


def normalize_charset(value: str) -> str:
    """把通知声明的 charset 归一成 Python 认识、且大小写不敏感的编解码器名。"""
    cs = (value or "").strip().lower().replace("_", "-")
    if not cs:
        return "utf-8"
    if cs == "utf8":
        return "utf-8"
    if cs in _GBK_ALIASES:
        return "gbk"
    return cs


def charset_candidates(*names: str) -> List[str]:
    """按给定顺序去重出一组候选字符集（声明值优先，其后是兜底）。"""
    out: List[str] = []
    for name in names:
        cs = normalize_charset(name)
        if cs and cs not in out:
            out.append(cs)
    for cs in _CHARSET_FALLBACKS:
        if cs not in out:
            out.append(cs)
    return out


def charset_of(raw: Any) -> str:
    """读出回调声明用的字符集（charset 参数本身是 ASCII，可先按 UTF-8 安全探一遍）。"""
    if not raw:
        return "utf-8"
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    for key, value in parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True):
        if key == "charset":
            return normalize_charset(value)
    return "utf-8"


def build_sign_content(params: Dict[str, Any], *, drop_blank: bool = True) -> str:
    """按支付宝网关规则拼「待验签字符串」。

    网关规则（缺一不可，见开放平台《自行实现签名》）：
    1. 剔除 sign 与 sign_type；
    2. **剔除值为空的参数**（"没有值的参数无需传递，也无需包含到待签名数据中"）；
    3. 键名按 ASCII 码升序排序；
    4. 以 `键=值` 用 `&` 连接，值取 **url_decode 之后的原生值**（不再二次 URL 编码）。

    注意：这里产出的是**字符串**；转成字节时要按通知声明的 charset 编码，
    见 _verify_signature(content, ..., charset)。

    drop_blank=False 用于兼容「支付宝确实对含空值原文签名」的极端情况（兜底用）。
    """
    items = []
    for key, value in (params or {}).items():
        key = str(key)
        if key in _SIGN_SKIP or value is None:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        text = value if isinstance(value, str) else str(value)
        if drop_blank and text == "":
            continue
        items.append((key, text))
    items.sort(key=lambda kv: kv[0])
    return "&".join(f"{k}={v}" for k, v in items)


def parse_notify_body(raw: Any, charset: str = None) -> Dict[str, str]:
    """把回调的原始报文（bytes / str）解析成参数，等价于官方验签第二步的 url_decode。

    为什么要自己解、而不是直接用框架解析好的 dict：
    - **字符集**：通知里声明的 charset 决定参数值的编码。实测沙箱会声明
      `charset=GBK` 并按 GBK 编码中文参数（如 subject），而 Flask/Werkzeug
      按 UTF-8 解析会得到乱码，验签必然失败；
    - 报文按该字符集编码，框架可能用错字符集、合并重复键或按自身规则转义；
    - keep_blank_values=True 才能保住空值参数，让上层按网关规则决定是否剔除。

    关键点：charset 必须交给 parse_qsl 做百分号解码（encoding=参数），
    不能先按某个字符集把整串解码完再解析，否则 %XX 仍会按默认 UTF-8 还原。
    """
    if not raw:
        return {}
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    cs = normalize_charset(charset or charset_of(raw))
    text = raw.decode(cs, "replace")
    return dict(parse_qsl(text, keep_blank_values=True, encoding=cs, errors="replace"))


def _normalize_pem(raw: str, header: str, footer: str) -> str:
    """规范化成 PEM：已是 PEM 则原样返回，裸 Base64 则补头尾。"""
    raw = (raw or "").strip()
    if "BEGIN" in raw:
        return raw
    body = "".join(raw.split())
    return f"{header}\n{body}\n{footer}\n"


def _load_key(path_value: str, env_var: str, header: str, footer: str) -> str:
    """读取密钥：环境变量优先，回退到密钥文件。

    优先环境变量是生产环境的最佳实践——密钥不必落盘，可交由容器/KMS 注入；
    本地开发用文件更方便，两者兼容。文件不存在或格式不对会给出明确提示。
    """
    from_env = os.getenv(env_var, "").strip()
    if from_env:
        return _normalize_pem(from_env, header, footer)
    path = Path(path_value)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        raise RuntimeError(
            f"密钥文件不存在：{path}（可改用环境变量 {env_var} 直接提供密钥内容）")
    return _normalize_pem(path.read_text(encoding="utf-8"), header, footer)


class AlipayProvider(PaymentProvider):
    """支付宝渠道。

    支付场景（scene）决定下单接口：
    - "page"：电脑网站支付 alipay.trade.page.pay，PC 浏览器收银台；
    - "wap"：手机网站支付 alipay.trade.wap.pay，手机浏览器唤起支付宝 App，
      推荐给移动端使用——沙箱对 wap 的支持比 page 完整，
      且不走 PC 收银台那套引用内网资源（stable.alipay.net）的页面。

    两者的验签、查单、关单完全一样，只是下单接口与 product_code 不同。
    """

    name = PROVIDER_ALIPAY_SANDBOX
    label = "支付宝沙箱"
    mode = MODE_REDIRECT

    # 场景 → SDK 方法名（SDK 内部已带好各自 product_code）
    _SCENES = {"page": "api_alipay_trade_page_pay", "wap": "api_alipay_trade_wap_pay"}

    def __init__(self, *, app_id: str, private_key_path: str, public_key_path: str,
                 sign_type: str = "RSA2", debug: bool = True, timeout: int = 15,
                 scene: str = "page"):
        self.app_id = (app_id or "").strip()
        self.private_key_path = private_key_path
        self.public_key_path = public_key_path
        self.sign_type = sign_type or "RSA2"
        self.debug = bool(debug)
        self.timeout = timeout
        self.scene = scene if scene in self._SCENES else "page"
        self.name = PROVIDER_ALIPAY_SANDBOX if self.debug else PROVIDER_ALIPAY
        base = "支付宝沙箱" if self.debug else "支付宝"
        self.label = base if self.scene == "page" else f"{base}（手机网站支付）"
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
            app_private_key_string=_load_key(
                self.private_key_path, "ALIPAY_PRIVATE_KEY", _PRIV_HEADER, _PRIV_FOOTER),
            alipay_public_key_string=_load_key(
                self.public_key_path, "ALIPAY_PUBLIC_KEY", _PUB_HEADER, _PUB_FOOTER),
            sign_type=self.sign_type,
            debug=self.debug,
            config=AliPayConfig(timeout=self.timeout),
        )
        return self._client

    def pay_url_of(self, order_string: str) -> str:
        return f"{self._ensure_client()._gateway}?{order_string}"

    # ------------------------------------------------------------ 接口实现

    def _build_order(self, pay_no: str, subject: str, amount: float,
                     return_url: str, notify_url: str):
        """下单，并同时给出两种可用形态：拼接好的 URL 与拆好的表单字段。"""
        client = self._ensure_client()
        method = getattr(client, self._SCENES[self.scene])
        order_string = method(
            out_trade_no=pay_no,
            total_amount=self.fmt_amount(amount),
            subject=subject,
            return_url=return_url,
            notify_url=notify_url,
        )
        fields = dict(parse_qsl(order_string, keep_blank_values=True))
        return client._gateway, order_string, fields

    def create_payment(self, *, pay_no: str, subject: str, amount: float,
                       return_url: str, notify_url: str) -> Dict[str, Any]:
        try:
            gateway, order_string, _ = self._build_order(
                pay_no, subject, amount, return_url, notify_url)
            return {
                "ok": True,
                "mode": MODE_REDIRECT,
                "pay_url": f"{gateway}?{order_string}",
                "pay_no": pay_no,
                "amount": self.fmt_amount(amount),
                "message": "请在支付宝收银台完成付款",
            }
        except Exception as e:
            return {"ok": False, "error": f"支付宝下单失败：{e}"}

    def build_checkout_form(self, *, pay_no: str, subject: str, amount: float,
                            return_url: str, notify_url: str):
        try:
            gateway, _, fields = self._build_order(
                pay_no, subject, amount, return_url, notify_url)
            return {"gateway": gateway, "fields": fields}
        except Exception:
            return None

    def _verify_signature(self, content: str, signature: str, sign_type: str,
                          charset: str = "utf-8") -> bool:
        """用支付宝公钥做 RSA 验签：RSA2=SHA256withRSA，RSA=SHA1withRSA。

        content 是按网关规则拼好的待验签原文；signature 是 base64 字符串。
        ⚠️ content 必须按**通知声明的 charset** 转字节：支付宝是把原文按该字符集
        编码后再签的，中文参数（subject 等）用错字符集必然验不过。
        任何异常（解码失败、密钥不可用等）都按「验签不通过」处理，绝不抛给调用方。
        """
        try:
            from Cryptodome.Hash import SHA, SHA256
            from Cryptodome.Signature import PKCS1_v1_5
            key = self._ensure_client().alipay_public_key
            digest = SHA.new() if sign_type == "RSA" else SHA256.new()
            digest.update(content.encode(normalize_charset(charset), "replace"))
            return bool(PKCS1_v1_5.new(key).verify(digest, base64.b64decode(signature)))
        except Exception:
            return False

    def verify_notify(self, data: Dict[str, Any] = None, *,
                      raw_body: Any = None) -> Tuple[bool, Dict[str, Any]]:
        """校验异步通知（或同步回跳参数）。

        返回 (是否可信, 标准化字段)。任何未通过验签的数据一律返回 False。

        ⚠️ 不要直接把通知参数丢给 SDK 的 verify()：
        - 它不剔除空值参数，而网关签名时剔除了；
        - 它固定按 UTF-8 把原文转字节，而通知实测声明 `charset=GBK`。
        两者任一不符，原文就对不上 → 恒失败（见模块开头第 4、5 条）。
        这里按网关规则自己拼原文、按声明的字符集转字节，SDK 仅作兜底。

        raw_body：回调的原始报文（bytes）。**必须传**，否则 GBK 通知里的中文
        会被框架按 UTF-8 解成乱码，无从验签。data 作为兜底候选一并尝试。
        """
        declared = charset_of(raw_body)

        # 候选 = (来源标签, 参数, 转字节用的字符集)
        candidates: List[Tuple[str, Dict[str, Any], str]] = []
        for cs in charset_candidates(declared):
            params = parse_notify_body(raw_body, cs)
            if params:
                candidates.append((f"原始报文/{cs}", params, cs))
        if data:
            # 框架解析结果：字符集已被框架拍板（通常是 utf-8）。
            # 通知声明 GBK 时这里的中文已损坏，仅作兜底。
            for cs in charset_candidates(declared):
                candidates.append((f"框架解析/{cs}", dict(data), cs))
        if not candidates:
            return False, {"error": "回调缺少参数"}

        signature = next((p.get("sign") for _, p, _ in candidates if p.get("sign")), "")
        if not signature:
            return False, {"error": "回调缺少 sign 参数"}

        # 签名算法优先取通知声明的，缺省用本渠道配置；只接受 RSA / RSA2
        declared_type = next((p.get("sign_type") for _, p, _ in candidates
                              if p.get("sign_type")), "")
        sign_type = str(declared_type or self.sign_type).upper()
        if sign_type not in _SIGN_TYPES:
            return False, {"error": f"不支持的签名算法：{declared_type}"}

        # 第一轮：按支付宝网关规则（剔除空值）验签
        hit = None
        for label, params, cs in candidates:
            sig = params.get("sign") or signature
            if self._verify_signature(build_sign_content(params), sig, sign_type, cs):
                hit = (label, params, cs, "gateway")
                break

        # 第二轮（兜底）：少数场景支付宝确实对含空值的原文签名，
        # 或 SDK 的拼装规则与网关一致，交给 SDK 再验一次。
        # 两条路都要求同一个支付宝公钥下的合法 RSA 签名，安全性不受影响。
        if hit is None:
            for label, params, _ in candidates:
                try:
                    probe = dict(params)
                    sig = probe.pop("sign", None)
                    if sig and self._ensure_client().verify(probe, sig):
                        hit = (label, params, "", "sdk")
                        break
                except Exception:
                    continue

        if hit is None:
            diag = candidates[0]
            blank = sorted(k for k, v in diag[1].items()
                           if v == "" and k not in _SIGN_SKIP)
            return False, {
                "error": "签名校验未通过",
                "charset": declared,
                "blank_params": blank,
                "content_head": build_sign_content(diag[1])[:200],
            }

        source, params, charset, how = hit
        if how != "gateway":
            print(f"⚠️ [支付] 异步通知经 SDK 兜底验签通过（参数来源：{source}）")

        # 验签通过只说明"是支付宝发的"，还需确认"是发给我的"
        if self.app_id and params.get("app_id") and params.get("app_id") != self.app_id:
            return False, {"error": f"app_id 不匹配：{params.get('app_id')}"}

        return True, {
            "out_trade_no": params.get("out_trade_no"),
            "trade_no": params.get("trade_no"),
            "trade_status": params.get("trade_status"),
            "amount": params.get("total_amount"),
            "buyer_id": params.get("buyer_id") or params.get("buyer_logon_id"),
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

    # ------------------------------------------------------------ 退款

    def refund(self, *, pay_no: str, amount: float, out_request_no: str,
               reason: str = "", trade_no: str = None) -> Dict[str, Any]:
        """调用 `alipay.trade.refund` 发起退款（**同步接口**，立即拿到结果）。

        参数怎么给：
        - 交易标识：优先 `trade_no`（支付宝交易号）；没有则用 `out_trade_no`（= 我们的 pay_no）；
        - `refund_amount`：本次退款额，少付整单就是部分退款；
        - `out_request_no`：退款请求号。**部分退款必传**，且同一交易下多次部分退款必须唯一。
          支付宝对「同一 out_trade_no + out_request_no」的重复请求只退一次并返回首次结果，
          这是渠道侧的重复退款防护——所以调用方必须传稳定值（本地幂等键）。
        - `refund_reason`：退款原因，超长会截断（网关上限 256 字符）。

        三种结果要分清，业务层的处理方式完全不同：
        - 成功：`ok=True`；`fund_change=N` 说明是重复请求命中了幂等，没有二次扣钱；
        - 明确失败：`ok=False` + `retryable=False`（超退/交易不存在等，重试无意义）；
        - 结果未知：`ok=False` + `unknown=True`（网络超时、响应验签失败），
          **不能当失败处理，也不能直接重试**——必须先用 `query_refund()` 对账。
        """
        if not out_request_no:
            return {"ok": False, "channel_status": REFUND_FAILED, "duplicate": False,
                    "retryable": False, "unknown": False,
                    "error": "缺少退款请求号 out_request_no（部分退款必须唯一）"}

        try:
            client = self._ensure_client()
        except Exception as e:
            # 密钥缺失/SDK 未装：属于配置问题，不是"结果未知"，不该重试
            return {"ok": False, "channel_status": REFUND_FAILED, "duplicate": False,
                    "retryable": False, "unknown": False,
                    "error": f"支付宝客户端不可用：{e}"}

        biz = {
            "refund_amount": self.fmt_amount(amount),
            "out_request_no": str(out_request_no),
        }
        text = (reason or "").strip()
        if text:
            biz["refund_reason"] = text[:_REFUND_REASON_MAX]
        if trade_no:
            biz["trade_no"] = trade_no
        else:
            biz["out_trade_no"] = pay_no

        try:
            res = client.api_alipay_trade_refund(**biz)
        except Exception as e:
            # 网络超时/连接中断：请求可能已经到达支付宝并退了钱，结果未知
            return {"ok": False, "channel_status": REFUND_PENDING, "duplicate": False,
                    "retryable": True, "unknown": True,
                    "error": f"退款请求结果未知（{type(e).__name__}: {e}）；"
                             f"请用退款请求号 {out_request_no} 查询确认后再重试"}

        if not isinstance(res, dict):
            # 响应验签失败时 SDK 返回 None，同样属于结果未知
            return {"ok": False, "channel_status": REFUND_PENDING, "duplicate": False,
                    "retryable": True, "unknown": True,
                    "error": "退款响应验签失败或格式异常（结果未知，请对账确认）"}

        code = str(res.get("code") or "")
        if code != "10000":
            sub_code = str(res.get("sub_code") or "")
            retryable = sub_code in _REFUND_RETRYABLE_SUB
            if sub_code in _REFUND_NOT_FOUND_SUB:
                msg = (f"支付宝侧查无此交易（{biz.get('trade_no') or biz.get('out_trade_no')}），"
                       f"该笔支付可能未真实经过支付宝，无法原路退回")
            else:
                msg = res.get("sub_msg") or res.get("msg") or "退款被支付宝拒绝"
            return {"ok": False, "channel_status": REFUND_FAILED, "duplicate": False,
                    "retryable": retryable, "unknown": False,
                    "code": code, "sub_code": sub_code, "error": msg, "raw": res}

        # code=10000：受理成功。fund_change=Y 表示本次确实发生了资金变动，
        # N 表示幂等命中（此前已用同一 out_request_no 退过），钱不会退第二次。
        fund_change = str(res.get("fund_change") or "").upper()
        return {
            "ok": True,
            "channel_status": REFUND_OK,
            "duplicate": fund_change == "N",
            "retryable": False,
            "unknown": False,
            "fund_change": fund_change,
            "refund_fee": res.get("refund_fee"),
            "channel_refund_no": res.get("trade_no") or "",
            "gmt_refund_pay": res.get("gmt_refund_pay") or "",
            "code": code,
            "raw": res,
        }

    def query_refund(self, *, pay_no: str, out_request_no: str,
                     trade_no: str = None) -> Dict[str, Any]:
        """调用 `alipay.trade.fastpay.refund.query` 查退款结果（对账兜底）。

        什么时候用：`refund()` 返回「处理中」时，隔一会儿拿**同一个** out_request_no
        查一次——`refunded=True` 说明钱已经退了，不必再发起（再发起也是幂等命中）；
        `queried=True` 但 `refunded=False` 说明渠道侧没有这笔退款的成功记录，
        可以安全用同一个请求号重试。

        ⚠️ 【实测坑】这个接口"命中"和"未命中"**都返回 code=10000**：
        成功退款时会带 `refund_status=REFUND_SUCCESS`（还有 refund_amount / out_request_no），
        而查不到该退款请求号时响应只有 `{"code":"10000","msg":"Success"}`。
        所以判断依据是「有没有退款记录字段」，不是 code（见 found 的赋值）。
        """
        try:
            client = self._ensure_client()
            kwargs = {"trade_no": trade_no} if trade_no else {"out_trade_no": pay_no}
            res = client.api_alipay_trade_fastpay_refund_query(
                out_request_no=str(out_request_no), **kwargs)
        except Exception as e:
            # 网络/配置问题：渠道没给出可用回答（queried=False），不能据此认为没退款
            return {"queried": False, "found": False, "refunded": False,
                    "error": f"退款查询失败（{type(e).__name__}: {e}）"}

        if not isinstance(res, dict) or str(res.get("code") or "") != "10000":
            # 渠道明确回答了：多为「该退款请求号不存在」→ 说明请求没到支付宝，可安全重试
            detail = (res or {}).get("sub_msg") or (res or {}).get("msg") or "查询无结果"
            return {"queried": True, "found": False, "refunded": False,
                    "code": (res or {}).get("code"),
                    "sub_code": (res or {}).get("sub_code"),
                    "error": detail, "raw": res}

        status = str(res.get("refund_status") or "")
        # 【实测】退款查询命中与未命中都返回 code=10000：
        #   - 退款成功：多出 refund_status=REFUND_SUCCESS / refund_amount / out_request_no；
        #   - 无此退款请求号：**只有** {"code":"10000","msg":"Success"} 两个字段。
        # 所以 found 不能只看 code，必须看有没有退款记录字段——
        # 否则会把「没退过」误判成「退过」，业务侧就不敢重试了。
        has_record = bool(status or res.get("refund_amount") or res.get("out_request_no"))
        return {
            "queried": True,
            "found": has_record,
            "refunded": status == _REFUND_QUERY_SUCCESS,
            "refund_status": status or None,
            "refund_amount": res.get("refund_amount"),
            "out_request_no": res.get("out_request_no") or str(out_request_no),
            "channel_refund_no": res.get("trade_no") or "",
            "gmt_refund_pay": res.get("gmt_refund_pay") or "",
            "gmt_refund": res.get("gmt_refund") or "",
            "raw": res,
        }

