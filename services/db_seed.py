"""
数据种子生成器：构建"模拟真实民航系统"的航班/价格/延误/客户/订单/投诉数据。

特性：
- 固定随机种子（默认 42），全量可复现；
- 每行价格由 (航班号, 日期, 舱位) 的稳定哈希驱动，增量扩展时与全量生成结果一致；
- ensure_seeded() 幂等：首次全量生成，之后仅按需补齐未来 30 天价格。
"""

import hashlib
import random
import sqlite3
from datetime import date, timedelta

from services import db

SEED = 42
HORIZON_DAYS = 30

# ---------------------------------------------------------------- 基础数据

AIRPORTS = [
    # (iata3, icao4, city_cn, city_en, lat, lon)
    ("PEK", "ZBAA", "北京", "Beijing", 40.0799, 116.6031),
    ("PVG", "ZSPD", "上海", "Shanghai", 31.1443, 121.8083),
    ("CAN", "ZGGG", "广州", "Guangzhou", 23.3924, 113.2988),
    ("SZX", "ZGSZ", "深圳", "Shenzhen", 22.6393, 113.8107),
    ("CTU", "ZUUU", "成都", "Chengdu", 30.5785, 103.9471),
    ("HGH", "ZSHC", "杭州", "Hangzhou", 30.2295, 120.4344),
    ("XIY", "ZLXY", "西安", "Xi'an", 34.4471, 108.7516),
    ("XMN", "ZSAM", "厦门", "Xiamen", 24.5440, 118.1277),
    ("NKG", "ZSNJ", "南京", "Nanjing", 31.7420, 118.8620),
    ("WUH", "ZHHH", "武汉", "Wuhan", 30.7838, 114.2081),
    ("CKG", "ZUCK", "重庆", "Chongqing", 29.7192, 106.6417),
    ("KMG", "ZPPP", "昆明", "Kunming", 25.1019, 102.9292),
    ("CSX", "ZGHA", "长沙", "Changsha", 28.1892, 113.2196),
    ("TAO", "ZSQD", "青岛", "Qingdao", 36.3611, 120.0882),
    ("CGO", "ZHCC", "郑州", "Zhengzhou", 34.5197, 113.8409),
    ("TSN", "ZBTJ", "天津", "Tianjin", 39.1244, 117.3462),
    ("SHE", "ZYTX", "沈阳", "Shenyang", 41.6398, 123.4839),
    ("DLC", "ZYTL", "大连", "Dalian", 38.9657, 121.5386),
    ("HRB", "ZYHB", "哈尔滨", "Harbin", 45.6234, 126.2503),
    ("SYX", "ZJSY", "三亚", "Sanya", 18.3029, 109.4123),
    ("HAK", "ZJHK", "海口", "Haikou", 19.9349, 110.4590),
    ("URC", "ZWWW", "乌鲁木齐", "Urumqi", 43.9071, 87.4742),
    ("LHW", "ZLLL", "兰州", "Lanzhou", 36.5152, 103.6204),
    ("KWE", "ZUGY", "贵阳", "Guiyang", 26.5385, 106.8008),
    ("NNG", "ZGNN", "南宁", "Nanning", 22.6083, 108.1724),
    ("FOC", "ZSFZ", "福州", "Fuzhou", 25.9352, 119.6633),
]

AIRLINES = [
    # (code, name_cn, is_lcc, price_factor)
    ("CA", "中国国航", 0, 1.00),
    ("MU", "东方航空", 0, 1.00),
    ("CZ", "南方航空", 0, 0.98),
    ("HU", "海南航空", 0, 0.97),
    ("3U", "四川航空", 0, 0.96),
    ("MF", "厦门航空", 0, 0.95),
    ("ZH", "深圳航空", 0, 0.95),
    ("HO", "吉祥航空", 0, 0.88),
    ("GS", "天津航空", 1, 0.75),
    ("9C", "春秋航空", 1, 0.68),
]

