"""
航班数据查询层（Repository）：供 function-calling 工具调用。

所有函数返回纯 dict/list，工具层负责包装成功/失败；本层不抛业务异常，
查不到时返回空结果，参数非法时返回 {"error": ...} 结构由调用方决定。
"""

import sqlite3
from datetime import date, datetime, timedelta

from services import audit, db, security
from services.db_seed import HORIZON_DAYS, MEMBER_BAGGAGE_BONUS_KG, default_baggage_rule


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

def search_flights(departure: str, destination: str, date_str: str = None) -> dict:
    """按出发/目的地（城市名或 IATA）查航班；date_str 为空时给出 30 天价格区间。"""
    dep_iata = city_to_iata(departure)
    arr_iata = city_to_iata(destination)
    if not dep_iata or not arr_iata:
        return {"error": f"暂不支持该航线：{departure} → {destination}"}

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
        weekday_cn = str(d.isoweekday())
        rows = conn.execute(
            base_sql + " AND instr(f.freq_days, ?) > 0 ORDER BY f.dep_time",
            (dep_iata, arr_iata, weekday_cn),
        ).fetchall()
        if not rows:
            conn.close()
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
        price_row = conn.execute(
            "SELECT MIN(CASE WHEN cabin='经济' THEN price END) AS min_p, "
            "MAX(CASE WHEN cabin='商务' THEN price END) AS max_p, "
            "MIN(flight_date) AS d_from, MAX(flight_date) AS d_to "
            "FROM flight_prices WHERE flight_no = ?",
            (r["flight_no"],),
        ).fetchone()
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


# ---------------------------------------------------------------- 行李规则

