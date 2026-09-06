"""值机选座数据层：座位图生成、值机窗口规则、值机/取消值机/登机牌。

设计要点：
- 座位库存按（航班号+日期）首次访问时懒生成：商务 1-3 排（A C | D F）、
  经济 31-55 排（A B C | D E F）；以 random.Random(航班号+日期) 确定性
  预占约 30% 座位，模拟真实航班的已占座位；
- 值机不改动订单状态机（已出票值机后仍是已出票）：值机信息独立存 checkins 表，
  改签/退票时由 flight_repo 自动取消值机并释放座位（延迟导入避免循环依赖）；
- 写路径全部校验登录身份与订单归属；写库不经过 LLM。
"""

import random
from datetime import datetime, timedelta

from services import db, security

BUSINESS_ROWS = range(1, 4)        # 商务舱 1-3 排
BUSINESS_COLS = ("A", "C", "D", "F")
ECON_ROWS = range(31, 56)          # 经济舱 31-55 排
ECON_COLS = ("A", "B", "C", "D", "E", "F")
PREOCCUPY_RATIO = 0.3              # 懒生成时的确定性预占比例

CHECKIN_OPEN_HOURS = 24            # 起飞前 24 小时开放值机
CHECKIN_CLOSE_MINUTES = 45         # 起飞前 45 分钟截止值机
BOARDING_LEAD_MINUTES = 30         # 登机时间 = 起飞前 30 分钟


def _norm(s: str) -> str:
    return (s or "").strip().upper()


# ---------------------------------------------------------------- 座位图

def _layout(cabin: str):
    """返回舱位的 (排号, 列字母) 布局。"""
    if cabin == "商务":
        return BUSINESS_ROWS, BUSINESS_COLS
    return ECON_ROWS, ECON_COLS


def _ensure_seats(conn, flight_no: str, flight_date: str) -> None:
    """座位懒生成（幂等）：首次访问该航班+日期时建满并确定性预占。"""
    flight_no, flight_date = _norm(flight_no), _norm(flight_date)
    n = conn.execute("SELECT COUNT(*) FROM seats WHERE flight_no = ? AND flight_date = ?",
                     (flight_no, flight_date)).fetchone()[0]
    if n:
        return
    rnd = random.Random(f"{flight_no}-{flight_date}")
    rows = []
    for cabin in ("商务", "经济"):
        row_ids, cols = _layout(cabin)
        for r in row_ids:
            for c in cols:
                occupied = rnd.random() < PREOCCUPY_RATIO
                rows.append((flight_no, flight_date, f"{r}{c}", cabin,
                             "occupied" if occupied else "free"))
    conn.executemany(
        "INSERT OR IGNORE INTO seats (flight_no, flight_date, seat_no, cabin, status) "
        "VALUES (?,?,?,?,?)", rows)


def seat_map(flight_no: str, flight_date: str, order_no: str = "") -> dict:
    """座位图：按舱位分区的排×列布局与占用状态。"""
    flight_no, flight_date = _norm(flight_no), _norm(flight_date)
    conn = db.get_connection()
    db.init_schema(conn)
    try:
        if not conn.execute("SELECT 1 FROM flights WHERE flight_no = ?", (flight_no,)).fetchone():
            return {"error": f"航班不存在：{flight_no}"}
        _ensure_seats(conn, flight_no, flight_date)
        rows = conn.execute(
            "SELECT seat_no, cabin, status, order_no FROM seats "
            "WHERE flight_no = ? AND flight_date = ? ORDER BY seat_no",
            (flight_no, flight_date)).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"error": f"航班不存在或无座位数据：{flight_no} {flight_date}"}

    order_no_n = _norm(order_no)
    cabins: dict = {}
    for r in rows:
        cab = cabins.setdefault(r["cabin"], [])
        row_no = "".join(ch for ch in r["seat_no"] if ch.isdigit())
        col = r["seat_no"][len(row_no):]
        row = next((x for x in cab if x["row"] == row_no), None)
        if row is None:
            row = {"row": row_no, "seats": []}
            cab.append(row)
        row["seats"].append({
            "seat_no": r["seat_no"],
            "status": r["status"],
            "mine": bool(order_no_n) and r["order_no"] == order_no_n,
        })
    free = sum(1 for r in rows if r["status"] == "free")
    return {"flight_no": flight_no, "flight_date": flight_date,
            "cabins": cabins, "free": free, "total": len(rows)}


# ---------------------------------------------------------------- 值机窗口

def checkin_window_status(dep: datetime, now: datetime = None) -> tuple:
    """值机窗口纯函数：起飞前 24h 开放、起飞前 45 分钟截止。

    返回 (可否值机, 原因说明)；不可值机时给出人话原因。
    """
    now = now or datetime.now()
    if dep is None:
        return False, "缺少航班起飞时间，无法值机"
    lead = dep - now
    lead_min = lead.total_seconds() / 60
    if lead_min < 0:
        return False, "航班已起飞，值机通道已关闭"
    if lead_min < CHECKIN_CLOSE_MINUTES:
        return False, f"值机已截止（起飞前{CHECKIN_CLOSE_MINUTES}分钟截止）"
    if lead_min > CHECKIN_OPEN_HOURS * 60:
        return False, f"值机尚未开放（起飞前{CHECKIN_OPEN_HOURS}小时开放）"
    return True, ""


