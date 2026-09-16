"""
航班数据查询层（Repository）：供 function-calling 工具调用。

所有函数返回纯 dict/list，工具层负责包装成功/失败；本层不抛业务异常，
查不到时返回空结果，参数非法时返回 {"error": ...} 结构由调用方决定。
"""

import sqlite3
from datetime import date, datetime, timedelta

from services import audit, db, security
from services.db_seed import HORIZON_DAYS


def _conn() -> sqlite3.Connection:
    conn = db.get_connection()
    db.init_schema(conn)
    return conn


# ---------------------------------------------------------------- 城市/机场解析

def resolve_city(name: str) -> str:
    """把城市名（或 IATA 码）解析为城市中文名；失败返回 None。"""
    if not name:
        return None
    s = str(name).strip().replace("市", "")
    conn = _conn()
    row = conn.execute(
        "SELECT city_cn FROM airports WHERE city_cn = ? OR city_en = ? OR iata3 = ?",
        (s, s, s.upper()),
    ).fetchone()
    conn.close()
    return row["city_cn"] if row else None


def city_to_iata(city: str) -> str:
    """城市中文名 → IATA 码（支持直接传 IATA）。"""
    if not city:
        return None
    s = str(city).strip().replace("市", "")
    conn = _conn()
    row = conn.execute("SELECT iata3, city_cn FROM airports WHERE iata3 = ? OR city_cn = ?",
                       (s.upper(), s)).fetchone()
    conn.close()
    return row["iata3"] if row else None


# ---------------------------------------------------------------- 航班搜索

def is_upcoming(dep_time: str, flight_date: date, now: datetime = None) -> bool:
    """班次在 now 时刻是否**还没起飞**（纯函数，便于单测）。

    - 未来日期：恒为 True（起飞时刻尚未到）；
    - 过去日期：恒为 False；
    - 当天：按 HH:MM 字符串比较（零填充，字典序即时间先后）。
    """
    now = now or datetime.now()
    if flight_date > now.date():
        return True
    if flight_date < now.date():
        return False
    return str(dep_time or "") > now.strftime("%H:%M")


def search_flights(departure: str, destination: str, date_str: str = None) -> dict:
    """按出发/目的地（城市名或 IATA）查航班；date_str 为空时给出 30 天价格区间。

    只返回**尚未起飞**的航班：
    - 过去日期直接拒绝（不出售已过期航班）；
    - 指定当天时，剔除起飞时刻已经过去的班次；
    - 不指定日期时，价格区间只统计今天及以后（历史价格档期不参与）。
    """
    dep_iata = city_to_iata(departure)
    arr_iata = city_to_iata(destination)
    if not dep_iata or not arr_iata:
        return {"error": f"暂不支持该航线：{departure} → {destination}"}

    today = date.today()
    conn = _conn()
    base_sql = (
        "SELECT f.flight_no, a.name_cn AS airline, f.dep_time, f.arr_time, f.aircraft, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city "
        "FROM flights f "
        "JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata "
        "JOIN airports fa ON fa.iata3 = f.arr_iata "
        "WHERE f.dep_iata = ? AND f.arr_iata = ?"
    )

    if date_str:
        d = _normalize_date(date_str)
        if not d:
            conn.close()
            return {"error": f"日期格式无效：{date_str}"}
        if d < today:
            conn.close()
            return {"error": f"不能查询过去日期的航班：{date_str} 已过期，"
                             f"仅支持今天（{today.isoformat()}）及以后"}
        weekday_cn = str(d.isoweekday())
        rows = conn.execute(
            base_sql + " AND instr(f.freq_days, ?) > 0 ORDER BY f.dep_time",
            (dep_iata, arr_iata, weekday_cn),
        ).fetchall()
        # 当天：剔除已经起飞的班次（飞走了就查不到、也订不了）
        departed = 0
        if d == today:
            now = datetime.now()
            remain = [r for r in rows if is_upcoming(r["dep_time"], d, now)]
            departed = len(rows) - len(remain)
            rows = remain
        if not rows:
            conn.close()
            if departed:
                return {"error": f"今天（{d.isoformat()}）{departure}→{destination} 的 "
                                 f"{departed} 个航班均已起飞，请查询明天及以后的航班"}
            return {"error": f"{date_str} 当天无 {departure}→{destination} 航班"}
        flights = []
        for r in rows:
            prices = conn.execute(
                "SELECT cabin, price FROM flight_prices WHERE flight_no = ? AND flight_date = ?",
                (r["flight_no"], d.isoformat()),
            ).fetchall()
            price_map = {p["cabin"]: p["price"] for p in prices}
            if not price_map:
                continue
            flights.append({
                "flight_no": r["flight_no"], "airline": r["airline"],
                "dep_time": r["dep_time"], "arr_time": r["arr_time"],
                "aircraft": r["aircraft"], "date": d.isoformat(),
                "prices": price_map,
            })
        conn.close()
        return {"departure": departure, "destination": destination, "date": d.isoformat(),
                "flights": flights, "count": len(flights)}

    rows = conn.execute(
        base_sql + " ORDER BY f.dep_time",
        (dep_iata, arr_iata),
    ).fetchall()
    flights = []
    for r in rows:
        # 价格区间只统计今天及以后：只剩历史价格的班次不再对外返回
        price_row = conn.execute(
            "SELECT MIN(CASE WHEN cabin='经济' THEN price END) AS min_p, "
            "MAX(CASE WHEN cabin='商务' THEN price END) AS max_p, "
            "MIN(flight_date) AS d_from, MAX(flight_date) AS d_to "
            "FROM flight_prices WHERE flight_no = ? AND flight_date >= ?",
            (r["flight_no"], today.isoformat()),
        ).fetchone()
        if price_row["min_p"] is None and price_row["max_p"] is None:
            continue
        flights.append({
            "flight_no": r["flight_no"], "airline": r["airline"],
            "dep_time": r["dep_time"], "arr_time": r["arr_time"],
            "aircraft": r["aircraft"],
            "price_range": {
                "min": price_row["min_p"], "max": price_row["max_p"],
                "from": price_row["d_from"], "to": price_row["d_to"],
            },
        })
    conn.close()
    return {"departure": departure, "destination": destination, "flight_count": len(flights),
            "flights": flights}


# ---------------------------------------------------------------- 价格趋势

def get_price_trend(from_city: str, to_city: str, days_ahead: int = None) -> dict:
    """航线近 N 天每日最低/最高价（来自 flight_prices 真实日期序列）。"""
    dep_iata = city_to_iata(from_city)
    arr_iata = city_to_iata(to_city)
    if not dep_iata or not arr_iata:
        return {"error": f"暂不支持该航线：{from_city} → {to_city}"}
    days = days_ahead or HORIZON_DAYS
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=min(days, 60) - 1)
    conn = _conn()
    rows = conn.execute(
        "SELECT p.flight_date, MIN(CASE WHEN p.cabin='经济' THEN p.price END) AS min_price, "
        "MAX(p.price) AS max_price "
        "FROM flight_prices p JOIN flights f ON f.flight_no = p.flight_no "
        "WHERE f.dep_iata = ? AND f.arr_iata = ? AND p.flight_date BETWEEN ? AND ? "
        "GROUP BY p.flight_date ORDER BY p.flight_date",
        (dep_iata, arr_iata, start.isoformat(), end.isoformat()),
    ).fetchall()
    conn.close()
    if not rows:
        return {"error": f"无 {from_city}→{to_city} 未来价格数据"}
    trend = [{"date": r["flight_date"], "min_price": r["min_price"], "max_price": r["max_price"]}
             for r in rows]
    # 简单趋势判定
    prices = [t["min_price"] for t in trend if t["min_price"]]
    current = prices[0] if prices else 0
    upcoming = prices[-1] if prices else 0
    direction = "上涨" if upcoming > current else ("下跌" if upcoming < current else "持平")
    return {"route": f"{from_city}-{to_city}", "trend": trend,
            "summary": {"current_min": current, "end_min": upcoming, "direction": direction}}


# ---------------------------------------------------------------- 延误预测

