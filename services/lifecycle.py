"""
订单生命周期后台任务（两个扫描）：

1. 起飞自动核销：status IN ('已出票','已改签') 且 flight_date + dep_time 已过 → 「已使用」。
   真实航司在值机/登机/起飞后会流转票状态（FLOWN），这里用后台线程模拟；
   已使用的票在状态机层面退不了、改不了，与"起飞后不可自愿退"的时间规则形成双保险。

2. 待支付超时取消：status='待支付' 且创建时间超过 _PAY_TIMEOUT_MINUTES → 「已取消」并站内通知，
   让订单状态机真正闭环（不支付的单不再永久挂起）。

由 web_app 启动时拉起（每 5 分钟扫描一次）；DB 为共享 SQLite，langgraph 进程
读取到的始终是最新状态。
"""

import threading
import time
import traceback
from datetime import datetime, timedelta

from services import db, notification_repo

_INTERVAL_SECONDS = 300
_PAY_TIMEOUT_MINUTES = 15  # 待支付订单超过该时长未支付即自动取消


def mark_flown_orders() -> int:
    """把起飞时间已过的有效票（已出票/已改签）置为「已使用」。返回影响行数。"""
    conn = db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE orders SET status = '已使用' "
            "WHERE status IN ('已出票', '已改签') "
            "AND (flight_date || ' ' || (SELECT f.dep_time FROM flights f WHERE f.flight_no = orders.flight_no)) <= ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _parse_created_at(raw: str) -> datetime:
    """解析订单创建时间；兼容旧数据的纯日期格式（按当天 23:59:59 计）。"""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return datetime.strptime(raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59)


def cancel_stale_pending_orders() -> int:
    """把超时未支付的「待支付」订单置为「已取消」，并给会员发站内通知。返回影响行数。"""
    cutoff = datetime.now() - timedelta(minutes=_PAY_TIMEOUT_MINUTES)
    conn = db.get_connection()
    canceled = 0
    try:
        rows = conn.execute(
            "SELECT order_no, member_id, flight_no, flight_date, created_at "
            "FROM orders WHERE status = '待支付'").fetchall()
        for r in rows:
            try:
                created = _parse_created_at(r["created_at"])
            except ValueError:
                continue
            if created > cutoff:
                continue
            conn.execute("UPDATE orders SET status = '已取消' WHERE order_no = ?", (r["order_no"],))
            notification_repo.create_notification(
                conn=conn, member_id=r["member_id"],
                title="订单已自动取消",
                content=(f"您的订单 {r['order_no']}（航班 {r['flight_no']}，{r['flight_date']}）"
                         f"超过{_PAY_TIMEOUT_MINUTES}分钟未支付，已自动取消。如需出行请重新预订。"),
                ntype="order_cancel")
            canceled += 1
        if canceled:
            conn.commit()
        return canceled
    finally:
        conn.close()


def _loop():
    while True:
        try:
            n = mark_flown_orders()
            if n:
                print(f"🔄 [生命周期] {n} 笔订单航班已起飞，状态置为「已使用」")
        except Exception:
            traceback.print_exc()
        try:
            n = cancel_stale_pending_orders()
            if n:
                print(f"🔄 [生命周期] {n} 笔订单超时未支付，状态置为「已取消」并通知会员")
        except Exception:
            traceback.print_exc()
        time.sleep(_INTERVAL_SECONDS)


def start_lifecycle_worker():
    """启动后台扫描线程（幂等：多次调用只启动一次）。"""
    t = threading.Thread(target=_loop, daemon=True, name="order-lifecycle")
    t.start()
    print("🔄 订单生命周期任务已启动（每5分钟扫描：起飞核销 / 待支付超时取消）")
    return t
