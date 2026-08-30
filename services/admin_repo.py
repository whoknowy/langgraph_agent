"""
管理端数据层：退款处理、投诉处理、航班/机场/航司维护、订单与会员查询。

调用前提：web 层已通过 services.security.set_current_admin 绑定管理员身份
（enforce_owner 对管理员豁免归属校验）。本模块不做会员归属限制——跨会员
处理正是运营平台的职责。
"""

import random
from datetime import date, timedelta

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
        "today_orders": one("SELECT COUNT(*) FROM orders WHERE created_at = ?", (today,)),
    }
    conn.close()
    return stats


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
    """同意退款：退票中 → 已退款，记录实际退款金额与备注（默认全额）。"""
    from services import flight_repo
    conn = _conn()
    row = conn.execute("SELECT status, amount FROM orders WHERE order_no = ?",
                       (security_normalize(order_no),)).fetchone()
    conn.close()
    if not row:
        return {"error": f"订单不存在：{order_no}"}
    if row["status"] != "退票中":
        return {"error": f"订单状态为「{row['status']}」，只有「退票中」的订单可以退款"}

    amount = int(refund_amount) if refund_amount is not None else int(row["amount"])
    if amount < 0 or amount > int(row["amount"]):
        return {"error": f"退款金额需在 0 ~ {row['amount']} 之间"}

    result = flight_repo._transition_order(order_no, None, "退票中", "已退款")
    if result.get("error"):
        return result
    conn = _conn()
    conn.execute("UPDATE orders SET refund_amount = ?, admin_note = ? WHERE order_no = ?",
                 (amount, (admin_note or "").strip(), security_normalize(order_no)))
    conn.commit()
    conn.close()
    return {"success": True, "order_no": security_normalize(order_no),
            "status": "已退款", "refund_amount": amount,
            "message": f"订单 {security_normalize(order_no)} 已退款 {amount} 元"}


def reject_refund(order_no: str, admin_note: str = "") -> dict:
    """驳回退票：退票中 → 恢复进入审批前的状态（改签单回到「已改签」，其余回「已出票」）。"""
    order_no_n = security_normalize(order_no)
    conn = _conn()
    r = conn.execute("SELECT status, prev_status FROM orders WHERE order_no = ?",
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
    conn.commit()
    conn.close()
    return {"success": True, "order_no": order_no_n, "status": restore,
            "message": f"订单 {order_no_n} 退票申请已驳回，恢复为「{restore}」"}


def security_normalize(v):
    return (v or "").strip().upper()


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
    tn = security_normalize(ticket_no)
    if not (reply or "").strip():
        return {"error": "处理回复不能为空"}
    conn = _conn()
    row = conn.execute("SELECT status FROM complaints WHERE ticket_no = ?", (tn,)).fetchone()
    if not row:
        conn.close()
        return {"error": f"投诉单不存在：{ticket_no}"}
    if row["status"] not in ("处理中", "已升级"):
        conn.close()
        return {"error": f"投诉状态为「{row['status']}」，无法执行解决操作"}
    conn.execute("UPDATE complaints SET status = '已解决', reply = ? WHERE ticket_no = ?",
                 (reply.strip(), tn))
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
    "f.duration_min, f.aircraft, f.freq_days "
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