def get_delay_prediction(route_str: str = None, airline: str = None,
                         from_city: str = None, to_city: str = None) -> dict:
    """按航司×航线×时段的历史统计口径给出延误概率与平均延误时长。"""
    if route_str:
        parts = route_str.replace("—", "-").split("-")
        if len(parts) == 2:
            from_city, to_city = parts[0].strip(), parts[1].strip()
    dep_iata = city_to_iata(from_city) if from_city else None
    arr_iata = city_to_iata(to_city) if to_city else None
    if not dep_iata or not arr_iata:
        return {"error": f"航线参数需为 城市-城市 或城市名对：{route_str}"}
    route_key = f"{dep_iata}-{arr_iata}"
    conn = _conn()
    sql = ("SELECT airline_code, time_bucket, mean_delay_min, delay_prob, sample_size "
           "FROM delay_stats WHERE route = ?")
    params = [route_key]
    if airline:
        sql += " AND airline_code = ?"
        params.append(airline)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        return {"error": f"无该航线延误统计：{from_city}-{to_city}"}
    total_n = sum(r["sample_size"] for r in rows)
    overall_prob = sum(r["delay_prob"] * r["sample_size"] for r in rows) / total_n
    overall_min = sum(r["mean_delay_min"] * r["sample_size"] for r in rows) / total_n
    bucket_names = {"morning": "早间(06-12)", "afternoon": "午后(12-18)", "evening": "晚间(18-24)"}
    by_time = [{"time_bucket": bucket_names.get(r["time_bucket"], r["time_bucket"]),
                "delay_prob": round(r["delay_prob"], 3),
                "mean_delay_min": round(r["mean_delay_min"], 1)}
               for r in rows]
    return {
        "route": f"{from_city}-{to_city}",
        "airline": airline or "全部航司",
        "overall": {"delay_prob": round(overall_prob, 3),
                    "mean_delay_min": round(overall_min, 1),
                    "sample_days": total_n},
        "by_time": by_time,
    }


# ---------------------------------------------------------------- 价格构成

def get_flight_price_detail(flight_no: str, date_str: str) -> dict:
    """机票价格构成明细（金额相加≈总价）。"""
    d = _normalize_date(date_str)
    if not flight_no or not d:
        return {"error": "需要航班号与日期 YYYY-MM-DD"}
    conn = _conn()
    frow = conn.execute(
        "SELECT f.flight_no, a.name_cn AS airline, f.duration_min, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city, f.dep_time "
        "FROM flights f JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata JOIN airports fa ON fa.iata3 = f.arr_iata "
        "WHERE f.flight_no = ?", (flight_no,),
    ).fetchone()
    if not frow:
        conn.close()
        return {"error": f"航班不存在：{flight_no}"}
    prices = conn.execute(
        "SELECT cabin, price FROM flight_prices WHERE flight_no = ? AND flight_date = ?",
        (flight_no, d.isoformat()),
    ).fetchall()
    conn.close()
    if not prices:
        return {"error": f"{flight_no} 在 {date_str} 无在售价格"}
    breakdown = {}
    for p in prices:
        price = p["price"]
        base = int(round(price * 0.70 / 10) * 10)
        fuel = 80 + (frow["duration_min"] // 60) * 20
        airport_fee = 50
        insurance = 30
        service = price - base - fuel - airport_fee
        breakdown[p["cabin"]] = {
            "total": price,
            "components": {
                "基础票价": base,
                "燃油附加费": fuel,
                "机场建设费": airport_fee,
                "服务费": service,
                "保险费(可选)": insurance,
            },
        }
    return {
        "flight_no": flight_no, "airline": frow["airline"],
        "route": f"{frow['dep_city']}-{frow['arr_city']}", "date": d.isoformat(),
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------- 订单/账单

def get_order_bill(member_id: str = None, order_no: str = None) -> dict:
    """按会员/订单号查账单（orders × flights × airlines）。

    硬校验：member_id 或订单归属必须与登录身份一致（防 LLM 越权传参）。
    """
    if not member_id and not order_no:
        return {"error": "需要提供会员号或订单号"}
    denied = security.enforce_owner(member_id, action="查询")
    if denied:
        return denied
    conn = _conn()
    if order_no and not security.normalize(member_id):
        row = conn.execute("SELECT member_id FROM orders WHERE order_no = ?",
                           (security.normalize(order_no),)).fetchone()
        conn.close()
        if not row:
            return {"error": f"订单不存在：{order_no}"}
        denied = security.enforce_owner(row["member_id"], action="查询")
        if denied:
            return denied
        conn = _conn()
    sql = (
        "SELECT o.order_no, o.member_id, o.flight_no, o.flight_date, o.cabin, o.amount, "
        "o.status, o.created_at, o.refund_amount, o.admin_note, a.name_cn AS airline, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city, f.dep_time, "
        "ck.seat_no AS checkin_seat, ck.gate AS checkin_gate, ck.boarding_time "
        "FROM orders o "
        "JOIN flights f ON f.flight_no = o.flight_no "
        "JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata "
        "JOIN airports fa ON fa.iata3 = f.arr_iata "
        "LEFT JOIN checkins ck ON ck.order_no = o.order_no "
        "WHERE 1=1"
    )
    params = []
    if member_id:
        sql += " AND o.member_id = ?"
        params.append(member_id)
    if order_no:
        sql += " AND o.order_no = ?"
        params.append(order_no)
    sql += " ORDER BY o.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    # 未支付的改签差价：订单页要能提示并让用户「继续支付」，否则用户中途放弃就再也付不了
    pending = {}
    if rows:
        marks = ",".join("?" for _ in rows)
        for p in conn.execute(
                f"SELECT * FROM change_requests WHERE status = ? AND order_no IN ({marks})",
                (CHANGE_PENDING_STATUS, *[r["order_no"] for r in rows])).fetchall():
            row_p = dict(p)
            # 差价支付已超时作废的不再提示「继续支付」（否则点进去只会又跳一次收银台）
            if _change_payment_expired(row_p):
                continue
            pending[row_p["order_no"]] = row_p
    conn.close()
    if not rows:
        return {"error": "未找到该会员/订单的账单记录"}
    orders = []
    for r in rows:
        pc = pending.get(r["order_no"])
        orders.append({
            "order_no": r["order_no"], "member_id": r["member_id"],
            "flight": f"{r['airline']}{r['flight_no']}",
            "flight_no": r["flight_no"],
            "route": f"{r['dep_city']}-{r['arr_city']}",
            "flight_date": r["flight_date"], "dep_time": r["dep_time"],
            "cabin": r["cabin"], "amount": r["amount"],
            "refund_amount": r["refund_amount"],
            "status": r["status"], "created_at": r["created_at"],
            "checked_in": r["checkin_seat"] is not None,
            "checkin_seat": r["checkin_seat"] or "",
            "checkin_gate": r["checkin_gate"] or "",
            "boarding_time": r["boarding_time"] or "",
            # 有未支付的改签差价 → 前端提示并可「继续支付差价」
            "change_pending": bool(pc),
            "change_diff": int(pc["fare_diff"]) if pc else 0,
            "change_target": (f"{pc['new_flight_no']} {pc['new_date']}" if pc else ""),
        })
    return {"member_id": member_id, "orders": orders, "count": len(orders),
            "total_amount": sum(o["amount"] for o in orders)}


# ---------------------------------------------------------------- 投诉

def query_complaints(member_id: str = None, ticket_no: str = None) -> dict:
    """查询投诉记录。硬校验：只能查登录会员本人的投诉。"""
    denied = security.enforce_owner(member_id, action="查询")
    if denied:
        return denied
    conn = _conn()
    if ticket_no and not security.normalize(member_id):
        row = conn.execute("SELECT member_id FROM complaints WHERE ticket_no = ?",
                           (security.normalize(ticket_no),)).fetchone()
        conn.close()
        if not row:
            return {"error": "未找到相关投诉记录"}
        denied = security.enforce_owner(row["member_id"], action="查询")
        if denied:
            return denied
        conn = _conn()
    sql = "SELECT * FROM complaints WHERE 1=1"
    params = []
    if member_id:
        sql += " AND member_id = ?"
        params.append(member_id)
    if ticket_no:
        sql += " AND ticket_no = ?"
        params.append(ticket_no)
    sql += " ORDER BY created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        return {"error": "未找到相关投诉记录"}
    return {"complaints": [dict(r) for r in rows], "count": len(rows)}

def create_complaint(member_id: str = None, order_no: str = None, content: str = None) -> dict:
    """新增投诉记录（状态"处理中"，单号接续 T 序号自增）。

    member_id 与 order_no 至少提供一个，便于关联到具体客户/订单。
    """
    if not content or not str(content).strip():
        return {"error": "投诉内容不能为空"}

    # 硬校验：投诉只能登记在登录会员名下；未指明时默认取登录身份
    login_id = security.get_current_member()
    if not login_id:
        return {"error": "未登录：请先登录会员账号后再登记投诉"}
    if member_id:
        denied = security.enforce_owner(member_id, action="登记")
        if denied:
            return denied
    else:
        member_id = login_id

    if not order_no and not member_id:
        return {"error": "需要提供会员号（M开头）或订单号（O开头）之一才能登记投诉"}

    conn = _conn()
    if member_id:
        row = conn.execute("SELECT 1 FROM customers WHERE member_id = ?", (member_id,)).fetchone()
        if not row:
            conn.close()
            return {"error": f"会员号 {member_id} 不存在"}
    if order_no:
        row = conn.execute("SELECT member_id FROM orders WHERE order_no = ?", (order_no,)).fetchone()
        if not row:
            conn.close()
            return {"error": f"订单号 {order_no} 不存在"}
        if security.normalize(row["member_id"]) != security.normalize(member_id):
            conn.close()
            return {"error": f"无权限：订单 {order_no} 不属于登录会员（{security.get_current_member()}）"}

    max_row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(ticket_no, 2) AS INTEGER)) AS m FROM complaints WHERE ticket_no LIKE 'T%'"
    ).fetchone()
    next_no = (max_row["m"] or 1000) + 1
    ticket_no = f"T{next_no}"

    from skills import mask_sensitive  # 懒导入：避免 skills→agents→tools→flight_repo 装载环
    conn.execute(
        "INSERT INTO complaints (ticket_no, member_id, order_no, content, status, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticket_no, member_id, order_no, mask_sensitive(str(content).strip()), "处理中", date.today().isoformat()),
    )
    conn.commit()
    conn.close()
    return {"success": True, "ticket_no": ticket_no, "status": "处理中",
            "message": f"投诉已登记，投诉单号 {ticket_no}，我们将在24小时内跟进处理"}