# 无向航线表：(城市1, 城市2, 飞行时长分钟)
ROUTES = [
    ("北京", "上海", 135), ("北京", "广州", 200), ("北京", "深圳", 205),
    ("北京", "成都", 180), ("北京", "杭州", 130), ("北京", "西安", 155),
    ("北京", "重庆", 175), ("北京", "昆明", 225), ("北京", "哈尔滨", 135),
    ("北京", "三亚", 250), ("北京", "天津", 55), ("北京", "沈阳", 100),
    ("北京", "大连", 90), ("北京", "武汉", 150), ("北京", "乌鲁木齐", 275),
    ("上海", "广州", 155), ("上海", "深圳", 155), ("上海", "成都", 190),
    ("上海", "杭州", 65), ("上海", "重庆", 175), ("上海", "三亚", 205),
    ("上海", "西安", 155), ("上海", "昆明", 220), ("上海", "武汉", 105),
    ("广州", "深圳", 70), ("广州", "成都", 150), ("广州", "西安", 155),
    ("广州", "海口", 90), ("广州", "武汉", 120),
    ("深圳", "成都", 155), ("深圳", "武汉", 115), ("深圳", "海口", 105),
    ("杭州", "成都", 165), ("杭州", "昆明", 195),
    ("重庆", "昆明", 95), ("重庆", "西安", 95), ("西安", "昆明", 150),
    ("武汉", "成都", 135), ("厦门", "上海", 100), ("厦门", "深圳", 90),
]

CUSTOMER_SURNAMES = "张李王刘陈杨黄赵吴周徐马朱胡郭林何高罗郑梁谢宋唐许韩冯邓曹彭曾"
CUSTOMER_GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰涛明超秀霞平刚桂英"

COMPLAINT_TEMPLATES = [
    "客服人员态度生硬，要求按服务规范处理并赔偿",
    "退票后款项迟迟未到账，请尽快处理",
    "航班临时改期导致行程受影响，要求补偿",
    "行李在托运过程中遗失，要求按标准赔偿",
    "改签费用过高，要求说明收费依据",
    "机上餐食与宣传不符，要求解释",
]

STATUS_WEIGHTS = ["已出票"] * 75 + ["已退款"] * 8 + ["退票中"] * 5 + ["已改签"] * 5 + ["待支付"] * 7
COMPLAINT_STATUS = ["已解决"] * 5 + ["处理中"] * 3 + ["已升级"] * 2


def _row_rng(*parts) -> random.Random:
    """为 (航班号,日期,舱位) 等组合生成稳定随机源，保证增量/全量一致。"""
    digest = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:12], 16))


def _seed_base(conn: sqlite3.Connection, rnd: random.Random) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO airports (iata3, icao4, city_cn, city_en, lat, lon) "
        "VALUES (?,?,?,?,?,?)",
        [(a) for a in AIRPORTS],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO airlines (code, name_cn, is_lcc) VALUES (?,?,?)",
        [(c, n, l) for (c, n, l, f) in AIRLINES],
    )

    city2iata = {city: iata for iata, _i, city, _e, _la, _lo in AIRPORTS}
    airline_pool = [a[0] for a in AIRLINES]
    used_nums: set[str] = set()
    flights_rows = []
    delay_rows = []

    for ridx, (city_a, city_b, duration) in enumerate(ROUTES):
        for direction in (0, 1):
            dep_city, arr_city = (city_a, city_b) if direction == 0 else (city_b, city_a)
            dep_iata = city2iata[dep_city]
            arr_iata = city2iata[arr_city]
            n_flights = 3 + (ridx + direction) % 3  # 3~5 班/天
            for slot in range(n_flights):
                airline = airline_pool[(ridx + direction + slot) % len(airline_pool)]
                number = 1000 + ((ridx + 1) * 61 + slot * 13 + direction * 7) % 8999
                while number in used_nums:
                    number += 1
                used_nums.add(number)
                flight_no = f"{airline}{number}"

                dep_min = 6 * 60 + 40 + slot * 190 + (ridx % 4) * 9
                if dep_min > 21 * 60:
                    dep_min -= 4 * 60
                arr_min = dep_min + duration
                dep_time = f"{dep_min // 60:02d}:{dep_min % 60:02d}"
                arr_time = f"{arr_min // 60:02d}:{arr_min % 60:02d}"
                freq = "124567" if slot % 3 == 2 else "1234567"
                aircraft = "B737-800" if duration <= 120 else ("A320neo" if duration <= 200 else "A330-300")
                flights_rows.append(
                    (flight_no, airline, dep_iata, arr_iata, dep_time, arr_time, duration, aircraft, freq)
                )

                # 延误统计：该航司×该航线×三个时段
                lcc = dict((a[0], a[2]) for a in AIRLINES)[airline]
                for bucket, b_adj, m_adj in (
                    ("morning", -0.02, -3),
                    ("afternoon", 0.03, 4),
                    ("evening", 0.04, 7),
                ):
                    prob = (0.30 if lcc else 0.15) + b_adj + rnd.uniform(-0.03, 0.03)
                    mean_min = (30 if lcc else 18) + m_adj + rnd.uniform(-6, 6)
                    route = f"{dep_iata}-{arr_iata}"
                    delay_rows.append(
                        (airline, route, bucket, round(mean_min, 1), round(min(max(prob, 0.05), 0.45), 3), 365)
                    )

    conn.executemany(
        "INSERT OR REPLACE INTO flights VALUES (?,?,?,?,?,?,?,?,?)",
        flights_rows,
    )
    conn.executemany(
        """INSERT OR REPLACE INTO delay_stats
           (airline_code, route, time_bucket, mean_delay_min, delay_prob, sample_size)
           VALUES (?,?,?,?,?,?)""",
        delay_rows,
    )

    # 城市坐标（天气工具用）
    conn.executemany(
        "INSERT OR REPLACE INTO city_coords VALUES (?,?,?)",
        [(city, lat, lon) for _i, _icao, city, _e, lat, lon in AIRPORTS],
    )

    # 客户
    customers = []
    for i in range(40):
        member_id = f"M{1000 + i}"
        name = CUSTOMER_SURNAMES[i % len(CUSTOMER_SURNAMES)] + CUSTOMER_GIVEN[(i * 7) % len(CUSTOMER_GIVEN)]
        phone = f"138{rnd.randint(10000000, 99999999)}"
        level = "金卡" if i % 10 == 0 else ("银卡" if i % 10 in (1, 2, 3) else "普通")
        customers.append((member_id, name, phone, f"member{i}@example.com", level))
    conn.executemany(
        "INSERT OR REPLACE INTO customers VALUES (?,?,?,?,?)",
        customers,
    )


