"""支付编排层：把「渠道能力」与「业务落账」串起来。

职责边界（与 AGENTS.md 一致）：
- 这是唯一同时接触 provider（外部网关）与 repo（本地库）的地方；
- 写库动作全部在本层以代码完成，不经过 LLM；
- 不依赖 Flask request 与 LLM，是单测的主要抓手（0 token）。

退款也在这里编排（refund_payment / query_refund_status）：
`provider.refund()` 只负责跟支付宝打交道，退款额度与流水落账在本层，
业务订单状态由调用方（REST / 管理端审批）在**渠道成功之后**推进。
"""

from typing import Any, Dict, Optional

from services import db, payment_repo
from services.payment import get_provider
from services.payment.base import (REFUND_FAILED, REFUND_NOT_NEEDED, REFUND_OK)

STATUS_PAID = "支付成功"
STATUS_CLOSED = "已关闭"
STATUS_PENDING = "待支付"

# 渠道结果未知时落进 refunds.status 的中间态：钱可能已经退了，订单不能动，
# 也不允许再发起新的退款 —— 必须对账后再处理。
# （刻意不叫「退款中」，免得和特殊退票的「退票中」看混）
REFUND_PENDING_ORDER_STATUS = "退款待对账"
# 渠道确认退款成功、但订单状态还没跟上的中间态：同样阻断自动重退，等人工收口。
REFUND_CHANNEL_DONE_STATUS = "渠道已退款"

# 这些退款流水状态说明「钱已经动过了」，同一请求号再调就直接幂等返回、不再发起退款。
# 注意 `退票中`（特殊退票等审批，钱还没退）、`退款失败`（没退成）都不在内，
# 它们必须继续往下走渠道。
SETTLED_LEDGER_STATUSES = ("已退款", REFUND_CHANNEL_DONE_STATUS)