def get_baggage_allowance(member_id: str = None, order_no: str = None,
                          flight_no: str = None, cabin: str = None,
                          airline: str = None) -> dict:
    """查询行李托运额度与超重费用（随身/免费托运/超重费率/加件费）。

    定位优先级：order_no > flight_no > airline。
    - order_no：查订单并按归属硬校验（防 LLM 越权传参），从订单取航班与舱位；
    - flight_no：查航班取航司，舱位缺省按"经济"；
    - airline：按航司代码（CA/MU/9C）或中文名直接查。
    返回中 free_checked.weight_kg 已叠加登录会员等级带来的额外托运额度。
    """
    if not (order_no or flight_no or airline):
        return {"error": "需要提供订单号、航班号或航司之一"}

    cabin = (str(cabin).strip() if cabin else "") or None
    if cabin and cabin not in ("经济", "商务"):
        return {"error": f"舱位只能是 经济 或 商务：{cabin}"}

    conn = _conn()
    try:
        ctx: dict = {}
        if order_no:
            o = conn.execute(
                "SELECT order_no, member_id, flight_no, flight_date, cabin "
                "FROM orders WHERE order_no = ?",
                (security.normalize(order_no),)).fetchone()
            if not o:
                return {"error": f"订单不存在：{order_no}"}
            denied = security.enforce_owner(o["member_id"], action="查询")
            if denied:
                return denied
            member_id = o["member_id"]
            cabin = cabin or o["cabin"]
            ctx = {"order_no": o["order_no"], "flight_no": o["flight_no"],
                   "flight_date": o["flight_date"]}
            frow = conn.execute("SELECT airline_code FROM flights WHERE flight_no = ?",
                                (o["flight_no"],)).fetchone()
        elif flight_no:
            frow = conn.execute(
                "SELECT flight_no, airline_code FROM flights WHERE flight_no = ?",
                (security.normalize(flight_no),)).fetchone()
            if not frow:
                return {"error": f"航班不存在：{flight_no}"}
            ctx = {"flight_no": frow["flight_no"]}
        else:
            frow = conn.execute(
                "SELECT code AS airline_code FROM airlines WHERE code = ? OR name_cn = ?",
                (security.normalize(airline), str(airline).strip())).fetchone()
            if not frow:
                return {"error": f"航司不存在：{airline}（可用代码如 CA/MU/9C 或中文名）"}

        if not frow:
            return {"error": "未找到对应的航班/航司信息"}
        airline_code = frow["airline_code"]
        cabin = cabin or "经济"

        arow = conn.execute("SELECT code, name_cn, is_lcc FROM airlines WHERE code = ?",
                            (airline_code,)).fetchone()
        if not arow:
            return {"error": f"航司数据缺失：{airline_code}"}

        rule = conn.execute(
            "SELECT carry_on_pieces, carry_on_kg, free_checked_pieces, free_checked_kg, "
            "overweight_fee_per_kg, extra_piece_fee, note "
            "FROM baggage_rules WHERE airline_code = ? AND cabin = ?",
            (airline_code, cabin)).fetchone()
        base = dict(rule) if rule else default_baggage_rule(arow["is_lcc"], cabin)

        # 会员等级附加额度：仅经济舱且本身含免费托运额时叠加
        level, bonus = None, 0
        if member_id:
            crow = conn.execute("SELECT level FROM customers WHERE member_id = ?",
                                (member_id,)).fetchone()
            if crow:
                level = crow["level"]
                if cabin == "经济" and base["free_checked_kg"] > 0:
                    bonus = MEMBER_BAGGAGE_BONUS_KG.get(level, 0)
    finally:
        conn.close()

    return {
        "airline": arow["name_cn"],
        "airline_code": airline_code,
        "cabin": cabin,
        "is_lcc": bool(arow["is_lcc"]),
        "carry_on": {"pieces": base["carry_on_pieces"], "weight_kg": base["carry_on_kg"]},
        "free_checked": {"pieces": base["free_checked_pieces"],
                         "weight_kg": base["free_checked_kg"] + bonus},
        "base_free_checked_kg": base["free_checked_kg"],
        "overweight_fee_per_kg": base["overweight_fee_per_kg"],
        "extra_piece_fee": base["extra_piece_fee"],
        "member_level": level,
        "member_bonus_kg": bonus,
        "note": base["note"],
        **ctx,
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
    conn.close()
    if not rows:
        return {"error": "未找到该会员/订单的账单记录"}
    orders = []
    for r in rows:
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
        "SELECT f.flight_no, a.name_cn AS airline, f.dep_time, "
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
    return {"flight_no": frow["flight_no"], "airline": frow["airline"],
            "route": f"{frow['dep_city']}-{frow['arr_city']}", "dep_time": frow["dep_time"],
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


def _record_refund(conn, request_id, order_no, member_id, refund_type, amount, fee, status) -> bool:
    """写退款流水（request_id 为幂等键）。已存在同 request_id 时返回 False。"""
    if not request_id:
        return True
    cur = conn.execute(
        "INSERT OR IGNORE INTO refunds (request_id, order_no, member_id, refund_type, "
        "amount, fee, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (request_id, order_no, member_id, refund_type, int(amount or 0), int(fee or 0),
         status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return cur.rowcount == 1


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


def refund_order_instant(order_no: str, member_id: str = None, request_id: str = None) -> dict:
    """自愿退票（规则费率，即时到账）：已出票/已改签 → 已退款。

    - 费用在代码层按公示规则计算，不经 LLM 决定；
    - **request_id 幂等**：同一请求重放只退一次（先查流水，直接返回首次结果）；
    - 更新语句带状态守卫（`AND status IN (...)`）保证原子，杜绝重复退款；
    - 归属校验由 refund_quote 从订单反查后走受信通道完成。
    """
    order_no = security.normalize(order_no)

    # 幂等优先：重复点击/重复提交直接返回首次结果，不再消费订单状态
    if request_id:
        existing = get_refund_by_request(request_id)
        if existing:
            audit.idempotent("退票", target=existing["order_no"], request_id=request_id,
                             detail={"refund_type": existing["refund_type"]})
            return {"success": True, "idempotent": True, "order_no": existing["order_no"],
                    "status": existing["status"], "refund_amount": existing["amount"],
                    "fee": existing["fee"],
                    "message": f"该退票请求已处理（订单 {existing['order_no']} 已{existing['status']}），无需重复提交"}

    quote = refund_quote(order_no, member_id)
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
                       "voluntary", quote["predict_amount"], quote["fee"], "已退款")
        conn.commit()
    finally:
        conn.close()

    from services import checkin_repo
    checkin_repo.cancel_checkin(order_no)   # 退票自动取消值机、释放座位
    audit.write_ok("退票", target=order_no, request_id=request_id,
                   detail={"fee": quote["fee"], "refund_amount": quote["predict_amount"]})
    return {"success": True, "order_no": order_no, "status": "已退款",
            "refund_amount": quote["predict_amount"], "fee": quote["fee"],
            "message": f"订单 {order_no} 已退款 {quote['predict_amount']} 元（手续费 {quote['fee']} 元，{quote['fee_tier']}）"}

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


def change_order(order_no: str, member_id: str, new_flight_no: str, new_date: str, new_cabin: str) -> dict:
    """执行改签：更新订单航班/日期/舱位/金额，状态置为「已改签」。

    免改签费，仅多退少补票价差（负差价直接调减金额，演示不产生退款流水）。
    """
    quote = change_quote(order_no, member_id, new_flight_no, new_date, new_cabin)
    if quote.get("error"):
        return quote

    order_no = security.normalize(order_no)
    new_info = quote["new"]
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE orders SET flight_no = ?, flight_date = ?, cabin = ?, amount = ?, status = '已改签', "
            "admin_note = ? WHERE order_no = ? AND status IN ('已出票', '已改签')",
            (new_info["flight_no"], new_info["date"], new_info["cabin"], new_info["amount"],
             f"改签: {quote['old']['flight_no']}/{quote['old']['date']}/{quote['old']['cabin']} -> "
             f"{new_info['flight_no']}/{new_info['date']}/{new_info['cabin']}，{quote['diff_desc']}",
             order_no))
        if cur.rowcount != 1:
            conn.rollback()
            return {"error": f"改签未生效：订单 {order_no} 状态刚刚发生变化（可能已被退票/改签/使用）"}
        conn.commit()
    finally:
        conn.close()
    from services import checkin_repo
    checkin_repo.cancel_checkin(order_no)   # 新航班需重新值机，原座位自动释放
    audit.write_ok("改签", target=order_no,
                   detail={"from": quote["old"], "to": new_info, "fare_diff": quote["fare_diff"]})
    return {"success": True, "order_no": order_no, "status": "已改签",
            "old": quote["old"], "new": new_info, "fare_diff": quote["fare_diff"],
            "message": f"订单 {order_no} 已改签至 {new_info['airline']}{new_info['flight_no']} "
                       f"{new_info['date']} {new_info['dep_time']}，{quote['diff_desc']}（原值机已取消，请重新值机）"}


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