def _price_for(flight_no: str, flight_date: date, cabin: str, duration_min: int, airline: str) -> int:
    """稳定可复现的单行价格（全量生成与增量扩展结果一致）。"""
    rnd = _row_rng(flight_no, flight_date.isoformat(), cabin)
    info = dict((a[0], a) for a in AIRLINES)
    _code, _name, lcc_flag, price_factor = info[airline]

    base = round((250 + duration_min * 3.3) / 10) * 10
    days_ahead = (flight_date - date.today()).days
    if days_ahead <= 2:
        adv = 1.32
    elif days_ahead <= 7:
        adv = 1.15
    elif days_ahead <= 14:
        adv = 0.96
    elif days_ahead <= 45:
        adv = 0.88
    else:
        adv = 0.80
    seasonal = {1: 1.18, 2: 1.18, 5: 1.06, 7: 1.12, 8: 1.12, 10: 1.06}.get(flight_date.month, 1.0)
    if cabin == "商务":
        base = base * 2.6
        price_factor *= 0.95
    jitter = rnd.uniform(0.92, 1.08)
    price = int(round(base * adv * seasonal * price_factor * jitter / 10) * 10)
    return max(price, 200)


def _seed_prices(conn: sqlite3.Connection, start: date, end: date) -> int:
    """为 [start, end] 区间内生日的航班生成价格；返回插入行数。"""
    conn.execute("DELETE FROM flight_prices WHERE flight_date >= ? AND flight_date <= ?",
                 (start.isoformat(), end.isoformat()))
    flights = conn.execute(
        "SELECT f.flight_no, f.airline_code, f.freq_days, f.duration_min "
        "FROM flights f ORDER BY f.flight_no"
    ).fetchall()
    rows = []
    for f in flights:
        flight_no, airline, freq, duration = f["flight_no"], f["airline_code"], f["freq_days"], f["duration_min"]
        for i in range((end - start).days + 1):
            d = start + timedelta(days=i)
            if str(d.isoweekday()) not in freq:
                continue
            for cabin in ("经济", "商务"):
                rows.append((flight_no, d.isoformat(), cabin, _price_for(flight_no, d, cabin, duration, airline)))
    conn.executemany(
        "INSERT OR REPLACE INTO flight_prices (flight_no, flight_date, cabin, price) VALUES (?,?,?,?)",
        rows,
    )
    return len(rows)


