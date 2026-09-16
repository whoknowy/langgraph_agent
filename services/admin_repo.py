"""
管理端数据层：退款处理、投诉处理、航班/机场/航司维护、订单与会员查询。

调用前提：web 层已通过 services.security.set_current_admin 绑定管理员身份
（enforce_owner 对管理员豁免归属校验）。本模块不做会员归属限制——跨会员
处理正是运营平台的职责。
"""

import random
from datetime import date, datetime, timedelta

from services import db
from services.db_seed import HORIZON_DAYS


def _conn():
    conn = db.get_connection()
    db.init_schema(conn)
    return conn


# ---------------------------------------------------------------- 工作台统计

def get_stats() -> dict:
    conn = _conn()
    today = date.today().isoformat()

    def one(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    stats = {
        "pending_refunds": one("SELECT COUNT(*) FROM orders WHERE status = '退票中'"),
        "pending_complaints": one("SELECT COUNT(*) FROM complaints WHERE status IN ('处理中','已升级')"),
        "flights_on_sale": one("SELECT COUNT(DISTINCT flight_no) FROM flights"),
        # created_at 是完整时间戳，按日期前缀比较才是"今日"（等值比较恒为 0）
        "today_orders": one("SELECT COUNT(*) FROM orders WHERE substr(created_at, 1, 10) = ?", (today,)),
        "today_checkins": one("SELECT COUNT(*) FROM checkins WHERE substr(checkin_at, 1, 10) = ?", (today,)),
    }
    conn.close()
    return stats


def stats_trend(days: int = 7) -> dict:
    """工作台图表数据：近 N 天订单量/退款量趋势 + 热门航线 Top5。

    - 订单按 created_at 日期前缀聚合；退款按 refunded_at（退款完成时刻，
      存量已退款订单无此列不计入）；日期轴恒为今天往前 N 天，缺数补 0。
    - 热门航线按近 N 天订单量取前 5（出发城市-到达城市）。
    """
    days = max(1, min(int(days), 30))
    conn = _conn()
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    start = dates[0]

    def by_day(column: str) -> dict:
        counts = {d: 0 for d in dates}
        sql = (f"SELECT substr({column}, 1, 10) AS day, COUNT(*) AS n FROM orders "
               f"WHERE {column} IS NOT NULL AND substr({column}, 1, 10) >= ? "
               f"GROUP BY day")
        for r in conn.execute(sql, (start,)):
            day = str(r["day"])
            if day in counts:
                counts[day] = r["n"]
        return counts

    orders_by_day = by_day("created_at")
    refunds_by_day = by_day("refunded_at")

    routes = conn.execute(
        "SELECT fd.city_cn || '-' || fa.city_cn AS route, COUNT(*) AS n "
        "FROM orders o "
        "JOIN flights f ON f.flight_no = o.flight_no "
        "JOIN airports fd ON fd.iata3 = f.dep_iata "
        "JOIN airports fa ON fa.iata3 = f.arr_iata "
        "WHERE substr(o.created_at, 1, 10) >= ? "
        "GROUP BY route ORDER BY n DESC, route LIMIT 5",
        (start,)).fetchall()
    conn.close()

    return {
        "days": dates,
        "orders": [orders_by_day[d] for d in dates],
        "refunds": [refunds_by_day[d] for d in dates],
        "top_routes": [{"route": r["route"], "count": r["n"]} for r in routes],
    }


# ---------------------------------------------------------------- 订单 / 退款

_ORDER_SELECT = (
    "SELECT o.order_no, o.member_id, o.flight_no, o.flight_date, o.cabin, o.amount, "
    "o.status, o.created_at, o.refund_amount, o.refund_reason, o.admin_note, "
    "a.name_cn AS airline, c.name AS member_name, c.level AS member_level, "
    "fd.city_cn AS dep_city, fa.city_cn AS arr_city, f.dep_time "
    "FROM orders o "
    "JOIN flights f ON f.flight_no = o.flight_no "
    "JOIN airlines a ON a.code = f.airline_code "
    "JOIN airports fd ON fd.iata3 = f.dep_iata "
    "JOIN airports fa ON fa.iata3 = f.arr_iata "
    "JOIN customers c ON c.member_id = o.member_id "
)


def list_orders(status: str = None, q: str = None, limit: int = 200) -> dict:
    """全局订单查询（可按状态筛选、按订单号/会员号/会员名搜索）。"""
    conn = _conn()
    sql, params = _ORDER_SELECT, []
    if status:
        sql += " WHERE o.status = ?"
        params.append(status)
    if q:
        cond = (" WHERE" if not status else " AND") + \
               " (o.order_no LIKE ? OR o.member_id LIKE ? OR c.name LIKE ?)"
        sql += cond
        like = f"%{q.strip()}%"
        params += [like, like, like]
    sql += " ORDER BY o.created_at DESC, o.order_no DESC LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"orders": rows, "count": len(rows)}


def list_refund_queue() -> dict:
    """待处理退款队列（状态=退票中）。"""
    result = list_orders(status="退票中")
    return {"refunds": result["orders"], "count": len(result["orders"])}


def approve_refund(order_no: str, refund_amount: int = None, admin_note: str = "") -> dict:
    """同意退款：退票中 → 已退款，记录实际退款金额与备注（默认全额）。

    顺序与不变量：**先渠道退款、成功之后才把订单置为「已退款」**。
    渠道失败时订单留在「退票中」，管理员修复原因后可重试；
    退款请求号按「用户提交时的 request_id，否则 订单+金额」生成——
    同金额重试复用同一个号，支付宝侧天然幂等，不会重复退钱；改了金额才会换号。
    """
    from services import audit, flight_repo, notification_repo, payment_service
    order_no_n = security_normalize(order_no)
    conn = _conn()
    row = conn.execute("SELECT status, amount, member_id FROM orders WHERE order_no = ?",
                       (order_no_n,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"订单不存在：{order_no}"}
    if row["status"] != "退票中":
        return {"error": f"订单状态为「{row['status']}」，只有「退票中」的订单可以退款"}

    amount = int(refund_amount) if refund_amount is not None else int(row["amount"])
    if amount < 0 or amount > int(row["amount"]):
        return {"error": f"退款金额需在 0 ~ {row['amount']} 之间"}

    pending = flight_repo.get_pending_refund(order_no_n, refund_type="special")
    out_request_no = (pending or {}).get("request_id") or f"ADM-{order_no_n}-{amount}"

    channel = payment_service.refund_payment(
        order_no=order_no_n, amount=amount, out_request_no=out_request_no,
        refund_type="special", fee=0,
        reason=(admin_note or "").strip() or "非自愿退票（航班延误/取消）")
    if channel.get("error"):
        audit.write_error("退票审批(渠道退款)", channel["error"], target=order_no_n,
                          request_id=out_request_no)
        return {"error": f"渠道退款未完成：{channel['error']}",
                "channel_status": channel.get("channel_status"),
                "retryable": channel.get("retryable"), "unknown": channel.get("unknown"),
                "out_request_no": out_request_no}

    result = flight_repo._transition_order(order_no_n, None, "退票中", "已退款")
    if result.get("error"):
        # 钱已经退出去了，订单却没推进：留痕 + 把渠道结果补写进流水，转人工核对
        audit.blocked("退票审批", f"渠道已退款但订单未推进：{result['error']}",
                      target=order_no_n, request_id=out_request_no)
        flight_repo.attach_refund_channel(
            out_request_no, channel, status="渠道已退款",
            create={"order_no": order_no_n, "member_id": row["member_id"],
                    "refund_type": "special", "amount": amount, "fee": 0,
                    "status": "渠道已退款"})
        return {"error": f"渠道已退款成功，但订单状态更新失败：{result['error']}，已转人工核对",
                "needs_manual": True, "order_no": order_no_n,
                "out_request_no": out_request_no}

    from services import checkin_repo
    checkin_repo.cancel_checkin(order_no_n)   # 退款完成，自动取消值机、释放座位
    flight_repo.attach_refund_channel(
        out_request_no, channel, status="已退款",
        create={"order_no": order_no_n, "member_id": row["member_id"],
                "refund_type": "special", "amount": amount, "fee": 0, "status": "已退款"})
    conn = _conn()
    conn.execute("UPDATE orders SET refund_amount = ?, admin_note = ?, refunded_at = ? WHERE order_no = ?",
                 (amount, (admin_note or "").strip(),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_no_n))
    # 通知文案按"钱到底怎么退的"说实话：走了渠道就写原路退回，没流水就写线下办理
    how = ("已由客服为您办理退款，请注意查收。" if channel.get("skipped")
           else f"已通过{channel.get('provider_label') or '支付渠道'}原路退回，请注意查收。")
    notification_repo.create_notification(
        conn=conn, member_id=row["member_id"], title="退款审核通过", ntype="refund",
        content=f"您的订单 {order_no_n} 退票审核已通过，退款 {amount} 元{how}")
    conn.commit()
    conn.close()
    return {"success": True, "order_no": order_no_n,
            "status": "已退款", "refund_amount": amount,
            "channel": channel.get("channel"),
            "channel_status": channel.get("channel_status"),
            "out_request_no": out_request_no,
            "message": f"订单 {order_no_n} 已退款 {amount} 元"
                       + ("" if channel.get("skipped") else f"（{channel.get('channel_status')}）")}


def settle_channel_refund(order_no: str, admin_note: str = "", offline: bool = False) -> dict:
    """「确认退款完成」：渠道的钱确实退出去了、但订单状态没跟上时的人工收口。

    两种模式，都要求**有据可依**，不允许凭空把订单标成已退款：

    1. `offline=False`（默认）：要求存在一条「渠道已退款」流水——那是"钱确实退了"的凭证。
       典型场景：渠道退款超时 → 对账确认钱已退 → 但订单当时没被改动
       （我们坚持"渠道成功才改单"）。此时不能重新发起退款（会退第二笔），只能补订单状态。
    2. `offline=True`（线下退款）：渠道**明确表示退不了**（如支付宝侧查无此交易、
       该渠道未实现退款），但运营需要给用户结案。此时要求：
       - 必须写备注（至少 4 个字）说明为什么走线下；
       - 必须有一条失败记录，且失败原因是"渠道不可能退"（ACQ.TRADE_NOT_EXIST / 渠道不支持）。
       满足后流水记为「线下退款」，通知用户"已由客服线下办理"，审计单独记一笔。
    """
    from services import audit, checkin_repo, flight_repo, notification_repo
    order_no_n = security_normalize(order_no)
    note = (admin_note or "").strip()
    conn = _conn()
    row = conn.execute("SELECT status, amount, member_id FROM orders "
                       "WHERE order_no = ?", (order_no_n,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"订单不存在：{order_no}"}

    open_refund = flight_repo.find_open_refund(order_no_n)
    failed_refund = None
    if offline:
        if len(note) < 4:
            return {"error": "线下退款必须写明备注（至少 4 个字），说明为何不经过渠道退款"}
        failed_refund = flight_repo.find_failed_refund(order_no_n)
        if not (failed_refund and _channel_refund_impossible(failed_refund)):
            return {"error": "该订单没有「渠道退不了」的记录，不能走线下退款；"
                             "请先尝试渠道退款，或先用对账接口确认渠道结果"}
    elif not open_refund or open_refund.get("status") != "渠道已退款":
        return {"error": "该订单没有「渠道已退款」的流水，不能直接标记退款完成；"
                         "请先用退款对账接口（/admin/api/refunds/query）确认渠道结果"}

    if row["status"] not in ("已出票", "已改签", "退票中"):
        return {"error": f"订单状态为「{row['status']}」，无需收口"}

    source = open_refund or failed_refund or {}
    request_id = source.get("request_id")
    amount = int(source.get("amount") or 0) or int(row["amount"] or 0)
    target_status = "线下退款" if offline else "已退款"

    result = flight_repo._transition_order(order_no_n, None,
                                           ("已出票", "已改签", "退票中"), "已退款")
    if result.get("error"):
        audit.blocked("退款收口", result["error"], target=order_no_n)
        return result

    if request_id:
        flight_repo.attach_refund_channel(request_id, {
            "pay_no": source.get("pay_no"),
            "out_request_no": source.get("out_request_no"),
            "channel": source.get("channel"),
            "channel_status": source.get("channel_status"),
            "channel_error": (f"线下退款：{note}" if offline
                              else source.get("channel_error")),
            "channel_raw": source.get("channel_raw"),
        }, status=target_status)

    checkin_repo.cancel_checkin(order_no_n)
    conn = _conn()
    conn.execute("UPDATE orders SET refund_amount = ?, admin_note = ?, refunded_at = ? "
                 "WHERE order_no = ?",
                 (amount, f"{'线下退款收口' if offline else '渠道退款确认收口'}：{note}".strip("："),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_no_n))
    how = ("已由客服为您办理退款，预计 3 个工作日内到账，请注意查收。"
           if offline else "已完成原路退回，请注意查收。")
    notification_repo.create_notification(
        conn=conn, member_id=row["member_id"], title="退款处理完成", ntype="refund",
        content=f"您的订单 {order_no_n} 退款 {amount} 元{how}")
    conn.commit()
    conn.close()
    audit.write_ok("线下退款" if offline else "退款收口", target=order_no_n,
                   detail={"amount": amount, "offline": bool(offline),
                           "request_id": request_id, "admin_note": note})
    return {"success": True, "order_no": order_no_n, "status": "已退款",
            "refund_amount": amount, "out_request_no": request_id, "offline": bool(offline),
            "message": f"订单 {order_no_n} 已按{'线下退款' if offline else '渠道退款结果'}"
                       f"收口为「已退款」（{amount} 元）"}


def _channel_refund_impossible(row) -> bool:
    """这条失败记录是否属于「渠道明确退不了」（线下退款的准入条件）。

    只看真正的不可退信号：支付宝查无此交易（本地虚拟支付）、渠道未实现退款。
    """
    if not row:
        return False
    blob = f"{row.get('channel_status') or ''} {row.get('channel_error') or ''} " \
           f"{row.get('channel_raw') or ''}"
    return ("ACQ.TRADE_NOT_EXIST" in blob or "查无此交易" in blob
            or "渠道不支持" in blob)


def reject_refund(order_no: str, admin_note: str = "") -> dict:
    """驳回退票：退票中 → 恢复进入审批前的状态（改签单回到「已改签」，其余回「已出票」）。"""
    from services import notification_repo
    order_no_n = security_normalize(order_no)
    conn = _conn()
    r = conn.execute("SELECT status, prev_status, member_id FROM orders WHERE order_no = ?",
                     (order_no_n,)).fetchone()
    if not r:
        conn.close()
        return {"error": f"订单不存在：{order_no}"}
    if r["status"] != "退票中":
        conn.close()
        return {"error": f"订单状态为「{r['status']}」，只有「退票中」的订单可以驳回"}
    restore = r["prev_status"] or "已出票"
    conn.execute("UPDATE orders SET status = ?, admin_note = ? WHERE order_no = ?",
                 (restore, "驳回退票：" + (admin_note or "").strip(), order_no_n))
    notification_repo.create_notification(
        conn=conn, member_id=r["member_id"], title="退票审核结果", ntype="refund",
        content=(f"您的订单 {order_no_n} 退票申请未通过审核"
                 + (f"（{admin_note.strip()}）" if (admin_note or "").strip() else "")
                 + f"，订单已恢复为「{restore}」。"))
    conn.commit()
    conn.close()
    return {"success": True, "order_no": order_no_n, "status": restore,
            "message": f"订单 {order_no_n} 退票申请已驳回，恢复为「{restore}」"}


def security_normalize(v):
    return (v or "").strip().upper()


# ---------------------------------------------------------------- 航班登机口指派

def assign_gate(flight_no: str, gate: str) -> dict:
    """指派/变更航班登机口（航班级物理资源，管理端"航班动态"职责）。

    变更（含首次指派）时通知该航班所有已值机旅客，登机牌与智能体转告
    随即展示最新登机口（checkin_repo 读取 flights.gate 实时值）。
    """
    import re
    flight_no = security_normalize(flight_no)
    gate = (gate or "").strip().upper()
    if not re.fullmatch(r"[A-Z][0-9]{1,3}", gate):
        return {"error": "登机口格式不正确（如 B12：1位字母+1~3位数字）"}

    from services import notification_repo
    conn = _conn()
    row = conn.execute("SELECT gate FROM flights WHERE flight_no = ?", (flight_no,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"航班不存在：{flight_no}"}
    old = (row["gate"] or "").strip().upper()
    conn.execute("UPDATE flights SET gate = ? WHERE flight_no = ?", (gate, flight_no))

    # 通知该航班已值机旅客（限定未过期航班）
    today = date.today().isoformat()
    affected = conn.execute(
        "SELECT ck.order_no, ck.seat_no, o.member_id, o.flight_date, c.name AS passenger "
        "FROM checkins ck "
        "JOIN orders o ON o.order_no = ck.order_no "
        "JOIN customers c ON c.member_id = o.member_id "
        "WHERE o.flight_no = ? AND o.flight_date >= ?",
        (flight_no, today)).fetchall()
    for r in affected:
        change_desc = (f"由 {old} 变更为 {gate}" if old and old != gate
                       else f"已指派为 {gate}")
        notification_repo.create_notification(
            conn=conn, member_id=r["member_id"], title="登机口变更提醒", ntype="flight",
            content=(f"{r['passenger']}您好，您乘坐的航班 {flight_no}"
                     f"（{r['flight_date']}，订单 {r['order_no']}，座位 {r['seat_no']}）"
                     f"登机口{change_desc}，请以最新登机牌为准。"))
    conn.commit()
    notified = len(affected)
    conn.close()
    message = f"航班 {flight_no} 登机口已设为 {gate}"
    if notified:
        message += f"，已通知 {notified} 位已值机旅客"
    return {"success": True, "flight_no": flight_no, "gate": gate,
            "notified": notified, "message": message}


# ---------------------------------------------------------------- 投诉

_COMPLAINT_SELECT = (
    "SELECT c.ticket_no, c.member_id, c.order_no, c.content, c.status, c.created_at, c.reply, "
    "cu.name AS member_name, cu.level AS member_level, cu.phone "
    "FROM complaints c JOIN customers cu ON cu.member_id = c.member_id "
)


def list_complaints(status: str = None, q: str = None, limit: int = 200) -> dict:
    conn = _conn()
    sql, params = _COMPLAINT_SELECT, []
    if status:
        sql += " WHERE c.status = ?"
        params.append(status)
    if q:
        cond = (" WHERE" if not status else " AND") + \
               " (c.ticket_no LIKE ? OR c.member_id LIKE ? OR cu.name LIKE ?)"
        sql += cond
        like = f"%{q.strip()}%"
        params += [like, like, like]
    sql += " ORDER BY c.created_at DESC, c.ticket_no DESC LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"complaints": rows, "count": len(rows)}


def resolve_complaint(ticket_no: str, reply: str) -> dict:
    """回复并解决投诉（处理中/已升级 → 已解决）。"""
    from services import notification_repo
    tn = security_normalize(ticket_no)
    if not (reply or "").strip():
        return {"error": "处理回复不能为空"}
    conn = _conn()
    row = conn.execute("SELECT status, member_id FROM complaints WHERE ticket_no = ?", (tn,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"投诉单不存在：{ticket_no}"}
    if row["status"] not in ("处理中", "已升级"):
        conn.close()
        return {"error": f"投诉状态为「{row['status']}」，无法执行解决操作"}
    conn.execute("UPDATE complaints SET status = '已解决', reply = ? WHERE ticket_no = ?",
                 (reply.strip(), tn))
    notification_repo.create_notification(
        conn=conn, member_id=row["member_id"], title="投诉已回复", ntype="complaint",
        content=f"您的投诉 {tn} 已处理完毕，客服回复：{reply.strip()}")
    conn.commit()
    conn.close()
    return {"success": True, "ticket_no": tn, "status": "已解决",
            "message": f"投诉 {tn} 已解决，回复已同步给客户查询"}


def escalate_complaint(ticket_no: str, note: str = "") -> dict:
    """升级投诉（处理中 → 已升级）。"""
    tn = security_normalize(ticket_no)
    conn = _conn()
    row = conn.execute("SELECT status FROM complaints WHERE ticket_no = ?", (tn,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"投诉单不存在：{ticket_no}"}
    if row["status"] != "处理中":
        conn.close()
        return {"error": f"投诉状态为「{row['status']}」，无法升级"}
    conn.execute("UPDATE complaints SET status = '已升级', reply = ? WHERE ticket_no = ?",
                 (("升级备注：" + note.strip()) if note.strip() else None, tn))
    conn.commit()
    conn.close()
    return {"success": True, "ticket_no": tn, "status": "已升级"}


def reopen_complaint(ticket_no: str) -> dict:
    """重新打开已解决的投诉（已解决 → 处理中）。"""
    tn = security_normalize(ticket_no)
    conn = _conn()
    row = conn.execute("SELECT status FROM complaints WHERE ticket_no = ?", (tn,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"投诉单不存在：{ticket_no}"}
    if row["status"] != "已解决":
        conn.close()
        return {"error": f"投诉状态为「{row['status']}」，只有「已解决」可重新打开"}
    conn.execute("UPDATE complaints SET status = '处理中' WHERE ticket_no = ?", (tn,))
    conn.commit()
    conn.close()
    return {"success": True, "ticket_no": tn, "status": "处理中"}


# ---------------------------------------------------------------- 航班维护

_FLIGHT_SELECT = (
    "SELECT f.flight_no, f.airline_code, a.name_cn AS airline, f.dep_iata, f.arr_iata, "
    "fd.city_cn AS dep_city, fa.city_cn AS arr_city, f.dep_time, f.arr_time, "
    "f.duration_min, f.aircraft, f.freq_days, f.gate "
    "FROM flights f JOIN airlines a ON a.code = f.airline_code "
    "JOIN airports fd ON fd.iata3 = f.dep_iata JOIN airports fa ON fa.iata3 = f.arr_iata "
)


def list_flights(q: str = None, limit: int = 300) -> dict:
    conn = _conn()
    sql, params = _FLIGHT_SELECT, []
    if q:
        sql += " WHERE (f.flight_no LIKE ? OR fd.city_cn LIKE ? OR fa.city_cn LIKE ? OR a.name_cn LIKE ?)"
        like = f"%{q.strip()}%"
        params = [like, like, like, like]
    sql += " ORDER BY f.flight_no LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # 附加今日起 7 天最低经济舱价（管理端参考）
    today = date.today().isoformat()
    for r in rows:
        p = conn.execute(
            "SELECT MIN(price) FROM flight_prices WHERE flight_no = ? AND flight_date >= ? AND cabin = '经济'",
            (r["flight_no"], today)).fetchone()[0]
        r["econ_price_from"] = p
    conn.close()
    return {"flights": rows, "count": len(rows)}


def create_flight(payload: dict) -> dict:
    """新增航班并为未来 HORIZON_DAYS 天生成在售票价。

    payload: flight_no, airline_code, dep_iata, arr_iata, dep_time(HH:MM),
             arr_time(HH:MM), aircraft, freq_days(默认1234567),
             econ_price(经济舱基准价), business_ratio(商务舱倍率，默认2.2)
    """
    import re
    flight_no = (payload.get("flight_no") or "").strip().upper()
    airline_code = (payload.get("airline_code") or "").strip().upper()
    dep_iata = (payload.get("dep_iata") or "").strip().upper()
    arr_iata = (payload.get("arr_iata") or "").strip().upper()
    dep_time = (payload.get("dep_time") or "").strip()
    arr_time = (payload.get("arr_time") or "").strip()
    aircraft = (payload.get("aircraft") or "A320").strip()
    freq_days = (payload.get("freq_days") or "1234567").strip()
    econ_price = payload.get("econ_price")
    ratio = payload.get("business_ratio", 2.2)

    if not re.fullmatch(r"[A-Z0-9]{2}[0-9]{3,4}", flight_no):
        return {"error": "航班号格式不正确（如 CA1999：2位航司码+3~4位数字）"}
    import re as _re
    if not _re.fullmatch(r"[1-7]{1,7}", freq_days) or len(set(freq_days)) != len(freq_days):
        return {"error": "执飞日应为1~7的数字组合（周一~周日，不重复），如 1234567"}
    for t, name in ((dep_time, "起飞时间"), (arr_time, "到达时间")):
        if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", t):
            return {"error": f"{name}格式应为 HH:MM"}
    try:
        econ_price = int(econ_price)
        if not (100 <= econ_price <= 20000):
            raise ValueError
    except (TypeError, ValueError):
        return {"error": "经济舱基准价需为 100~20000 的整数"}
    try:
        ratio = float(ratio)
        if not (1.0 <= ratio <= 5.0):
            raise ValueError
    except (TypeError, ValueError):
        return {"error": "商务舱倍率需在 1.0 ~ 5.0 之间"}
    if dep_iata == arr_iata:
        return {"error": "出发与到达机场不能相同"}

    conn = _conn()
    if conn.execute("SELECT 1 FROM flights WHERE flight_no = ?", (flight_no,)).fetchone():
        conn.close()
        return {"error": f"航班号已存在：{flight_no}"}
    for code, name in ((airline_code, "航司"), (dep_iata, "出发机场"), (arr_iata, "到达机场")):
        table = "airlines" if name == "航司" else "airports"
        if not conn.execute(f"SELECT 1 FROM {table} WHERE {'code' if name == '航司' else 'iata3'} = ?",
                            (code,)).fetchone():
            conn.close()
            return {"error": f"{name}不存在：{code}"}

    # 跨天计算时长
    dep_min = int(dep_time[:2]) * 60 + int(dep_time[3:])
    arr_min = int(arr_time[:2]) * 60 + int(arr_time[3:])
    duration = arr_min - dep_min if arr_min > dep_min else arr_min + 24 * 60 - dep_min

    conn.execute(
        "INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, "
        "duration_min, aircraft, freq_days) VALUES (?,?,?,?,?,?,?,?,?)",
        (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, duration, aircraft, freq_days),
    )

    # 生成未来 HORIZON_DAYS 天票价（确定性小幅浮动，商务舱按倍率取整到10）
    rnd = random.Random(f"admin-{flight_no}")
    today = date.today()
    price_rows = []
    for i in range(1, HORIZON_DAYS + 1):
        d = (today + timedelta(days=i)).isoformat()
        swing = rnd.choice((0, 0, 20, -20, 40, -40, 60))
        econ = max(100, econ_price + swing)
        biz = int(round(econ * ratio / 10) * 10)
        price_rows.append((flight_no, d, "经济", econ))
        price_rows.append((flight_no, d, "商务", biz))
    conn.executemany(
        "INSERT OR IGNORE INTO flight_prices (flight_no, flight_date, cabin, price) VALUES (?,?,?,?)",
        price_rows,
    )
    conn.commit()
    conn.close()
    return {"success": True, "flight_no": flight_no, "duration_min": duration,
            "prices_generated": len(price_rows),
            "message": f"航班 {flight_no} 已上架，并生成未来 {HORIZON_DAYS} 天票价"}


# ---------------------------------------------------------------- 机场 / 航司

def list_airports() -> dict:
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT iata3, city_cn, city_en, lat, lon FROM airports ORDER BY iata3").fetchall()]
    conn.close()
    return {"airports": rows, "count": len(rows)}


def create_airport(payload: dict) -> dict:
    iata3 = (payload.get("iata3") or "").strip().upper()
    city_cn = (payload.get("city_cn") or "").strip()
    city_en = (payload.get("city_en") or "").strip()
    try:
        lat, lon = float(payload.get("lat")), float(payload.get("lon"))
    except (TypeError, ValueError):
        return {"error": "经纬度必须是数字"}
    if not re_fullmatch_iata(iata3):
        return {"error": "机场三字码格式不正确（3个大写字母，如 PEK）"}
    if not city_cn or not city_en:
        return {"error": "城市中文名/英文名不能为空"}
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return {"error": "经纬度超出范围"}

    conn = _conn()
    exists = conn.execute(
        "SELECT 1 FROM airports WHERE iata3 = ? OR city_cn = ?", (iata3, city_cn)).fetchone()
    if exists:
        conn.close()
        return {"error": f"机场已存在（三字码或城市名重复）：{iata3} {city_cn}"}
    conn.execute(
        "INSERT INTO airports (iata3, icao4, city_cn, city_en, lat, lon) VALUES (?,?,?,?,?,?)",
        (iata3, None, city_cn, city_en, lat, lon))
    conn.execute("INSERT OR IGNORE INTO city_coords (city, lat, lon) VALUES (?,?,?)",
                 (city_cn, lat, lon))
    conn.commit()
    conn.close()
    return {"success": True, "airport": iata3, "message": f"机场 {city_cn}({iata3}) 已添加"}


def re_fullmatch_iata(code: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Z]{3}", code or ""))


def list_airlines() -> dict:
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT code, name_cn, is_lcc FROM airlines ORDER BY code").fetchall()]
    conn.close()
    return {"airlines": rows, "count": len(rows)}


def create_airline(payload: dict) -> dict:
    code = (payload.get("code") or "").strip().upper()
    name_cn = (payload.get("name_cn") or "").strip()
    is_lcc = 1 if payload.get("is_lcc") else 0
    import re
    if not re.fullmatch(r"[A-Z0-9]{2}", code or ""):
        return {"error": "航司二字码格式不正确（2位字母/数字，如 CA、3U）"}
    if not name_cn:
        return {"error": "航司名称不能为空"}
    conn = _conn()
    if conn.execute("SELECT 1 FROM airlines WHERE code = ?", (code,)).fetchone():
        conn.close()
        return {"error": f"航司已存在：{code}"}
    conn.execute("INSERT INTO airlines (code, name_cn, is_lcc) VALUES (?,?,?)",
                 (code, name_cn, is_lcc))
    conn.commit()
    conn.close()
    return {"success": True, "airline": code, "message": f"航司 {name_cn}({code}) 已添加"}


# ---------------------------------------------------------------- 会员（只读）

def list_customers(q: str = None, limit: int = 200) -> dict:
    conn = _conn()
    sql = ("SELECT member_id, name, phone, email, level FROM customers")
    params = []
    if q:
        sql += " WHERE member_id LIKE ? OR name LIKE ? OR phone LIKE ?"
        like = f"%{q.strip()}%"
        params = [like, like, like]
    sql += " ORDER BY member_id LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return {"customers": rows, "count": len(rows)}