# ---------------------------------------------------------------- 会员/订票

def get_customer(member_id: str) -> dict:
    """查询会员信息（登录校验与身份注入用）。"""
    conn = _conn()
    r = conn.execute("SELECT member_id, name, phone, email, level FROM customers WHERE member_id = ?",
                     ((member_id or "").strip().upper(),)).fetchone()
    conn.close()
    return dict(r) if r else {"error": f"会员不存在：{member_id}"}


def find_customer_by_account(account: str) -> dict:
    """按会员号或手机号查找会员（密码登录用）。"""
    acc = (account or "").strip()
    if not acc:
        return {"error": "请输入会员号或手机号"}
    conn = _conn()
    r = (conn.execute("SELECT member_id, name, phone, email, level, password_hash FROM customers "
                      "WHERE member_id = ? OR phone = ?", (acc.upper(), acc)).fetchone())
    conn.close()
    return dict(r) if r else {"error": "账号不存在，请核对会员号或手机号"}


def register_member(name: str, phone: str, password: str, email: str = "") -> dict:
    """注册新会员：手机号唯一，口令哈希存储（与 admins 同策略），返回新会员档案。"""
    from werkzeug.security import generate_password_hash
    name = (name or "").strip()
    phone = (phone or "").strip()
    email = (email or "").strip()
    if not name or len(name) > 20:
        return {"error": "请输入姓名（20字以内）"}
    if not _is_valid_phone(phone):
        return {"error": "请输入11位手机号"}
    if not password or len(password) < 6:
        return {"error": "密码至少6位"}
    if email and ("@" not in email or len(email) > 50):
        return {"error": "邮箱格式不正确"}

    conn = _conn()
    try:
        if conn.execute("SELECT 1 FROM customers WHERE phone = ?", (phone,)).fetchone():
            return {"error": "该手机号已注册，请直接登录"}
        row = conn.execute(
            "SELECT MAX(CAST(SUBSTR(member_id, 2) AS INTEGER)) AS n FROM customers "
            "WHERE member_id LIKE 'M%'").fetchone()
        next_no = (row["n"] or 1000) + 1
        member_id = f"M{next_no}"
        conn.execute(
            "INSERT INTO customers (member_id, name, phone, email, level, password_hash) "
            "VALUES (?,?,?,?,?,?)",
            (member_id, name, phone, email or None, "普卡", generate_password_hash(password)))
        conn.commit()
        return {"member_id": member_id, "name": name, "phone": phone, "level": "普卡"}
    finally:
        conn.close()


def verify_member_password(account: str, password: str) -> dict:
    """密码登录校验：账号为会员号或手机号。仅对设置了密码的账户可用。"""
    from werkzeug.security import check_password_hash
    cust = find_customer_by_account(account)
    if cust.get("error"):
        return cust
    if not cust.get("password_hash"):
        return {"error": "该账户未设置密码，请使用会员号+手机尾号登录"}
    if not check_password_hash(cust["password_hash"], password or ""):
        return {"error": "密码不正确"}
    return {"member_id": cust["member_id"], "name": cust["name"], "level": cust["level"]}


def _is_valid_phone(phone: str) -> bool:
    import re
    return bool(re.fullmatch(r"1\d{10}", phone or ""))


def list_demo_accounts(limit: int = 3) -> list:
    """演示账号（登录页一键填入）：手机号仅返回后4位。"""
    conn = _conn()
    rows = conn.execute("SELECT member_id, name, phone, level FROM customers ORDER BY member_id LIMIT ?",
                        (int(limit),)).fetchall()
    conn.close()
    return [{"member_id": r["member_id"], "name": r["name"],
             "phone_suffix": r["phone"][-4:], "level": r["level"]} for r in rows]


def booking_quote(flight_no: str, flight_date: str, cabin: str, passengers: int = 1) -> dict:
    """订票报价：按航班+日期+舱位取实时票价，返回单价与总价。"""
    d = _normalize_date(flight_date)
    if not flight_no or not d:
        return {"error": "需要航班号与日期 YYYY-MM-DD"}
    cabin = (cabin or "").strip()
    if cabin not in ("经济", "商务"):
        return {"error": "舱位仅支持：经济 / 商务"}
    try:
        passengers = int(passengers)
    except (TypeError, ValueError):
        return {"error": "人数必须是整数"}
    if not 1 <= passengers <= 9:
        return {"error": "人数需在 1-9 之间"}

    conn = _conn()
    frow = conn.execute(
        "SELECT f.flight_no, a.name_cn AS airline, f.dep_time, f.arr_time, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city "
        "FROM flights f JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata JOIN airports fa ON fa.iata3 = f.arr_iata "
        "WHERE f.flight_no = ?", (flight_no.strip().upper(),)).fetchone()
    if not frow:
        conn.close()
        return {"error": f"航班不存在：{flight_no}"}
    prow = conn.execute(
        "SELECT price FROM flight_prices WHERE flight_no = ? AND flight_date = ? AND cabin = ?",
        (frow["flight_no"], d.isoformat(), cabin)).fetchone()
    conn.close()
    if not prow:
        return {"error": f"{flight_no} 在 {d.isoformat()} 无 {cabin}舱 在售票价"}

    unit = int(prow["price"])
    # 字段尽量拆开给前端（确认卡片要按「航司 / 航线 / 起降时间 / 舱位 / 票价」分块展示，
    # 不想让前端去 split(route) 猜城市）
    return {"flight_no": frow["flight_no"], "airline": frow["airline"],
            "route": f"{frow['dep_city']}-{frow['arr_city']}",
            "dep_city": frow["dep_city"], "arr_city": frow["arr_city"],
            "dep_time": frow["dep_time"], "arr_time": frow["arr_time"],
            "flight_date": d.isoformat(), "cabin": cabin, "passengers": passengers,
            "unit_price": unit, "total_amount": unit * passengers}


def _generate_order_no(conn: sqlite3.Connection) -> str:
    """生成唯一订单号：O + 7位随机数字（与种子数据格式一致）。"""
    import random
    for _ in range(20):
        no = "O" + "".join(random.choice("0123456789") for _ in range(7))
        if not conn.execute("SELECT 1 FROM orders WHERE order_no = ?", (no,)).fetchone():
            return no
    raise RuntimeError("订单号生成失败")