def _seed_orders_and_complaints(conn: sqlite3.Connection, rnd: random.Random) -> None:
    conn.execute("DELETE FROM orders")
    conn.execute("DELETE FROM complaints")

    rows = conn.execute(
        "SELECT p.flight_no, p.flight_date, p.cabin, p.price FROM flight_prices p "
        "WHERE p.flight_date BETWEEN ? AND ? ORDER BY p.flight_no, p.flight_date, p.cabin",
        ((date.today() + timedelta(days=1)).isoformat(), (date.today() + timedelta(days=25)).isoformat()),
    ).fetchall()

    customers = conn.execute("SELECT member_id FROM customers ORDER BY member_id").fetchall()
    order_rows = []
    complaints = []
    used_orders = set()

    for ci, cust in enumerate(customers):
        member_id = cust["member_id"]
        n = 1 + ci % 3
        for _ in range(n):
            row = rnd.choice(rows)
            if row["flight_no"] + row["flight_date"] + row["cabin"] in used_orders:
                continue
            used_orders.add(row["flight_no"] + row["flight_date"] + row["cabin"])
            order_no = f"O{ci + 1:03d}{rnd.randint(1000, 9999)}"
            status = rnd.choice(STATUS_WEIGHTS)
            created_at = date.fromisoformat(row["flight_date"]) - timedelta(days=1 + rnd.randint(0, 14))
            order_rows.append((order_no, member_id, row["flight_no"], row["flight_date"],
                               row["cabin"], row["price"], status, created_at.isoformat()))

    conn.executemany(
        "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?)",
        order_rows,
    )

    # 投诉覆盖前 n 名会员（演示时提问"我要投诉"可查到名下记录）
    member_to_orders: dict[str, list[tuple]] = {}
    for r in order_rows:
        member_to_orders.setdefault(r[1], []).append(r)
    ordered_members = [cust["member_id"] for cust in customers[:12]]

    for i in range(len(COMPLAINT_TEMPLATES) * 2):
        member_id = ordered_members[i % len(ordered_members)]
        member_orders = member_to_orders.get(member_id) or []
        if not member_orders:
            continue
        order_no = member_orders[i % len(member_orders)][0]
        ticket_no = f"T{1000 + i}"
        content = COMPLAINT_TEMPLATES[(i + rnd.randint(0, 2)) % len(COMPLAINT_TEMPLATES)]
        status = rnd.choice(COMPLAINT_STATUS)
        created = date.today() - timedelta(days=1 + rnd.randint(0, 20))
        complaints.append((ticket_no, member_id, order_no, content, status, created.isoformat()))

    conn.executemany(
        "INSERT OR REPLACE INTO complaints VALUES (?,?,?,?,?,?)",
        complaints,
    )


def _seed_admin(conn: sqlite3.Connection) -> None:
    """幂等预置演示管理员 admin / admin123。"""
    from werkzeug.security import generate_password_hash
    conn.execute(
        "INSERT OR IGNORE INTO admins (username, password_hash, name) VALUES (?,?,?)",
        ("admin", generate_password_hash("admin123"), "运营管理员"),
    )
    conn.commit()


def ensure_seeded(conn: sqlite3.Connection, force: bool = False) -> None:
    """幂等初始化：建表 + 首次全量种子 + 按需补齐未来 30 天价格。"""
    db.init_schema(conn)

    if force:
        for table in ("complaints", "orders", "flight_prices", "delay_stats", "flights",
                      "airlines", "airports", "customers", "city_coords", "meta"):
            conn.execute(f"DELETE FROM {table}")

    _seed_admin(conn)

    count = conn.execute("SELECT COUNT(*) AS c FROM flights").fetchone()["c"]
    today = date.today()

    if count == 0:
        rnd = random.Random(SEED)
        _seed_base(conn, rnd)
        _seed_prices(conn, today, today + timedelta(days=HORIZON_DAYS))
        _seed_orders_and_complaints(conn, rnd)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('seed_version','1')")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('seed_date',?)", (today.isoformat(),))
        conn.commit()
        return

    # 增量：确保未来 30 天价格存在（每日运行）
    max_date_row = conn.execute("SELECT MAX(flight_date) AS d FROM flight_prices").fetchone()
    horizon_end = today + timedelta(days=HORIZON_DAYS)
    max_date = date.fromisoformat(max_date_row["d"]) if max_date_row and max_date_row["d"] else None
    if max_date is None or max_date < horizon_end:
        start = (max_date + timedelta(days=1)) if max_date and max_date > today else today
        _seed_prices(conn, start, horizon_end)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('seed_date',?)", (today.isoformat(),))
        conn.commit()


def reset_database() -> None:
    """清空并重建（调试用）。"""
    conn = db.get_connection()
    ensure_seeded(conn, force=True)
    conn.close()
