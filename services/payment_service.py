"""支付编排层：把「渠道能力」与「业务落账」串起来。

职责边界（与 AGENTS.md 一致）：
- 这是唯一同时接触 provider（外部网关）与 repo（本地库）的地方；
- 写库动作全部在本层以代码完成，不经过 LLM；
- 不依赖 Flask request 与 LLM，是单测的主要抓手（0 token）。
"""

from typing import Any, Dict, Optional

from services import db, payment_repo
from services.payment import get_provider

STATUS_PAID = "支付成功"
STATUS_CLOSED = "已关闭"
STATUS_PENDING = "待支付"


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
    if order["status"] != "待支付":
        return {"error": f"订单状态为「{order['status']}」，无需支付"}

    try:
        provider = get_provider(provider_name)
    except Exception as e:
        return {"error": f"支付渠道不可用：{e}"}

    payment = payment_repo.create_payment(order["order_no"], order["amount"], provider.name)
    result = provider.create_payment(
        pay_no=payment["pay_no"],
        subject=subject or f"机票订单 {order['order_no']}",
        amount=float(order["amount"]),
        return_url=return_url or "",
        notify_url=notify_url or "",
    )
    if not result.get("ok"):
        return {"error": result.get("error") or "支付发起失败"}

    return {
        "success": True,
        "provider": provider.name,
        "provider_label": provider.label,
        "mode": result.get("mode") or provider.mode,
        "pay_url": result.get("pay_url"),
        "pay_no": payment["pay_no"],
        "order_no": order["order_no"],
        "amount": float(order["amount"]),
        "message": result.get("message") or "请在收银台完成付款",
    }


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
        result = flight_repo.pay_order(payment["order_no"])
    if result.get("error"):
        # 钱已收但订单推进不了（典型：支付期间订单被超时任务取消）
        order = _load_order(payment["order_no"])
        if order:
            _notify(order["member_id"], "支付异常",
                    f"订单 {payment['order_no']} 已收款 ¥{payment['amount']}，"
                    f"但出票失败（{result['error']}）。客服将尽快与您联系处理退款。")
        return {"success": True, "settled": False, "pay_no": pay_no,
                "order_no": payment["order_no"],
                "warning": f"已收款但未能出票：{result['error']}（需人工处理）"}

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