def book_flight(member_id: str, flight_no: str, flight_date: str, cabin: str, passengers: int = 1) -> dict:
    """创建订单（状态"待支付"）。member_id 必须为已登录会员。"""
    if not member_id:
        return {"error": "需要登录会员身份才能订票"}
    denied = security.enforce_owner(member_id, action="订票")
    if denied:
        return denied
    quote = booking_quote(flight_no, flight_date, cabin, passengers)
    if quote.get("error"):
        return quote

    conn = _conn()
    cust = conn.execute("SELECT 1 FROM customers WHERE member_id = ?", (member_id,)).fetchone()
    if not cust:
        conn.close()
        return {"error": f"会员号 {member_id} 不存在"}
    order_no = _generate_order_no(conn)
    conn.execute(
        "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, status, created_at, passengers) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (order_no, member_id, quote["flight_no"], quote["flight_date"], quote["cabin"],
         quote["total_amount"], "待支付", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), quote["passengers"]),
    )
    conn.commit()
    conn.close()
    return {"success": True, "order_no": order_no, **quote, "status": "待支付",
            "message": f"订单 {order_no} 已创建（待支付），金额 {quote['total_amount']} 元"}


def _transition_order(order_no: str, member_id: str, from_status, to_status: str) -> dict:
    """订单状态流转（校验归属与前置状态）。

    归属校验**从订单反查**再用受信通道比对，不信任调用方传入的 member_id；
    更新语句带状态守卫（`AND status IN (...)`）保证原子，避免并发下重复流转。

    from_status 可传 str 或 tuple；进入「退票中」时自动把来源状态记入 prev_status，
    供管理端驳回后恢复（改签单驳回应回到「已改签」而不是「已出票」）。
    """
    from_list = (from_status,) if isinstance(from_status, str) else tuple(from_status)
    order_no = (order_no or "").strip().upper()
    conn = _conn()
    try:
        r = conn.execute("SELECT member_id, status FROM orders WHERE order_no = ?",
                         (order_no,)).fetchone()
        if not r:
            return {"error": f"订单不存在：{order_no}"}
        denied = security.enforce_owner(r["member_id"], action="操作")
        if denied:
            audit.denied(to_status, denied["error"], target=order_no)
            return denied
        if r["status"] not in from_list:
            return {"error": f"订单状态为「{r['status']}」，无法执行该操作（需为「{'/'.join(from_list)}」）"}
        marks = ",".join("?" for _ in from_list)
        if to_status == "退票中":
            cur = conn.execute(
                f"UPDATE orders SET status = ?, prev_status = ? WHERE order_no = ? AND status IN ({marks})",
                (to_status, r["status"], order_no, *from_list))
        else:
            cur = conn.execute(
                f"UPDATE orders SET status = ? WHERE order_no = ? AND status IN ({marks})",
                (to_status, order_no, *from_list))
        if cur.rowcount != 1:
            conn.rollback()
            return {"error": f"订单 {order_no} 状态刚刚发生变化，操作未生效，请刷新后重试"}
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "order_no": order_no, "status": to_status,
            "message": f"订单 {order_no} 状态已更新为「{to_status}」"}


def pay_order(order_no: str, member_id: str = None) -> dict:
    """支付订单：待支付 → 已出票。"""
    return _transition_order(order_no, member_id, "待支付", "已出票")


def _record_refund(conn, request_id, order_no, member_id, refund_type, amount, fee, status,
                   channel=None) -> bool:
    """写退款流水（request_id 为幂等键），**同一请求号重复写入时更新为本次结果**。

    为什么要 upsert：一次退款可能被尝试多次（渠道失败后重试、超时后对账），
    同一个 request_id 必须只留一行、且最终反映真实结果——否则重试成功后
    流水里还留着上一次的「退款失败」，对账会看糊涂。

    `channel` 是渠道退款结果（services/payment_service.refund_payment 的返回），
    落库后即便流程中断，也能凭 out_request_no 去支付宝对账。
    """
    if not request_id:
        return True
    ch = channel or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 13 项，顺序与下面 INSERT/UPDATE 的列一致（第 7 项在 INSERT 里是 created_at、
    # 在 UPDATE 里是 updated_at —— 重试不覆盖首次尝试时间）
    vals = (order_no, member_id or "", refund_type, int(amount or 0), int(fee or 0),
            status, now, ch.get("pay_no"), ch.get("out_request_no") or request_id,
            ch.get("channel"), ch.get("channel_status"), ch.get("channel_error"),
            ch.get("channel_raw"))
    cur = conn.execute(
        "INSERT OR IGNORE INTO refunds (request_id, order_no, member_id, refund_type, "
        "amount, fee, status, created_at, pay_no, out_request_no, channel, "
        "channel_status, channel_error, channel_raw, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, *vals, now))
    if cur.rowcount == 1:
        return True
    conn.execute(
        "UPDATE refunds SET order_no = ?, member_id = ?, refund_type = ?, amount = ?, "
        "fee = ?, status = ?, updated_at = ?, pay_no = ?, out_request_no = ?, channel = ?, "
        "channel_status = ?, channel_error = ?, channel_raw = ? WHERE request_id = ?",
        (*vals, request_id))
    return False


def attach_refund_channel(request_id, channel, status: str = None, create: dict = None) -> bool:
    """把渠道退款结果补写到退款流水上（按 request_id 定位）。

    两种用法：
    - 特殊退票的审批场景：用户提交时先落了「退票中」流水，审批通过、渠道退款成功后
      再补写渠道结果（`create` 传 None）；
    - 兜底场景：提交时没带 requestId（历史上允许），审批时才生成，
      此时行不存在 → 用 `create`（order_no/member_id/refund_type/amount/fee）补插一行。

    返回是否写成功。写不成功不阻断主流程，但调用方应感知（对账会缺一条线索）。
    """
    if not request_id:
        return False
    ch = channel or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE refunds SET pay_no = ?, out_request_no = ?, channel = ?, "
            "channel_status = ?, channel_error = ?, channel_raw = ?, updated_at = ?"
            + (", status = ?" if status else "")
            + " WHERE request_id = ?",
            [ch.get("pay_no"), ch.get("out_request_no") or request_id, ch.get("channel"),
             ch.get("channel_status"), ch.get("channel_error"), ch.get("channel_raw"), now]
            + ([status] if status else []) + [request_id])
        if cur.rowcount != 1 and create:
            cur = conn.execute(
                "INSERT OR IGNORE INTO refunds (request_id, order_no, member_id, refund_type, "
                "amount, fee, status, created_at, pay_no, out_request_no, channel, "
                "channel_status, channel_error, channel_raw, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, create.get("order_no"), create.get("member_id") or "",
                 create.get("refund_type") or "special",
                 int(create.get("amount") or 0), int(create.get("fee") or 0),
                 status or create.get("status") or "已退款", now,
                 ch.get("pay_no"), ch.get("out_request_no") or request_id, ch.get("channel"),
                 ch.get("channel_status"), ch.get("channel_error"), ch.get("channel_raw"), now))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_pending_refund(order_no: str, refund_type: str = None):
    """取该订单最新的待审批退款流水（特殊退票走审批时用它拿渠道幂等键）。"""
    conn = _conn()
    try:
        sql = ("SELECT * FROM refunds WHERE order_no = ? AND status = ? "
               + ("AND refund_type = ? " if refund_type else "")
               + "ORDER BY id DESC LIMIT 1")
        params = [security.normalize(order_no), "退票中"]
        if refund_type:
            params.append(refund_type)
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# 未定论的退款状态：钱可能已经出去了，但订单状态还没跟上。
# 只要有这种流水存在，就不允许对该订单再发起新的退款（防重复退款）。
# - 退款待对账：渠道结果未知（网络超时），必须先用退款对账接口问清楚；
# - 渠道已退款：渠道确认钱退了，但订单没推进，等管理端「确认退款完成」收口。
OPEN_REFUND_STATUSES = ("退款待对账", "渠道已退款")

# 已定论（钱已动过）的退款状态：同一 requestId 重放直接返回首次结果。
# 注意 `退票中`（等审批）、`退款失败`（没退成）、`退款待对账`（结果未知）都不在内。
SETTLED_REFUND_STATUSES = ("已退款", "渠道已退款", "线下退款")
PENDING_REFUND_STATUS = "退款待对账"


