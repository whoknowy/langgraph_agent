"""
SQLite 数据层：连接与建表。

模拟"真实民航系统"的航班/价格/延误/客户/订单/投诉数据持久层，
供 function-calling 工具与 Repository 查询使用。
数据库文件：data/flight_system.db（首次启动自动建表，参见 db_seed.ensure_seeded）。
"""

from pathlib import Path
import sqlite3

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "flight_system.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS airports (
    iata3    TEXT PRIMARY KEY,
    icao4    TEXT,
    city_cn  TEXT UNIQUE,
    city_en  TEXT,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    timezone TEXT DEFAULT 'Asia/Shanghai'
);

CREATE TABLE IF NOT EXISTS airlines (
    code    TEXT PRIMARY KEY,
    name_cn TEXT NOT NULL,
    is_lcc  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS flights (
    flight_no    TEXT PRIMARY KEY,
    airline_code TEXT NOT NULL REFERENCES airlines(code),
    dep_iata     TEXT NOT NULL REFERENCES airports(iata3),
    arr_iata     TEXT NOT NULL REFERENCES airports(iata3),
    dep_time     TEXT NOT NULL,
    arr_time     TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    aircraft     TEXT,
    freq_days    TEXT NOT NULL DEFAULT '1234567'
);

CREATE TABLE IF NOT EXISTS flight_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_no   TEXT NOT NULL REFERENCES flights(flight_no),
    flight_date TEXT NOT NULL,
    cabin       TEXT NOT NULL CHECK (cabin IN ('经济', '商务')),
    price       INTEGER NOT NULL,
    currency    TEXT DEFAULT 'CNY',
    UNIQUE (flight_no, flight_date, cabin)
);

CREATE INDEX IF NOT EXISTS idx_prices_date   ON flight_prices(flight_date);
CREATE INDEX IF NOT EXISTS idx_prices_flight ON flight_prices(flight_no);

CREATE TABLE IF NOT EXISTS delay_stats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    airline_code   TEXT NOT NULL,
    route          TEXT NOT NULL,
    time_bucket    TEXT NOT NULL CHECK (time_bucket IN ('morning', 'afternoon', 'evening')),
    mean_delay_min REAL NOT NULL,
    delay_prob     REAL NOT NULL,
    sample_size    INTEGER NOT NULL,
    UNIQUE (airline_code, route, time_bucket)
);

CREATE TABLE IF NOT EXISTS customers (
    member_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    phone     TEXT NOT NULL,
    email     TEXT,
    level     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_no    TEXT PRIMARY KEY,
    member_id   TEXT NOT NULL REFERENCES customers(member_id),
    flight_no   TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    cabin       TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_member   ON orders(member_id);
CREATE INDEX IF NOT EXISTS idx_orders_flight   ON orders(flight_no);

CREATE TABLE IF NOT EXISTS complaints (
    ticket_no  TEXT PRIMARY KEY,
    member_id  TEXT NOT NULL,
    order_no   TEXT,
    content    TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS city_coords (
    city TEXT PRIMARY KEY,
    lat  REAL NOT NULL,
    lon  REAL NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接（行工厂为 Row，支持外键）。"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(SCHEMA)
    conn.commit()
