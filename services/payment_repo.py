"""支付流水仓储：落账与幂等的唯一出口。

关键不变量（改动前请先读）：
- `pay_no` 是幂等键。一笔流水只会从「待支付」翻转到「支付成功」一次；
- `mark_paid()` 返回 True 才代表「本次是首次翻转」，调用方据此推进业务订单。
  支付宝异步通知会间隔重投 8 次，全靠这里去重，漏掉这层会出现重复出票；
- 一笔业务订单可有多笔流水（支付失败重试），但同一时刻只允许存在一笔待支付流水。

本模块只做数据落账，不含任何渠道逻辑（签名/验签见 services/payment/）。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from services import db

# 支付超时：与 lifecycle 的待支付订单超时（15 分钟）保持一致
DEFAULT_PAY_TIMEOUT_MINUTES = 15

# 金额容差（元）：浮点比较用
AMOUNT_TOLERANCE = 0.01


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _generate_pay_no(conn) -> str:
    """生成唯一支付号：P + 月日时分秒 + 4 位随机。"""
    import random
    for _ in range(30):
        no = "P" + datetime.now().strftime("%m%d%H%M%S") + "".join(
            random.choice("0123456789") for _ in range(4))
        if not conn.execute("SELECT 1 FROM payments WHERE pay_no = ?", (no,)).fetchone():
            return no
    raise RuntimeError("支付号生成失败")


def create_payment(order_no: str, amount, provider: str,
                   timeout_minutes: int = DEFAULT_PAY_TIMEOUT_MINUTES) -> Dict[str, Any]:
    """为一笔订单创建（或复用）待支付流水。

    同一订单+同一渠道+同一金额已存在待支付流水时直接复用，
    避免用户反复点「去支付」产生一堆悬挂流水。
    """
    amount = float(amount)
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM payments WHERE order_no = ? AND status = ? AND provider = ? "
            "AND abs(amount - ?) < ? ORDER BY id DESC LIMIT 1",
            (order_no, "待支付", provider, amount, AMOUNT_TOLERANCE)).fetchone()
        if row:
            return dict(row)
        pay_no = _generate_pay_no(conn)
        now = _now()
        expire = (datetime.now() + timedelta(minutes=timeout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO payments (pay_no, order_no, provider, out_trade_no, amount, status, "
            "created_at, expire_at) VALUES (?,?,?,?,?,?,?,?)",
            (pay_no, order_no, provider, pay_no, amount, "待支付", now, expire))
        conn.commit()
        row = conn.execute("SELECT * FROM payments WHERE pay_no = ?", (pay_no,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_payment(pay_no: str) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM payments WHERE pay_no = ?", (pay_no,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_by_out_trade_no(out_trade_no: str) -> Optional[Dict[str, Any]]:
    """按商户交易号查流水（异步回调里只有这个号）。"""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM payments WHERE out_trade_no = ?", (out_trade_no,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_payment(order_no: str) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM payments WHERE order_no = ? ORDER BY id DESC LIMIT 1",
            (order_no,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def amount_matches(payment: Dict[str, Any], amount) -> bool:
    """回调金额与流水金额是否一致（防篡改）。"""
    try:
        return abs(float(payment["amount"]) - float(amount)) <= AMOUNT_TOLERANCE
    except (TypeError, ValueError, KeyError):
        return False


def mark_paid(pay_no: str, trade_no: str = None, buyer_id: str = None,
              notify_raw: str = None) -> bool:
    """幂等置为「支付成功」。

    返回 True = 本次是首次翻转（调用方应推进业务订单）；
    返回 False = 流水不存在或此前已处理过（重复通知，调用方什么都不用做）。
    """
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE payments SET status = ?, trade_no = COALESCE(?, trade_no), "
            "buyer_id = COALESCE(?, buyer_id), notify_raw = COALESCE(?, notify_raw), paid_at = ? "
            "WHERE pay_no = ? AND status = ?",
            ("支付成功", trade_no, buyer_id, notify_raw, _now(), pay_no, "待支付"))
        changed = cur.rowcount == 1
        conn.commit()
        return changed
    finally:
        conn.close()


def mark_closed(pay_no: str) -> bool:
    """关闭待支付流水（用户取消 / 订单超时）。"""
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE payments SET status = ? WHERE pay_no = ? AND status = ?",
            ("已关闭", pay_no, "待支付"))
        changed = cur.rowcount == 1
        conn.commit()
        return changed
    finally:
        conn.close()


def close_pending_by_order(order_no: str) -> int:
    """关闭某订单下全部待支付流水（订单被取消时调用），返回受影响行数。"""
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE payments SET status = ? WHERE order_no = ? AND status = ?",
            ("已关闭", order_no, "待支付"))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def list_expired_pending(minutes: int = DEFAULT_PAY_TIMEOUT_MINUTES):
    """列出超时仍未支付的流水，供后台任务关单。"""
    cutoff = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM payments WHERE status = ? AND created_at < ?",
            ("待支付", cutoff)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
