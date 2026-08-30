"""
航班数据查询层（Repository）：供 function-calling 工具调用。

所有函数返回纯 dict/list，工具层负责包装成功/失败；本层不抛业务异常，
查不到时返回空结果，参数非法时返回 {"error": ...} 结构由调用方决定。
"""

import sqlite3
from datetime import date, timedelta

from services import db
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


# ---------------------------------------------------------------- 订单/账单

def get_order_bill(member_id: str = None, order_no: str = None) -> dict:
    """按会员/订单号查账单（orders × flights × airlines）。"""
    if not member_id and not order_no:
        return {"error": "需要提供会员号或订单号"}
    conn = _conn()
    sql = (
        "SELECT o.order_no, o.member_id, o.flight_no, o.flight_date, o.cabin, o.amount, "
        "o.status, o.created_at, a.name_cn AS airline, "
        "fd.city_cn AS dep_city, fa.city_cn AS arr_city, f.dep_time "
        "FROM orders o "
        "JOIN flights f ON f.flight_no = o.flight_no "
        "JOIN airlines a ON a.code = f.airline_code "
        "JOIN airports fd ON fd.iata3 = f.dep_iata "
        "JOIN airports fa ON fa.iata3 = f.arr_iata "
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
            "route": f"{r['dep_city']}-{r['arr_city']}",
            "flight_date": r["flight_date"], "dep_time": r["dep_time"],
            "cabin": r["cabin"], "amount": r["amount"],
            "status": r["status"], "created_at": r["created_at"],
        })
    return {"member_id": member_id, "orders": orders, "count": len(orders),
            "total_amount": sum(o["amount"] for o in orders)}


# ---------------------------------------------------------------- 投诉

def query_complaints(member_id: str = None, ticket_no: str = None) -> dict:
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