def _order_with_departure(order_no: str):
    """查询订单 + 起飞时间；返回 (Row, depart(datetime) 或 None) 或 (None, None)。"""
    conn = db.get_connection()
    r = conn.execute(
        "SELECT o.order_no, o.member_id, o.status, o.cabin, o.flight_no, o.flight_date, "
        "f.dep_time FROM orders o JOIN flights f ON f.flight_no = o.flight_no "
        "WHERE o.order_no = ?", (_norm(order_no),)).fetchone()
    conn.close()
    if not r:
        return None, None
    try:
        dep = datetime.strptime(f"{r['flight_date']} {r['dep_time']}", "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        dep = None
    return r, dep


# ---------------------------------------------------------------- 值机 / 登机牌

def _gate_and_boarding(flight_no: str, flight_date: str, dep: datetime) -> tuple:
    """登机口与登机时间。

    登机口是机场物理资源，与航班绑定：以航班号+日期为种子确定性生成，
    同一航班同一天的所有值机旅客得到同一登机口（零维护，且跨次一致）。
    登机时间 = 起飞前 30 分钟，同样由航班推出。
    """
    rnd = random.Random(f"gate-{_norm(flight_no)}-{_norm(flight_date)}")
    gate = f"{rnd.choice('ABC')}{rnd.randint(1, 32)}"
    boarding = (dep - timedelta(minutes=BOARDING_LEAD_MINUTES)).strftime("%H:%M")
    return gate, boarding


def _get_checkin(conn, order_no: str):
    return conn.execute("SELECT * FROM checkins WHERE order_no = ?", (_norm(order_no),)).fetchone()


def checkin_info(order_no: str, member_id: str = None) -> dict:
    """值机状态查询：订单航班信息、是否已值机、值机窗口是否开放。"""
    order_no = _norm(order_no)
    r, dep = _order_with_departure(order_no)
    if not r:
        return {"error": f"订单不存在：{order_no}"}
    if member_id and _norm(member_id) != _norm(r["member_id"]):
        return {"error": "无权限：只能操作登录会员本人的订单"}
    conn = db.get_connection()
    ck = _get_checkin(conn, order_no)
    conn.close()
    can, reason = checkin_window_status(dep)
    return {
        "order_no": order_no,
        "flight_no": r["flight_no"],
        "flight_date": r["flight_date"],
        "cabin": r["cabin"],
        "order_status": r["status"],
        "checked_in": ck is not None,
        "seat_no": ck["seat_no"] if ck else "",
        "gate": ck["gate"] if ck else "",
        "boarding_time": ck["boarding_time"] if ck else "",
        "depart_time": dep.strftime("%Y-%m-%d %H:%M") if dep else "",
        "window_open": can,
        "window_reason": reason,
    }


def do_checkin(order_no: str, member_id: str, seat_no: str) -> dict:
    """值机/改座：校验窗口与归属，占座（原子）并写入值机记录。

    已值机订单再次值机视为改座：旧座位自动释放。返回登机牌数据。
    """
    order_no, seat_no = _norm(order_no), _norm(seat_no).upper()
    r, dep = _order_with_departure(order_no)
    if not r:
        return {"error": f"订单不存在：{order_no}"}
    if member_id and _norm(member_id) != _norm(r["member_id"]):
        return {"error": "无权限：只能为登录会员本人的订单值机"}
    if r["status"] not in ("已出票", "已改签"):
        return {"error": f"订单状态为「{r['status']}」，只有「已出票/已改签」的订单可以值机"}
    can, reason = checkin_window_status(dep)
    if not can:
        return {"error": reason}
    if not seat_no:
        return {"error": "请先选择座位"}

    conn = db.get_connection()
    db.init_schema(conn)
    try:
        _ensure_seats(conn, r["flight_no"], r["flight_date"])
        seat = conn.execute(
            "SELECT cabin, status, order_no FROM seats "
            "WHERE flight_no = ? AND flight_date = ? AND seat_no = ?",
            (r["flight_no"], r["flight_date"], seat_no)).fetchone()
        if not seat:
            return {"error": f"座位 {seat_no} 不存在（{r['cabin']}舱座位图见选座面板）"}
        if seat["cabin"] != r["cabin"]:
            return {"error": f"座位 {seat_no} 是{seat['cabin']}舱，与订单舱位（{r['cabin']}）不符"}
        old = _get_checkin(conn, order_no)
        if old and old["seat_no"] == seat_no:
            conn.close()
            return _boardpass_payload(order_no, "座位未变化")
        if seat["status"] != "free" and not (old and old["seat_no"] == seat_no):
            return {"error": f"座位 {seat_no} 已被占用，请换一个座位"}

        # 改座：先释放旧座位
        if old:
            conn.execute(
                "UPDATE seats SET status = 'free', order_no = NULL "
                "WHERE flight_no = ? AND flight_date = ? AND seat_no = ? AND order_no = ?",
                (r["flight_no"], r["flight_date"], old["seat_no"], order_no))
        # 原子占座：仅当仍为 free 时生效
        cur = conn.execute(
            "UPDATE seats SET status = 'occupied', order_no = ? "
            "WHERE flight_no = ? AND flight_date = ? AND seat_no = ? AND status = 'free'",
            (order_no, r["flight_no"], r["flight_date"], seat_no))
        if cur.rowcount != 1:
            conn.rollback()
            return {"error": f"座位 {seat_no} 刚刚被其他乘客抢占了，请换一个座位"}
        gate, boarding = _gate_and_boarding(r["flight_no"], r["flight_date"], dep)
        conn.execute(
            "INSERT INTO checkins (order_no, seat_no, gate, boarding_time, checkin_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(order_no) DO UPDATE SET seat_no = excluded.seat_no, "
            "gate = excluded.gate, boarding_time = excluded.boarding_time, "
            "checkin_at = excluded.checkin_at",
            (order_no, seat_no, gate, boarding,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return _boardpass_payload(order_no, "值机成功" if not old else "改座成功",
                                  conn=conn)
    finally:
        conn.close()


def cancel_checkin(order_no: str, member_id: str = None) -> dict:
    """取消值机：释放座位并删除值机记录（退票/改签时由仓库层自动调用，无记录时幂等成功）。"""
    order_no = _norm(order_no)
    if member_id:
        r, _ = _order_with_departure(order_no)
        if not r:
            return {"error": f"订单不存在：{order_no}"}
        if _norm(member_id) != _norm(r["member_id"]):
            return {"error": "无权限：只能操作登录会员本人的订单"}
    conn = db.get_connection()
    try:
        ck = _get_checkin(conn, order_no)
        if not ck:
            return {"success": True, "released": False, "message": "该订单没有值机记录"}
        conn.execute(
            "UPDATE seats SET status = 'free', order_no = NULL "
            "WHERE flight_no = (SELECT flight_no FROM orders WHERE order_no = ?) "
            "AND flight_date = (SELECT flight_date FROM orders WHERE order_no = ?) "
            "AND seat_no = ? AND order_no = ?",
            (order_no, order_no, ck["seat_no"], order_no))
        conn.execute("DELETE FROM checkins WHERE order_no = ?", (order_no,))
        conn.commit()
        return {"success": True, "released": True, "seat_no": ck["seat_no"],
                "message": f"已取消值机，座位 {ck['seat_no']} 已释放"}
    finally:
        conn.close()


def _boardpass_payload(order_no: str, message: str, conn=None) -> dict:
    """组装登机牌数据（复用外部连接或自开）。"""
    own = conn is None
    if own:
        conn = db.get_connection()
    try:
        ck = _get_checkin(conn, order_no)
        row = conn.execute(
            "SELECT o.order_no, o.flight_no, o.flight_date, o.cabin, o.member_id, "
            "c.name AS passenger, a.name_cn AS airline, "
            "f.dep_time, fd.city_cn AS dep_city, fa.city_cn AS arr_city "
            "FROM orders o JOIN customers c ON c.member_id = o.member_id "
            "JOIN flights f ON f.flight_no = o.flight_no "
            "JOIN airlines a ON a.code = f.airline_code "
            "JOIN airports fd ON fd.iata3 = f.dep_iata "
            "JOIN airports fa ON fa.iata3 = f.arr_iata "
            "WHERE o.order_no = ?", (order_no,)).fetchone()
    finally:
        if own:
            conn.close()
    if not ck or not row:
        return {"error": "值机记录异常，请重试"}
    return {
        "success": True,
        "message": message,
        "order_no": order_no,
        "passenger": row["passenger"],
        "member_id": row["member_id"],
        "airline": row["airline"],
        "flight_no": row["flight_no"],
        "route": f"{row['dep_city']} → {row['arr_city']}",
        "flight_date": row["flight_date"],
        "dep_time": row["dep_time"],
        "cabin": row["cabin"],
        "seat_no": ck["seat_no"],
        "gate": ck["gate"],
        "boarding_time": ck["boarding_time"],
    }


def get_boarding_pass(order_no: str, member_id: str = None) -> dict:
    """登机牌查询（已值机订单）。"""
    order_no = _norm(order_no)
    if member_id:
        r, _ = _order_with_departure(order_no)
        if not r:
            return {"error": f"订单不存在：{order_no}"}
        if _norm(member_id) != _norm(r["member_id"]):
            return {"error": "无权限：只能查看登录会员本人的登机牌"}
    conn = db.get_connection()
    try:
        if not _get_checkin(conn, order_no):
            return {"error": f"订单 {order_no} 尚未值机"}
        return _boardpass_payload(order_no, "登机牌")
    finally:
        conn.close()
