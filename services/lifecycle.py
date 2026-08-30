"""
订单生命周期后台任务：航班起飞后自动将有效票置为「已使用」。

真实航司在值机/登机/起飞后会流转票状态（FLOWN），这里用后台线程模拟：
扫描 status IN ('已出票','已改签') 且 flight_date + dep_time 已过的订单 → status='已使用'。

连带效果：
- 已使用的票在状态机层面退不了、改不了（refund/change 的状态校验会拒绝），
  与"起飞后不可自愿退"的时间规则形成双保险；
- 系统运行几天后自然沉淀历史订单，会员查询"坐过的航班"有真实数据。

由 web_app 启动时拉起（每 5 分钟扫描一次）；DB 为共享 SQLite，langgraph 进程
读取到的始终是最新状态。
"""

import threading
import time
import traceback
from datetime import datetime

from services import db

_INTERVAL_SECONDS = 300


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


def _loop():
    while True:
        try:
            n = mark_flown_orders()
            if n:
                print(f"🔄 [生命周期] {n} 笔订单航班已起飞，状态置为「已使用」")
        except Exception:
            traceback.print_exc()
        time.sleep(_INTERVAL_SECONDS)


def start_lifecycle_worker():
    """启动后台扫描线程（幂等：多次调用只启动一次）。"""
    t = threading.Thread(target=_loop, daemon=True, name="order-lifecycle")
    t.start()
    print("🔄 订单生命周期任务已启动（每5分钟扫描起飞航班）")
    return t
