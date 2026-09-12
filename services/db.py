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

-- 行李规则：按 航司×舱位 定义随身/免费托运额度与超重、加件费率
CREATE TABLE IF NOT EXISTS baggage_rules (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    airline_code          TEXT NOT NULL REFERENCES airlines(code),
    cabin                 TEXT NOT NULL CHECK (cabin IN ('经济', '商务')),
    carry_on_pieces       INTEGER NOT NULL,
    carry_on_kg           INTEGER NOT NULL,
    free_checked_pieces   INTEGER NOT NULL,
    free_checked_kg       INTEGER NOT NULL,
    overweight_fee_per_kg INTEGER NOT NULL,
    extra_piece_fee       INTEGER NOT NULL,
    note                  TEXT,
    UNIQUE (airline_code, cabin)
);

CREATE INDEX IF NOT EXISTS idx_baggage_airline ON baggage_rules(airline_code);

CREATE TABLE IF NOT EXISTS city_coords (
    city TEXT PRIMARY KEY,
    lat  REAL NOT NULL,
    lon  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '服务通知',
    content    TEXT NOT NULL,
    ntype      TEXT NOT NULL DEFAULT 'system',
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_member ON notifications(member_id);

CREATE TABLE IF NOT EXISTS session_titles (
    thread_id  TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_no   TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    seat_no     TEXT NOT NULL,
    cabin       TEXT NOT NULL CHECK (cabin IN ('经济', '商务')),
    status      TEXT NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'occupied')),
    order_no    TEXT,
    UNIQUE (flight_no, flight_date, seat_no)
);

CREATE INDEX IF NOT EXISTS idx_seats_flight ON seats(flight_no, flight_date);

CREATE TABLE IF NOT EXISTS checkins (
    order_no      TEXT PRIMARY KEY,
    seat_no       TEXT NOT NULL,
    gate          TEXT NOT NULL,
    boarding_time TEXT NOT NULL,
    checkin_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_owners (
    thread_id  TEXT PRIMARY KEY,
    member_id  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_owners_member ON session_owners(member_id);

-- 支付流水：一次业务订单可发起多笔支付（失败重试），每笔独立 pay_no 作为幂等键
CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_no       TEXT UNIQUE NOT NULL,
    order_no     TEXT NOT NULL,
    provider     TEXT NOT NULL DEFAULT 'mock',
    out_trade_no TEXT NOT NULL,
    trade_no     TEXT,
    amount       REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT '待支付',
    buyer_id     TEXT,
    notify_raw   TEXT,
    created_at   TEXT NOT NULL,
    paid_at      TEXT,
    expire_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_order  ON payments(order_no);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);

-- 审计日志：越权尝试、注入/高危拦截、每笔写操作的执行与拒绝都落这里。
-- 刻意不参与 reset_database 的清库（审计必须留痕），也不被业务代码修改。
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event      TEXT NOT NULL,          -- write / denied / blocked / idempotent
    action     TEXT NOT NULL,          -- 订票/退票/改签/支付/值机/查询...
    actor      TEXT,                   -- 登录会员号 / admin:xxx / system
    target     TEXT,                   -- 订单号 / 投诉单号 / 航班号
    result     TEXT NOT NULL,          -- success / denied / error / replay
    request_id TEXT,                   -- 幂等键
    ip         TEXT,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_event   ON audit_logs(event, result);

-- 退款流水：request_id 是幂等键，防重复退款（同一请求重放只落一次）
CREATE TABLE IF NOT EXISTS refunds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT UNIQUE NOT NULL,
    order_no    TEXT NOT NULL,
    member_id   TEXT NOT NULL,
    refund_type TEXT NOT NULL,         -- voluntary / special
    amount      INTEGER NOT NULL DEFAULT 0,
    fee         INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,         -- 已退款 / 退票中
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_no);

-- 一次性确认凭证：写操作必须先拿到它（用户确认过），服务端消费后才执行。
-- 绑定 会员+动作+目标+参数指纹，用完即废，过期作废。
CREATE TABLE IF NOT EXISTS confirm_tokens (
    token       TEXT PRIMARY KEY,
    member_id   TEXT NOT NULL,
    action      TEXT NOT NULL,         -- book / pay / change / refund / checkin
    target      TEXT,
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_confirm_tokens_member ON confirm_tokens(member_id);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等加列（SQLite ALTER TABLE 不支持 IF NOT EXISTS）。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接（行工厂为 Row，支持外键）。"""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 模式：读写不互斥，避免多浏览器并发演示时报 database is locked
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(SCHEMA)
    # 轻量迁移：管理端新增字段（幂等）
    _ensure_column(conn, "orders", "refund_amount", "refund_amount INTEGER")
    _ensure_column(conn, "orders", "refund_reason", "refund_reason TEXT")
    _ensure_column(conn, "orders", "admin_note", "admin_note TEXT")
    _ensure_column(conn, "orders", "passengers", "passengers INTEGER DEFAULT 1")
    _ensure_column(conn, "orders", "prev_status", "prev_status TEXT")
    _ensure_column(conn, "complaints", "reply", "reply TEXT")
    # 会员注册：口令哈希列（演示账号为 NULL，走手机尾号登录）
    _ensure_column(conn, "customers", "password_hash", "password_hash TEXT")
    # 退款完成时间（趋势图表统计用；存量已退款订单为 NULL 不计入）
    _ensure_column(conn, "orders", "refunded_at", "refunded_at TEXT")
    # 支付接入：支付渠道（mock/alipay_sandbox/alipay/wechat）与支付完成时间
    _ensure_column(conn, "orders", "pay_channel", "pay_channel TEXT")
    _ensure_column(conn, "orders", "paid_at", "paid_at TEXT")
    # 航班登机口（航班级物理资源，管理端指派；NULL 时值机侧按航班+日期确定性兜底）
    _ensure_column(conn, "flights", "gate", "gate TEXT")
    # 管理员首次登录强制改密标记（存量默认口令由启动巡检置位）
    _ensure_column(conn, "admins", "must_change_password", "must_change_password INTEGER NOT NULL DEFAULT 0")
    # 种子订单未记录人数（金额即单人票价），统一按1人回填
    conn.execute("UPDATE orders SET passengers = 1 WHERE passengers IS NULL")
    conn.commit()