def find_failed_refund(order_no: str):
    """取该订单最近一条**渠道明确失败**的退款流水。

    用途：线下退款收口的准入判断——只有"渠道真的退不了"（如支付宝查无此交易）
    才允许运营走线下退款，不能凭空空手把订单标成已退款。
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM refunds WHERE order_no = ? AND status = ? AND channel_status = ? "
            "ORDER BY id DESC LIMIT 1",
            (security.normalize(order_no), "退款失败", "失败")).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_open_refund(order_no: str, exclude_request_id: str = None):
    """取该订单未定论的退款流水（退款待对账 / 渠道已退款），没有则 None。

    用于退款入口的「先查未定论退款、再决定是否动渠道」——这是「重复退款」
    在订单状态守卫之外的第二道闸门：网络超时导致的未知结果也必须留下痕迹，
    否则客户端换个 requestId 重试就会真的退第二笔。
    """
    conn = _conn()
    try:
        marks = ",".join("?" for _ in OPEN_REFUND_STATUSES)
        params = [security.normalize(order_no), *OPEN_REFUND_STATUSES]
        sql = f"SELECT * FROM refunds WHERE order_no = ? AND status IN ({marks})"
        if exclude_request_id:
            sql += " AND request_id != ?"
            params.append(exclude_request_id.strip())
        sql += " ORDER BY id DESC LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_refund_by_request(request_id: str):
    """按幂等键取退款流水（用于识别重复请求）。"""
    if not request_id:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM refunds WHERE request_id = ?",
                           (str(request_id).strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _refund_order_member(order_no: str):
    """取订单归属会员（幂等响应/流水记录用）。"""
    conn = _conn()
    try:
        row = conn.execute("SELECT member_id, status, amount FROM orders WHERE order_no = ?",
                           (security.normalize(order_no),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def refund_order(order_no: str, member_id: str = None, request_id: str = None) -> dict:
    """特殊退票（非自愿：延误/取消等）：已出票/已改签 → 退票中，进入管理端审批队列。

    request_id 幂等：同一请求重放直接返回首次结果，不重复流转订单。
    """
    order_no = security.normalize(order_no)
    if request_id:
        existing = get_refund_by_request(request_id)
        if existing:
            audit.idempotent("退票(特殊)", target=existing["order_no"], request_id=request_id)
            return {"success": True, "idempotent": True, "order_no": existing["order_no"],
                    "status": existing["status"],
                    "message": f"该退票请求已受理（{existing['status']}），无需重复提交"}
    result = _transition_order(order_no, member_id, ("已出票", "已改签"), "退票中")
    if result.get("success"):
        info = _refund_order_member(order_no) or {}
        conn = _conn()
        try:
            _record_refund(conn, request_id, order_no, info.get("member_id") or "",
                           "special", info.get("amount") or 0, 0, "退票中")
            conn.commit()
        finally:
            conn.close()
        from services import checkin_repo
        checkin_repo.cancel_checkin(order_no)   # 退票审批期间释放座位；驳回后可重新值机
        audit.write_ok("退票(特殊)", target=order_no, request_id=request_id)
    return result


# ------------------------------------------------ 自愿退票（规则费率，即时退款）

def _refund_fee_rate(hours_to_departure: float):
    """自愿退票费率（公示规则）。返回 (费率, 档位说明)。"""
    if hours_to_departure >= 72:
        return 0.05, "起飞前72小时以上（收5%）"
    if hours_to_departure >= 48:
        return 0.10, "起飞前48-72小时（收10%）"
    if hours_to_departure >= 24:
        return 0.20, "起飞前24-48小时（收20%）"
    return 0.30, "起飞前24小时以内（收30%）"


def _order_departure(order_no: str):
    """查询订单对应的起飞时间；返回 (Row订单, depart(datetime) 或 None)。"""
    conn = _conn()
    r = conn.execute(
        "SELECT o.order_no, o.member_id, o.status, o.amount, o.flight_date, f.dep_time "
        "FROM orders o JOIN flights f ON f.flight_no = o.flight_no "
        "WHERE o.order_no = ?", (security.normalize(order_no),)).fetchone()
    conn.close()
    if not r:
        return r, None
    try:
        from datetime import datetime as _dt
        depart = _dt.strptime(f"{r['flight_date']} {r['dep_time']}", "%Y-%m-%d %H:%M")
    except Exception:
        depart = None
    return r, depart


def refund_quote(order_no: str, member_id: str = None) -> dict:
    """自愿退票报价：返回手续费与预计到账金额（供确认卡片展示）。

    归属校验从订单反查会员号再走受信通道（不信任传参）。
    """
    order_no = security.normalize(order_no)
    r, depart = _order_departure(order_no)
    if not r:
        return {"error": f"订单不存在：{order_no}"}
    denied = security.enforce_owner(r["member_id"], action="退票")
    if denied:
        audit.denied("退票报价", denied["error"], target=order_no)
        return denied
    if r["status"] not in ("已出票", "已改签"):
        return {"error": f"订单状态为「{r['status']}」，只有「已出票/已改签」的订单可以退票"}
    if depart is None:
        return {"error": "订单缺少航班起飞时间，无法计算退款"}

    from datetime import datetime as _dt
    hours = (_dt.now() - depart).total_seconds() / 3600
    if hours >= 0:
        return {"error": "航班已起飞，自愿退票通道已关闭；如因航班延误/取消需要退票，请走特殊退票通道"}
    rate, tier = _refund_fee_rate(-hours)
    amount = int(r["amount"])
    fee = int(amount * rate)
    return {"order_no": order_no, "amount": amount, "fee_rate": rate, "fee_tier": tier,
            "fee": fee, "predict_amount": amount - fee, "depart_time": depart.isoformat(sep=" ", timespec="minutes")}


def refund_order_instant(order_no: str, member_id: str = None, request_id: str = None,
                         quote: dict = None, channel: dict = None) -> dict:
    """自愿退票（规则费率，即时到账）：已出票/已改签 → 已退款。

    - 费用在代码层按公示规则计算，不经 LLM 决定；
    - **request_id 幂等**：同一请求重放只退一次（先查流水，直接返回首次结果）；
    - 更新语句带状态守卫（`AND status IN (...)`）保证原子，杜绝重复退款；
    - 归属校验由 refund_quote 从订单反查后走受信通道完成；
    - `quote`：调用方已经算过的报价（渠道扣款要用同一个金额，避免二次计算漂移）；
    - `channel`：渠道退款结果，落进退款流水，供对账与失败追溯。

    ⚠️ 调用顺序约定：**渠道退款成功之后才允许调用本函数**。
    本函数只做业务状态变更，自身不触达任何支付渠道。
    """
    order_no = security.normalize(order_no)

    # 幂等优先：重复点击/重复提交直接返回首次结果，不再消费订单状态。
    # ⚠️ 但只有**真已定论**的流水才算"处理过"：`退款失败` 必须允许用同一个
    # requestId 重试（重试会覆盖那一行流水）；`退款待对账` 是"结果未知"，
    # 绝不能当成功回给用户——否则用户被告知"已处理"，钱其实没退。
    if request_id:
        existing = get_refund_by_request(request_id)
        if existing and existing["status"] in SETTLED_REFUND_STATUSES:
            audit.idempotent("退票", target=existing["order_no"], request_id=request_id,
                             detail={"refund_type": existing["refund_type"]})
            return {"success": True, "idempotent": True, "order_no": existing["order_no"],
                    "status": existing["status"], "refund_amount": existing["amount"],
                    "fee": existing["fee"],
                    "message": f"该退票请求已处理（订单 {existing['order_no']} 已{existing['status']}），无需重复提交"}
        if existing and existing["status"] == PENDING_REFUND_STATUS:
            return {"error": "这笔退款的结果还未确认（渠道超时），请先对账后再处理，不要重复提交",
                    "pending": True, "order_no": existing["order_no"],
                    "status": existing["status"], "out_request_no": request_id}

    quote = quote or refund_quote(order_no, member_id)
    if quote.get("error"):
        return quote

    info = _refund_order_member(order_no) or {}
    note = f"自愿退票：{quote['fee_tier']}，手续费{quote['fee']}元"
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE orders SET status = '已退款', refund_amount = ?, admin_note = ?, refunded_at = ? "
            "WHERE order_no = ? AND status IN ('已出票', '已改签')",
            (quote["predict_amount"], note,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_no))
        if cur.rowcount != 1:
            conn.rollback()
            return {"error": f"退款未生效：订单 {order_no} 状态刚刚发生变化（可能已被退款/改签/使用）"}
        _record_refund(conn, request_id, order_no, info.get("member_id") or "",
                       "voluntary", quote["predict_amount"], quote["fee"], "已退款",
                       channel=channel)
        conn.commit()
    finally:
        conn.close()

    from services import checkin_repo
    checkin_repo.cancel_checkin(order_no)   # 退票自动取消值机、释放座位
    audit.write_ok("退票", target=order_no, request_id=request_id,
                   detail={"fee": quote["fee"], "refund_amount": quote["predict_amount"],
                           "channel": (channel or {}).get("channel"),
                           "channel_status": (channel or {}).get("channel_status")})
    result = {"success": True, "order_no": order_no, "status": "已退款",
              "refund_amount": quote["predict_amount"], "fee": quote["fee"],
              "message": f"订单 {order_no} 已退款 {quote['predict_amount']} 元（手续费 {quote['fee']} 元，{quote['fee_tier']}）"}
    if channel:
        result["channel"] = channel.get("channel")
        result["channel_status"] = channel.get("channel_status")
        if channel.get("duplicate"):
            result["duplicate"] = True
    return result

# ------------------------------------------------ 改签（免改签费，差价多退少补）

def _ticket_passengers(row) -> int:
    """订单乘机人数：新订单有记录；种子订单金额即单人票价，按1人。"""
    p = row["passengers"] if "passengers" in row.keys() else None
    try:
        return int(p) if p else 1
    except (TypeError, ValueError):
        return 1


def change_quote(order_no: str, member_id: str, new_flight_no: str, new_date: str, new_cabin: str) -> dict:
    """改签报价：同航线任意航司任意未来日期，免改签费，只计算票价差（多退少补）。"""
    order_no = security.normalize(order_no)
    new_flight_no = security.normalize(new_flight_no)
    new_cabin = (new_cabin or "").strip()
    if new_cabin not in ("经济", "商务"):
        return {"error": "舱位仅支持：经济 / 商务"}

    conn = _conn()
    o = conn.execute(
        "SELECT o.order_no, o.member_id, o.status, o.amount, o.passengers, o.flight_no, o.flight_date, "
        "o.cabin, f.dep_iata, f.arr_iata, f.dep_time "
        "FROM orders o JOIN flights f ON f.flight_no = o.flight_no WHERE o.order_no = ?",
        (order_no,)).fetchone()
    if not o:
        conn.close()
        return {"error": f"订单不存在：{order_no}"}
    denied = security.enforce_owner(o["member_id"], action="改签")
    if denied:
        conn.close()
        audit.denied("改签报价", denied["error"], target=order_no)
        return denied
    if o["status"] not in ("已出票", "已改签"):
        conn.close()
        return {"error": f"订单状态为「{o['status']}」，只有「已出票/已改签」的订单可以改签"}
    if o["dep_iata"] == o["arr_iata"]:
        conn.close()
        return {"error": "订单航线数据异常"}

    from datetime import datetime as _dt
    try:
        old_depart = _dt.strptime(f"{o['flight_date']} {o['dep_time']}", "%Y-%m-%d %H:%M")
    except Exception:
        old_depart = None
    if old_depart and (old_depart - _dt.now()).total_seconds() <= 0:
        conn.close()
        return {"error": "原航班已起飞，无法改签"}

    nd = _normalize_date(new_date)
    if not nd:
        conn.close()
        return {"error": "新日期格式应为 YYYY-MM-DD"}
    if nd <= date.today():
        conn.close()
        return {"error": "改签日期必须是今天之后的日期"}

    nf = conn.execute(
        "SELECT f.flight_no, a.name_cn AS airline, f.dep_iata, f.arr_iata, f.dep_time, f.arr_time, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city "
        "FROM flights f JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata JOIN airports fa ON fa.iata3 = f.arr_iata "
        "WHERE f.flight_no = ?", (new_flight_no,)).fetchone()
    if not nf:
        conn.close()
        return {"error": f"新航班不存在：{new_flight_no}"}
    if (nf["dep_iata"], nf["arr_iata"]) != (o["dep_iata"], o["arr_iata"]):
        conn.close()
        return {"error": f"改签仅支持同一航线（{o['dep_iata']}-{o['arr_iata']}），新航班航线不符"}
    np_row = conn.execute(
        "SELECT price FROM flight_prices WHERE flight_no = ? AND flight_date = ? AND cabin = ?",
        (new_flight_no, nd.isoformat(), new_cabin)).fetchone()
    conn.close()
    if not np_row:
        return {"error": f"{new_flight_no} 在 {nd.isoformat()} 无 {new_cabin}舱 在售票价"}

    passengers = _ticket_passengers(o)
    old_unit = int(o["amount"]) // max(1, passengers)
    new_unit = int(np_row["price"])
    new_total = new_unit * passengers
    diff = new_total - int(o["amount"])
    return {"order_no": order_no, "passengers": passengers,
            "old": {"flight_no": o["flight_no"], "date": o["flight_date"], "cabin": o["cabin"],
                    "amount": int(o["amount"])},
            "new": {"flight_no": nf["flight_no"], "airline": nf["airline"],
                    "route": f"{nf['dep_city']}-{nf['arr_city']}", "date": nd.isoformat(),
                    "dep_time": nf["dep_time"], "arr_time": nf["arr_time"],
                    "cabin": new_cabin, "unit_price": new_unit, "amount": new_total},
            "fare_diff": diff,
            "diff_desc": (f"需补差价 {diff} 元" if diff > 0 else
                          (f"退回差价 {-diff} 元" if diff < 0 else "票价相同，无差价")),
            "change_fee": 0,
            "message": f"改签免手续费，{('补差价 ' + str(diff) + ' 元') if diff > 0 else (('退差价 ' + str(-diff) + ' 元') if diff < 0 else '无差价')}"}


# ------------------------------------------------ 改签流水（差价必须真的走渠道）

# 差价 > 0：先落这个状态，订单**不动**；支付宝收款成功后才调 apply_change 落成改签。
CHANGE_PENDING_STATUS = "待支付差价"
CHANGE_DONE_STATUS = "已改签"
# 差价退款失败：没改签，允许用同一个请求号重试（退款流水会覆盖为最新结果）
CHANGE_REFUND_FAILED_STATUS = "差价退款失败"
# 钱已按差价退回、但订单没能改（极端并发）：必须人工收口，不能静默重试
CHANGE_MANUAL_STATUS = "待人工改签"
# 超时未支付被关掉（用户放弃补差价）——订单本来就没动，清掉这道闸而已
CHANGE_CANCELED_STATUS = "已取消"


def _change_payment_expired(row: dict) -> bool:
    """这笔「待支付差价」是否已经超时作废。

    不判它的话，用户一旦在收银台放弃付款，这笔待付差价会**永久锁死**该订单
    以后的改签（只能改回同一班继续付），而报错文案却写着"可等超时后再改签"。
    """
    from services import payment_repo
    pay_no = (row or {}).get("pay_no")
    if pay_no:
        p = payment_repo.get_payment(pay_no)
        if p:
            # 流水已关闭 → 作废；仍待支付 → 还活着（别抢用户的付款机会）
            return p["status"] != "待支付"
    try:
        created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return False
    minutes = payment_repo.DEFAULT_PAY_TIMEOUT_MINUTES
    return (datetime.now() - created).total_seconds() > minutes * 60


def _cancel_change(request_id: str) -> bool:
    """关闭超时未支付的改签流水（订单没动过，只需把闸放开）。"""
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE change_requests SET status = ?, updated_at = ? "
            "WHERE request_id = ? AND status = ?",
            (CHANGE_CANCELED_STATUS, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             request_id, CHANGE_PENDING_STATUS))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _new_change_request_id(order_no: str) -> str:
    """服务端生成改签请求号（客户端没传 requestId 时的兜底）。

    它身兼三职：change_requests 的幂等键、差价支付流水的关联键（靠它反查该改哪张单）、
    差价退款的 out_request_no。所以**必须存在**——否则钱收了也找不到该改哪张单。
    客户端应自带并复用同一 requestId（断网重发不会改两次）。
    """
    import uuid
    return f"CH{security.normalize(order_no)}-{uuid.uuid4().hex[:16]}"


def _record_change(conn, request_id, order_no, member_id, quote, status, pay_no=None,
                   refund_request_id=None, channel=None) -> bool:
    """写改签流水（request_id 幂等键；重复写入 upsert 为本次结果）。

    pay_no / refund_request_id 用 COALESCE 更新：重试时调用方可能只传其中一个，
    不能把已挂上的关联键抹掉。
    """
    if not request_id:
        return False
    old = quote.get("old") or {}
    new = quote.get("new") or {}
    ch = channel or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vals = (order_no, member_id or "", old.get("flight_no"), old.get("date"),
            new.get("flight_no"), new.get("date"), new.get("cabin"),
            int(old.get("amount") or 0), int(new.get("amount") or 0),
            int(quote.get("fare_diff") or 0), status, now,
            pay_no, refund_request_id, ch.get("channel"), ch.get("channel_status"),
            ch.get("channel_error"))
    cur = conn.execute(
        "INSERT OR IGNORE INTO change_requests (request_id, order_no, member_id, "
        "old_flight_no, old_date, new_flight_no, new_date, new_cabin, old_amount, "
        "new_amount, fare_diff, status, created_at, pay_no, refund_request_id, "
        "channel, channel_status, channel_error, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, *vals, now))
    if cur.rowcount == 1:
        return True
    conn.execute(
        "UPDATE change_requests SET order_no=?, member_id=?, old_flight_no=?, old_date=?, "
        "new_flight_no=?, new_date=?, new_cabin=?, old_amount=?, new_amount=?, fare_diff=?, "
        "status=?, updated_at=?, pay_no=COALESCE(?, pay_no), "
        "refund_request_id=COALESCE(?, refund_request_id), channel=?, channel_status=?, "
        "channel_error=? WHERE request_id=?",
        (*vals, request_id))
    return False


def get_change_by_request(request_id: str):
    """按改签请求号取流水（幂等判断的事实源）。"""
    if not request_id:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM change_requests WHERE request_id = ?",
                           (request_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_pending_change(order_no: str):
    """取该订单「待支付差价」的改签流水。

    有它 = 这张单正在等差价支付：此时 `/api/pay/create` 该收的是差价，
    而不是票款（票早就付过了）。
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM change_requests WHERE order_no = ? AND status = ? "
            "ORDER BY id DESC LIMIT 1",
            (security.normalize(order_no), CHANGE_PENDING_STATUS)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_change_by_pay_no(pay_no: str):
    """按差价支付流水号反查改签流水（支付回调落账时靠它判断该改签还是出票）。"""
    if not pay_no:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM change_requests WHERE pay_no = ? "
                           "ORDER BY id DESC LIMIT 1", (pay_no,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def attach_change_payment(request_id: str, pay_no: str) -> bool:
    """把差价支付流水号挂到改签流水上。"""
    if not (request_id and pay_no):
        return False
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE change_requests SET pay_no = ?, updated_at = ? WHERE request_id = ?",
            (pay_no, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), request_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _flight_display(flight_no: str) -> dict:
    """航班展示信息（航司/时刻/城市），只用于文案，不参与金额计算。"""
    conn = _conn()
    try:
        r = conn.execute(
            "SELECT f.flight_no, a.name_cn AS airline, f.dep_time, f.arr_time, "
            "fd.city_cn AS dep_city, fa.city_cn AS arr_city "
            "FROM flights f JOIN airlines a ON a.code = f.airline_code "
            "JOIN airports fd ON fd.iata3 = f.dep_iata "
            "JOIN airports fa ON fa.iata3 = f.arr_iata WHERE f.flight_no = ?",
            (flight_no,)).fetchone()
        return dict(r) if r else {}
    finally:
        conn.close()


def _apply_change(order_no: str, quote: dict, expected_old: dict = None) -> dict:
    """把改签落到订单上（纯本地写，不碰任何渠道）。

    `expected_old` 供「先收款/先退款、后改签」的两段式流程做并发守卫：
    订单必须仍停在改签前的**航班 + 日期**上，否则拒绝（避免把别人的改动覆盖掉）。
    """
    order_no = security.normalize(order_no)
    new_info = quote["new"]
    old_info = quote["old"]
    conn = _conn()
    try:
        sql = ("UPDATE orders SET flight_no = ?, flight_date = ?, cabin = ?, amount = ?, "
               "status = '已改签', admin_note = ? "
               "WHERE order_no = ? AND status IN ('已出票', '已改签')")
        params = [new_info["flight_no"], new_info["date"], new_info["cabin"], new_info["amount"],
                  f"改签: {old_info['flight_no']}/{old_info['date']}/{old_info['cabin']} -> "
                  f"{new_info['flight_no']}/{new_info['date']}/{new_info['cabin']}，{quote['diff_desc']}",
                  order_no]
        if expected_old:
            sql += " AND flight_no = ? AND flight_date = ?"
            params += [expected_old.get("flight_no"), expected_old.get("date")]
        cur = conn.execute(sql, params)
        if cur.rowcount != 1:
            conn.rollback()
            return {"error": f"改签未生效：订单 {order_no} 的状态或航班刚刚发生变化"
                             f"（可能已被退票/改签/使用），请刷新后重试"}
        conn.commit()
    finally:
        conn.close()
    from services import checkin_repo
    checkin_repo.cancel_checkin(order_no)   # 新航班需重新值机，原座位自动释放
    audit.write_ok("改签", target=order_no,
                   detail={"from": old_info, "to": new_info, "fare_diff": quote["fare_diff"]})
    return {"success": True, "order_no": order_no, "status": "已改签",
            "old": old_info, "new": new_info, "fare_diff": quote["fare_diff"],
            "message": f"订单 {order_no} 已改签至 {new_info.get('airline', '')}{new_info['flight_no']} "
                       f"{new_info['date']} {new_info.get('dep_time', '')}，{quote['diff_desc']}"
                       f"（原值机已取消，请重新值机）"}


def apply_change(order_no: str, request_id: str) -> dict:
    """把「待支付差价」的改签落成订单变更（差价收款成功后调用）。

    ⚠️ 金额与航班一律用流水里**冻结的值**（客户就是按它付的钱），不重新报价——
    否则支付期间价格波动会变成「钱付了、改签却算不出同样的差价」。
    """
    row = get_change_by_request(request_id)
    if not row:
        return {"error": f"改签流水不存在：{request_id}"}
    if row["status"] == CHANGE_DONE_STATUS:
        return {"success": True, "idempotent": True, "order_no": row["order_no"],
                "message": "该改签已生效，无需重复处理"}
    if row["status"] != CHANGE_PENDING_STATUS:
        return {"error": f"改签流水状态为「{row['status']}」，不能执行改签"}

    disp = _flight_display(row["new_flight_no"])
    diff = int(row["fare_diff"])
    quote = {
        "old": {"flight_no": row["old_flight_no"], "date": row["old_date"],
                "cabin": "", "amount": int(row["old_amount"] or 0)},
        "new": {"flight_no": row["new_flight_no"], "airline": disp.get("airline", ""),
                "route": f"{disp.get('dep_city', '')}-{disp.get('arr_city', '')}",
                "date": row["new_date"], "dep_time": disp.get("dep_time", ""),
                "arr_time": disp.get("arr_time", ""), "cabin": row["new_cabin"],
                "amount": int(row["new_amount"] or 0)},
        "fare_diff": diff,
        "diff_desc": (f"需补差价 {diff} 元" if diff > 0 else
                      (f"退回差价 {-diff} 元" if diff < 0 else "票价相同，无差价")),
    }
    result = _apply_change(row["order_no"], quote,
                           expected_old={"flight_no": row["old_flight_no"],
                                         "date": row["old_date"]})
    if result.get("error"):
        return result
    conn = _conn()
    try:
        _record_change(conn, request_id, row["order_no"], row["member_id"], quote,
                       CHANGE_DONE_STATUS, pay_no=row["pay_no"])
        conn.commit()
    finally:
        conn.close()
    result["request_id"] = request_id
    return result


def change_order(order_no: str, member_id: str, new_flight_no: str, new_date: str, new_cabin: str) -> dict:
    """执行改签（报价 + 立即生效）——**不结差价**，仅供内部/管理端与测试使用。

    ⚠️ 面向用户的 REST 流程必须走 `begin_change`：它会保证差价真的走渠道
    （补差价先收款、退差价先退款），本函数不碰任何渠道。
    """
    quote = change_quote(order_no, member_id, new_flight_no, new_date, new_cabin)
    if quote.get("error"):
        return quote
    return _apply_change(order_no, quote)


def begin_change(order_no: str, member_id: str, new_flight_no: str, new_date: str,
                 new_cabin: str, request_id: str = None) -> dict:
    """用户改签入口：**差价结清才生效**（多退少补都落到渠道）。

    | 差价 | 动作 | 订单状态 |
    |---|---|---|
    | > 0 | 落「待支付差价」流水，订单不动，返回 need_pay；用户走 /api/pay/create 收差价，
            支付成功后由 payment_service.settle_payment 调 apply_change 落成改签 |
    | < 0 | **先调渠道退款**（复用退款编排，原路退回差价），成功后才改签；失败则不改签 + 留痕 |
    | = 0 | 直接改签，落一行已改签流水 |
    """
    quote = change_quote(order_no, member_id, new_flight_no, new_date, new_cabin)
    if quote.get("error"):
        return quote
    order_no = security.normalize(order_no)
    diff = int(quote["fare_diff"])
    request_id = request_id or _new_change_request_id(order_no)

    # 幂等：同一个请求号重放不重复执行（与退款流水同一套契约）
    existing = get_change_by_request(request_id)
    if existing:
        if existing["status"] == CHANGE_DONE_STATUS:
            audit.idempotent("改签", target=order_no, request_id=request_id)
            return {"success": True, "idempotent": True, "applied": True, "order_no": order_no,
                    "request_id": request_id, "fare_diff": existing["fare_diff"],
                    "old": quote["old"], "new": quote["new"],
                    "message": f"该改签请求已生效（订单 {order_no} 已是改签后状态），无需重复提交"}
        if existing["status"] == CHANGE_PENDING_STATUS:
            return {"success": True, "need_pay": True, "idempotent": True, "order_no": order_no,
                    "request_id": request_id, "fare_diff": existing["fare_diff"],
                    "old": quote["old"], "new": quote["new"], "quote": quote,
                    "pay_no": existing["pay_no"],
                    "message": f"改签差价 {existing['fare_diff']} 元待支付，请继续完成支付"}
        if existing["status"] == CHANGE_MANUAL_STATUS:
            return {"error": f"该改签的差价已退回，但订单变更未完成（请求号 {request_id}），"
                             f"请联系客服处理", "needs_manual": True, "request_id": request_id}
        # 「差价退款失败」：允许用同一个请求号重试（退款流水会覆盖为最新结果）

    # 该订单还挂着一笔未支付的改签差价：目标相同就让用户接着付（同一个 requestId 除外）；
    # 目标不同则拒绝——否则会留下两笔悬挂的待付差价，说不清到底要改哪一班。
    open_change = get_pending_change(order_no)
    if open_change and _change_payment_expired(open_change):
        # 上一笔差价支付已超时作废（用户放弃/流水已关闭）→ 自动放开，别永久锁死改签
        _cancel_change(open_change["request_id"])
        open_change = None
    if open_change and open_change["request_id"] != request_id:
        same_target = (open_change["new_flight_no"], open_change["new_date"],
                       open_change["new_cabin"]) == (quote["new"]["flight_no"],
                                                     quote["new"]["date"], quote["new"]["cabin"])
        if not same_target:
            return {"error": f"该订单还有一笔未支付的改签差价（{open_change['new_flight_no']} "
                             f"{open_change['new_date']}，需补 {open_change['fare_diff']} 元）；"
                             f"请先完成支付，或等该笔支付超时后再改签",
                    "needs_pay": True, "pending_request_id": open_change["request_id"],
                    "fare_diff": int(open_change["fare_diff"])}
        return {"success": True, "need_pay": True, "idempotent": True, "order_no": order_no,
                "request_id": open_change["request_id"], "fare_diff": open_change["fare_diff"],
                "old": quote["old"], "new": quote["new"], "quote": quote,
                "pay_no": open_change["pay_no"],
                "message": f"该订单的改签差价 {open_change['fare_diff']} 元尚未支付，请先完成支付"}

    if diff > 0:
        conn = _conn()
        try:
            _record_change(conn, request_id, order_no, member_id, quote, CHANGE_PENDING_STATUS)
            conn.commit()
        finally:
            conn.close()
        audit.write_ok("改签(待补差价)", target=order_no, request_id=request_id,
                       detail={"fare_diff": diff, "to": quote["new"]})
        return {"success": True, "need_pay": True, "order_no": order_no, "request_id": request_id,
                "fare_diff": diff, "quote": quote, "old": quote["old"], "new": quote["new"],
                "message": f"改签需补差价 {diff} 元，请先完成支付；支付成功后改签自动生效"}

    if diff < 0:
        from services import payment_service
        refund = payment_service.refund_payment(
            order_no=order_no, amount=abs(diff), out_request_no=request_id,
            refund_type="change_diff", reason=f"改签退回差价（{order_no}）")
        info = _refund_order_member(order_no) or {}
        ledger_member = info.get("member_id") or member_id or ""
        if refund.get("error"):
            conn = _conn()
            try:
                # 失败路径的渠道留痕由 refund_payment 自己写（退款流水表），
                # 这里把改签流水标记成「差价退款失败」，订单不动。
                _record_change(conn, request_id, order_no, ledger_member, quote,
                               CHANGE_REFUND_FAILED_STATUS, refund_request_id=request_id,
                               channel=refund)
                conn.commit()
            finally:
                conn.close()
            audit.write_error("改签(退差价)", refund["error"], target=order_no)
            return {"error": f"改签需退回差价 {abs(diff)} 元，但渠道退款未成功：{refund['error']}"
                             f"（订单未改动，可稍后重试）",
                    "fare_diff": diff, "refund": refund, "request_id": request_id}

        # 钱已经退出去了：先把退款流水落定（同一个 request_id 也是它的幂等键）。
        # 这样即便下面改签失败，账上也查得到这笔差价确实退了。
        conn = _conn()
        try:
            _record_refund(conn, request_id, order_no, ledger_member, "change_diff",
                           abs(diff), 0, "已退款", channel=refund)
            conn.commit()
        finally:
            conn.close()

        result = _apply_change(order_no, quote)
        if result.get("error"):
            # 钱已经退出去了、订单却没改成 → 必须人工收口，绝不能当成功
            conn = _conn()
            try:
                _record_change(conn, request_id, order_no, ledger_member, quote,
                               CHANGE_MANUAL_STATUS, refund_request_id=request_id,
                               channel=refund)
                conn.commit()
            finally:
                conn.close()
            audit.write_error("改签(退差价后改签失败)", result["error"], target=order_no)
            return {**result, "needs_manual": True, "refunded": True, "refund": refund,
                    "error": result["error"] + f"；差价 {abs(diff)} 元已原路退回，需人工完成改签"}

        conn = _conn()
        try:
            _record_change(conn, request_id, order_no, ledger_member, quote, CHANGE_DONE_STATUS,
                           refund_request_id=request_id, channel=refund)
            conn.commit()
        finally:
            conn.close()
        return {**result, "request_id": request_id, "refunded": True, "refund": refund,
                "message": result["message"] + f"；差价 {abs(diff)} 元已原路退回"}

    result = _apply_change(order_no, quote)
    if result.get("error"):
        return result
    conn = _conn()
    try:
        _record_change(conn, request_id, order_no, member_id, quote, CHANGE_DONE_STATUS)
        conn.commit()
    finally:
        conn.close()
    return {**result, "request_id": request_id, "message": result["message"] + "（票价相同，无差价）"}


# ---------------------------------------------------------------- 天气

def get_city_coords(city: str) -> dict:
    """城市 → (lat, lon)，供 Open-Meteo 使用。"""
    c = resolve_city(city)
    if not c:
        return {"error": f"暂不支持的天气城市：{city}"}
    conn = _conn()
    row = conn.execute("SELECT lat, lon FROM city_coords WHERE city = ?", (c,)).fetchone()
    conn.close()
    return {"city": c, "lat": row["lat"], "lon": row["lon"]} if row else {"error": f"无 {c} 坐标"}


def _normalize_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value).strip().replace("/", "-"))
    except (ValueError, AttributeError, TypeError):
        return None