def _load_order(order_no: str) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM orders WHERE order_no = ?",
                           ((order_no or "").strip().upper(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mark_order_paid(order_no: str, channel: str) -> None:
    from datetime import datetime
    conn = db.get_connection()
    try:
        conn.execute("UPDATE orders SET pay_channel = ?, paid_at = ? WHERE order_no = ?",
                     (channel, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_no))
        conn.commit()
    finally:
        conn.close()


def _notify(member_id: str, title: str, content: str) -> None:
    try:
        from services import notification_repo
        notification_repo.create_notification(member_id, content, title=title)
    except Exception as e:  # 通知失败不能影响支付主流程
        print(f"⚠️ [支付] 站内通知写入失败：{e}")


def start_payment(order_no: str, member_id: str = None, provider_name: str = None,
                  return_url: str = None, notify_url: str = None,
                  subject: str = None) -> Dict[str, Any]:
    """发起支付：校验订单 → 建/复用流水 → 调渠道拿跳转信息。

    返回给前端的 `mode` 决定交互：redirect=跳转外部收银台（需轮询查单），
    direct=站内直接确认（mock）。
    """
    order = _load_order(order_no)
    if not order:
        return {"error": f"订单不存在：{order_no}"}
    denied = _enforce_order_owner(order, "支付")
    if denied:
        return denied

    from services import flight_repo
    # 改签补差价：订单早已是「已出票/已改签」，这时该收的不是票款而是差价。
    # 收谁的款由服务端的改签流水决定 —— 前端不区分，仍走同一个 /api/pay/create。
    pending_change = flight_repo.get_pending_change(order["order_no"])
    if not pending_change and order["status"] != "待支付":
        return {"error": f"订单状态为「{order['status']}」，无需支付"}
    amount = float(pending_change["fare_diff"]) if pending_change else float(order["amount"])

    try:
        provider = get_provider(provider_name)
    except Exception as e:
        return {"error": f"支付渠道不可用：{e}"}

    payment = payment_repo.create_payment(order["order_no"], amount, provider.name)
    if pending_change:
        # 把流水挂到改签单上：回调落账时靠它判断「该改签」而不是「该出票」
        flight_repo.attach_change_payment(pending_change["request_id"], payment["pay_no"])
    result = provider.create_payment(
        pay_no=payment["pay_no"],
        subject=subject or (f"改签差价 {order['order_no']}" if pending_change
                            else f"机票订单 {order['order_no']}"),
        amount=amount,
        return_url=return_url or "",
        notify_url=notify_url or "",
    )
    if not result.get("ok"):
        return {"error": result.get("error") or "支付发起失败"}

    payload = {
        "success": True,
        "provider": provider.name,
        "provider_label": provider.label,
        "mode": result.get("mode") or provider.mode,
        "pay_url": result.get("pay_url"),
        "pay_no": payment["pay_no"],
        "order_no": order["order_no"],
        "amount": amount,
        "message": result.get("message") or "请在收银台完成付款",
    }
    if pending_change:
        payload.update({"purpose": "change_diff",
                        "fare_diff": int(pending_change["fare_diff"]),
                        "change_request_id": pending_change["request_id"]})
    return payload


def settle_payment(pay_no: str = None, out_trade_no: str = None, *,
                   expect_amount=None, trade_no: str = None, buyer_id: str = None,
                   notify_raw: str = None, provider_name: str = None) -> Dict[str, Any]:
    """支付成功落账（幂等）。

    `expect_amount` 来自渠道回调，必须与流水金额一致才允许改单（防篡改）。
    返回 `already=True` 表示重复通知，调用方直接忽略即可。
    """
    payment = (payment_repo.get_payment(pay_no) if pay_no else None) \
        or (payment_repo.get_by_out_trade_no(out_trade_no) if out_trade_no else None)
    if not payment:
        return {"error": f"支付流水不存在：{pay_no or out_trade_no}"}

    pay_no = payment["pay_no"]
    if payment["status"] == STATUS_PAID:
        return {"success": True, "already": True, "pay_no": pay_no,
                "order_no": payment["order_no"], "message": "该支付已处理，忽略重复通知"}
    if payment["status"] == STATUS_CLOSED:
        return {"error": "支付流水已关闭（订单可能已取消），请先重新下单"}

    if expect_amount is not None and not payment_repo.amount_matches(payment, expect_amount):
        return {"error": f"支付金额不符：应付 {payment['amount']}，实付 {expect_amount}"}

    first = payment_repo.mark_paid(pay_no, trade_no=trade_no, buyer_id=buyer_id,
                                   notify_raw=notify_raw)
    if not first:
        return {"success": True, "already": True, "pay_no": pay_no,
                "order_no": payment["order_no"], "message": "该支付已处理，忽略重复通知"}

    from services import flight_repo, security
    # 异步回调没有登录身份，属于系统内部流程：显式声明 system_context，
    # 让仓库层的归属校验按"系统流程"放行（归属已在发起支付时校验过）。
    with security.system_context("支付异步回调落账"):
        # 这笔流水是「改签补差价」还是「票款」？决定收款成功后该改签还是该出票。
        change = flight_repo.get_change_by_pay_no(payment["pay_no"])
        if change:
            result = flight_repo.apply_change(change["order_no"], change["request_id"])
        else:
            result = flight_repo.pay_order(payment["order_no"])
    if result.get("error"):
        # 钱已收但业务推进不了（典型：支付期间订单被超时任务取消 / 订单状态被别人改过）
        action = "改签" if change else "出票"
        order = _load_order(payment["order_no"])
        if order:
            _notify(order["member_id"], "支付异常",
                    f"订单 {payment['order_no']} 已收款 ¥{payment['amount']}，"
                    f"但{action}失败（{result['error']}）。客服将尽快与您联系处理退款。")
        return {"success": True, "settled": False, "pay_no": pay_no,
                "order_no": payment["order_no"],
                "warning": f"已收款但未能{action}：{result['error']}（需人工处理）"}

    if change:
        # 改签差价不改动订单的「首次支付」信息（pay_channel/paid_at 属于原票款）
        return {"success": True, "settled": True, "pay_no": pay_no,
                "order_no": payment["order_no"], "amount": payment["amount"],
                "purpose": "change_diff",
                "message": f"差价支付成功，订单 {payment['order_no']} 改签已生效"}

    _mark_order_paid(payment["order_no"], provider_name or payment["provider"])
    return {"success": True, "settled": True, "pay_no": pay_no,
            "order_no": payment["order_no"], "amount": payment["amount"],
            "message": f"支付成功，订单 {payment['order_no']} 已出票"}


def _enforce_order_owner(order: dict, action: str):
    """支付相关操作的归属校验：从订单反查会员号走受信通道，不信任传参。"""
    from services import security
    return security.enforce_owner(order.get("member_id"), action=action)


def confirm_payment(pay_no: str, member_id: str = None) -> Dict[str, Any]:
    """站内确认支付（direct 模式，目前仅 mock 渠道使用）。"""
    payment = payment_repo.get_payment(pay_no)
    if not payment:
        return {"error": f"支付流水不存在：{pay_no}"}
    order = _load_order(payment["order_no"])
    if not order:
        return {"error": f"订单不存在：{payment['order_no']}"}
    denied = _enforce_order_owner(order, "支付")
    if denied:
        return denied
    return settle_payment(pay_no=pay_no, provider_name=payment["provider"])


def get_payment_status(order_no: str, member_id: str = None) -> Dict[str, Any]:
    """给前端轮询用：订单当前状态 + 最近一笔支付流水状态。"""
    order = _load_order(order_no)
    if not order:
        return {"error": f"订单不存在：{order_no}"}
    denied = _enforce_order_owner(order, "查询支付状态")
    if denied:
        return denied

    from services import flight_repo
    pending = flight_repo.get_pending_change(order["order_no"])
    if pending:
        # 改签补差价期间：订单本身早就是「已出票/已改签」，若沿用下面那句
        # `paid = status != 待支付`，前端一跳收银台就会被判成"已支付、改签成功"。
        # 所以这里的 paid 必须表示「差价已到账、改签已生效」。
        return {
            "order_no": order["order_no"],
            "order_status": order["status"],
            "paid": False,
            "pay_no": pending["pay_no"],
            "pay_status": "待支付",
            "provider": None,
            "amount": int(pending["fare_diff"]),
            "purpose": "change_diff",
            "change_pending": True,
            "change_request_id": pending["request_id"],
            "message": f"改签差价 {pending['fare_diff']} 元待支付，支付成功后改签自动生效",
        }

    payment = payment_repo.get_latest_payment(order_no)
    return {
        "order_no": order["order_no"],
        "order_status": order["status"],
        "paid": order["status"] != "待支付",
        "pay_no": payment["pay_no"] if payment else None,
        "pay_status": payment["status"] if payment else None,
        "provider": payment["provider"] if payment else None,
        "amount": order["amount"],
    }


# ------------------------------------------------------------------ 退款

def _channel_payload(name: str, out_request_no: str, payment: Optional[dict],
                     result: dict = None, error: str = None) -> Dict[str, Any]:
    """把渠道结果整理成可落 refunds 表、也可直接回给前端的结构。"""
    import json
    result = result or {}
    raw = result.get("raw")
    try:
        raw_text = json.dumps(raw, ensure_ascii=False)[:2000] if raw is not None else None
    except (TypeError, ValueError):
        raw_text = str(raw)[:2000] if raw is not None else None
    return {
        "channel": name,
        "out_request_no": out_request_no,
        "pay_no": (payment or {}).get("pay_no"),
        "channel_status": result.get("channel_status") or ("失败" if error else None),
        "channel_error": error or result.get("error"),
        "channel_raw": raw_text,
    }


def refund_payment(*, order_no: str, amount, out_request_no: str,
                   refund_type: str = "voluntary", reason: str = "",
                   fee: float = 0, provider_name: str = None,
                   member_id: str = None) -> Dict[str, Any]:
    """按渠道退款（**同步**），这是唯一接触外部网关的退款入口。

    调用顺序与不变量（改这里之前先读）：
    1. 渠道退款在前、业务状态变更在后。**任何被标记为「已退款」的订单，
       渠道必然已经退款成功**（或明确标记为无需渠道退款）；
    2. 反过来，渠道成功但业务落账失败时必须留痕转人工，不能静默吞掉；
    3. 本地不做「先改状态再退款」——否则渠道失败会留下"订单退了、钱没退"的假账。

    异常处理矩阵：
    | 场景 | 返回 | 订单状态 |
    |---|---|---|
    | 渠道正常退款 | ok=True | 由调用方推进 |
    | 同一请求号重复调用（渠道幂等命中） | ok=True, duplicate=True（不重复占额度） | 由调用方推进 |
    | 无已支付流水（种子/线下单） | ok=True, skipped=True | 由调用方推进 |
    | 金额 <= 0 或超过可退余额 | error（**不调渠道**） | 不变 |
    | 渠道明确拒绝 | error, retryable 视 sub_code | 不变 |
    | 渠道结果未知（超时） | error, unknown=True | 不变，须对账 |
    | 渠道成功但额度占用失败 | success=True + needs_manual | 由调用方推进并告警 |

    `out_request_no` 是渠道侧幂等键，必须传稳定值（本地退款请求号），
    重试要复用同一个值，渠道不会重复退款。
    """
    order = _load_order(order_no)
    if not order:
        return {"error": f"订单不存在：{order_no}"}
    if member_id:
        denied = _enforce_order_owner(order, "退款")
        if denied:
            return denied

    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"error": "退款金额非法"}
    if amount <= 0:
        return {"error": "退款金额必须大于 0"}

    out_request_no = (out_request_no or "").strip()
    if not out_request_no:
        return {"error": "缺少退款请求号（幂等键），拒绝发起渠道退款"}

    from services import flight_repo
    # 先看这个请求号是不是**已经退过**（直接调用本函数时也要幂等；web 层另有更早的拦截）。
    # ⚠️ 只有"钱已经动了"的状态才算退过：`退票中`（特殊退票等审批）和 `退款失败`
    # 都还没退钱，必须继续往下走渠道——否则管理端审批会拿着同一条 request_id
    # 直接返回"已处理"，钱根本没退出去。
    same = flight_repo.get_refund_by_request(out_request_no)
    if same and same.get("status") == REFUND_PENDING_ORDER_STATUS:
        # 上次超时未知：既不能当成功（会误报"已退款"），也不能重试（可能退第二笔）
        return {"error": "这笔退款的结果还没确认（渠道超时），请先对账后再处理，不要重复提交",
                "pending": True, "unsettled": True,
                "channel_status": REFUND_PENDING_ORDER_STATUS,
                "out_request_no": out_request_no,
                "open_request_no": same["request_id"]}
    if same and same.get("status") in SETTLED_LEDGER_STATUSES:
        return {"success": True, "duplicate": True, "idempotent": True,
                "channel_status": same.get("channel_status"),
                "channel": same.get("channel"), "pay_no": same.get("pay_no"),
                "out_request_no": out_request_no, "amount": float(same.get("amount") or 0),
                "refunded_total": None, "partial": False, "needs_manual": False,
                "message": f"该退款请求已受理（{same.get('status')}），未重复发起退款"}

    # 未定论的退款（超时未知/渠道已退但订单没推进）：不许再发起新的退款，
    # 这是「重复退款」最危险的一条路径——钱可能已经在路上，再发起就是退第二笔。
    open_refund = flight_repo.find_open_refund(order["order_no"],
                                               exclude_request_id=out_request_no)
    if open_refund:
        return {"error": f"该订单有一笔退款尚未定论（请求号 {open_refund['request_id']}，"
                         f"状态「{open_refund['status']}」）"
                         + (f"：{open_refund.get('channel_error')}" if open_refund.get("channel_error") else "")
                         + "，请先用退款对账接口确认结果后再处理",
                "unsettled": True, "open_request_no": open_refund["request_id"],
                "open_status": open_refund["status"], "channel_status": open_refund["status"]}

    payment = payment_repo.get_paid_payment(order["order_no"])
    if not payment:
        # 没有「已支付的渠道流水」：种子订单/线下收款，渠道侧无钱可退。
        # 这是正常分支，业务层照常退款，只是在流水上标明原因。
        return {
            "success": True, "skipped": True, "needs_manual": False,
            "channel_status": REFUND_NOT_NEEDED, "channel": None,
            "channel_error": None, "channel_raw": None,
            "out_request_no": out_request_no, "pay_no": None,
            "amount": amount, "refunded_total": 0, "partial": False,
            "message": "该订单没有已支付的渠道流水，无需渠道退款",
        }

    refundable = payment_repo.refundable_amount(payment)
    try:
        provider = get_provider(provider_name or payment["provider"])
    except Exception as e:
        return {"error": f"退款渠道不可用：{e}", "channel_status": REFUND_FAILED}

    if amount > refundable + payment_repo.AMOUNT_TOLERANCE:
        # 本地额度不够。先别急着拒绝——**渠道知道这个请求号是不是已经退过了**：
        # 典型场景是「上次退款已成功但本地流水没写全，客户端拿同一个 requestId 重试」，
        # 此时若直接按超额拒绝，就会出现"钱退了、订单却还挂着"的尴尬。
        # 查一次渠道（只在这条异常分支上发生）：
        #   已退 → 按重复请求返回成功，不再占额度；
        #   查不到 → 确实超额，照常拒绝。
        try:
            seen = provider.query_refund(pay_no=payment["pay_no"],
                                         out_request_no=out_request_no,
                                         trade_no=payment.get("trade_no"))
        except Exception:
            seen = {}
        if seen.get("queried") and seen.get("found") and seen.get("refunded"):
            payload = _channel_payload(provider.name, out_request_no, payment, seen)
            payload.update({
                "success": True, "skipped": False, "duplicate": True,
                "provider_label": provider.label, "amount": amount,
                "refund_fee": seen.get("refund_amount"),
                "channel_status": REFUND_OK,
                "refunded_total": float(payment["refunded_amount"] or 0),
                "refundable": refundable, "partial": False, "needs_manual": False,
                "message": f"该退款请求此前已由{provider.label}受理，未重复退款（请求号 {out_request_no}）",
            })
            return payload
        # 本地先拦：不浪费一次渠道调用，也不给渠道制造必然失败的请求
        hint = "" if seen.get("queried") else "（渠道退款查询不可用，未能确认是否为重复请求）"
        return {"error": f"超出可退金额：本笔支付实付 {float(payment['amount']):.2f}，"
                         f"已退 {float(payment['refunded_amount'] or 0):.2f}，"
                         f"可退余额 {refundable:.2f}{hint}",
                "refundable": refundable, "channel_status": REFUND_FAILED}

    result = provider.refund(pay_no=payment["pay_no"], amount=amount,
                             out_request_no=out_request_no, reason=reason,
                             trade_no=payment.get("trade_no"))
    if not result.get("ok"):
        retry_hint = ""
        if result.get("unknown"):
            retry_hint = "（结果未知，请先用对账接口确认，不要直接重试）"
        elif result.get("retryable"):
            retry_hint = "（可稍后用同一个退款请求号重试）"
        payload = _channel_payload(provider.name, out_request_no, payment, result)
        payload.update({
            "error": (result.get("error") or "渠道退款失败") + retry_hint,
            "channel_status": result.get("channel_status") or REFUND_FAILED,
            "retryable": bool(result.get("retryable")),
            "unknown": bool(result.get("unknown")),
            "code": result.get("code"), "sub_code": result.get("sub_code"),
            "amount": amount,
        })
        # 失败的尝试也要留痕（同一个 request_id 只留一行，重试时被覆盖为最新结果）：
        # - 结果未知 → 「退款待对账」：钱可能在路上，**阻断**新的退款，等对账；
        # - 明确失败 → 「退款失败」：不阻断重试，同时是「线下退款」收口的依据。
        ledger_status = (REFUND_PENDING_ORDER_STATUS if result.get("unknown")
                         else "退款失败")
        flight_repo.attach_refund_channel(
            out_request_no, payload, status=ledger_status,
            create={"order_no": order["order_no"], "member_id": order.get("member_id"),
                    "refund_type": refund_type, "amount": amount, "fee": fee,
                    "status": ledger_status})
        return payload

    base = _channel_payload(provider.name, out_request_no, payment, result)
    base.update({
        "success": True, "skipped": False,
        "provider_label": provider.label,
        "amount": amount, "duplicate": bool(result.get("duplicate")),
        "refund_fee": result.get("refund_fee"),
        "channel_status": result.get("channel_status") or REFUND_OK,
        "channel_refund_no": result.get("channel_refund_no"),
    })

    if result.get("duplicate"):
        # 渠道按 out_request_no 判定这是重复请求：**本次没有新的资金变动**。
        # 所以不能再占一次本地退款额度（首次请求已经占过），否则账目会多记一笔退款。
        base.update({"refunded_total": float(payment["refunded_amount"] or 0),
                     "refundable": refundable, "partial": False,
                     "needs_manual": False,
                     "warning": "渠道按退款请求号判定为重复请求，未重复退款；"
                                "若本地额度与渠道不一致，请用退款对账接口核对",
                     "message": f"该退款请求此前已受理，渠道未重复退款（请求号 {out_request_no}）"})
        return base

    claim = payment_repo.claim_refund_amount(payment["pay_no"], amount)
    if not claim.get("ok"):
        # 渠道已经把钱退了，本地额度却没占上（并发/人工改库）：必须人工核对，不能装作成功
        base.update({"needs_manual": True,
                     "warning": f"渠道已退款成功，但本地额度占用失败：{claim.get('error')}，请人工核对账目",
                     "refunded_total": claim.get("refunded_total"),
                     "refundable": claim.get("refundable")})
        base["message"] = base["warning"]
        return base

    base.update({"refunded_total": claim.get("refunded_total"),
                 "refundable": claim.get("refundable"),
                 "partial": bool(claim.get("partial")),
                 "needs_manual": False,
                 "message": (f"退款成功：{amount:.2f} 元已由{provider.label}原路退回"
                             + ("；本笔支付已全额退完" if not claim.get("partial")
                                else f"；本笔支付实付 {float(payment['amount']):.2f} 元，"
                                     f"累计已退 {claim.get('refunded_total'):.2f} 元，"
                                     f"未退 {claim.get('refundable'):.2f} 元"))})
    return base


def query_refund_status(out_request_no: str, pay_no: str = None,
                        order_no: str = None, provider_name: str = None) -> Dict[str, Any]:
    """退款对账：用退款请求号去渠道问「这笔退款到底成功了没」。

    场景：refund_payment 返回 unknown（超时）。查到已成功 → 把中间态流水改成
    「渠道已退款」（钱出去了、订单还没跟上，交给人工收口）；
    查到没退 → 改成「退款失败」并**解除**拦截，允许用同一个请求号重新发起。
    """
    from services import flight_repo
    out_request_no = (out_request_no or "").strip()
    payment = None
    if pay_no:
        payment = payment_repo.get_payment(pay_no)
    elif order_no:
        payment = payment_repo.get_paid_payment(order_no)
    if not payment:
        return {"error": f"未找到可对账的支付流水：{pay_no or order_no}"}
    if not out_request_no:
        return {"error": "缺少退款请求号"}

    try:
        provider = get_provider(provider_name or payment["provider"])
    except Exception as e:
        return {"error": f"退款渠道不可用：{e}"}

    result = provider.query_refund(pay_no=payment["pay_no"], out_request_no=out_request_no,
                                   trade_no=payment.get("trade_no"))
    if not result.get("queried"):
        # 渠道没给出可用回答（网络故障/渠道不支持）：不能据此判断"没退款"
        return {"error": result.get("error") or "渠道未返回可用的退款查询结果",
                "queried": False, "found": False,
                "pay_no": payment["pay_no"], "out_request_no": out_request_no}

    refunded = bool(result.get("refunded"))
    # 把中间态流水落定：这决定了后续还能不能对该订单发起退款
    settled_status = (REFUND_CHANNEL_DONE_STATUS if refunded else "退款失败")
    row = flight_repo.get_refund_by_request(out_request_no)
    if row:
        flight_repo.attach_refund_channel(out_request_no, {
            "pay_no": payment["pay_no"], "out_request_no": out_request_no,
            "channel": provider.name,
            "channel_status": (result.get("channel_status") or REFUND_OK) if refunded else REFUND_FAILED,
            "channel_error": None if refunded else "对账确认渠道无此退款，可重新发起",
            "channel_raw": _channel_payload(provider.name, out_request_no, payment, result)["channel_raw"],
        }, status=settled_status)

    return {
        "success": True,
        "queried": True,
        "found": bool(result.get("found")),
        "refunded": refunded,
        "refund_status": result.get("refund_status"),
        "refund_amount": result.get("refund_amount"),
        "channel": provider.name, "pay_no": payment["pay_no"],
        "out_request_no": out_request_no,
        "ledger_status": settled_status if row else None,
        "refundable": payment_repo.refundable_amount(payment),
        "next_step": ("渠道确认这笔钱已经退了。请勿再次发起退款；"
                      "若订单状态仍不是「已退款」，用管理端「确认退款完成」收口"
                      if refunded else
                      "渠道确认没有这笔退款，可以安全地用同一个退款请求号重新发起"),
        "message": ("渠道已确认退款成功" if refunded
                    else "渠道查无这笔退款，可安全重试（请复用同一退款请求号）"),
    }

