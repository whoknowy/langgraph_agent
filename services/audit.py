"""
审计日志：谁、在什么身份下、对哪个对象、做了什么、结果如何。

设计要点：
- **只追加，不修改**；刻意不参与 `reset_database(force=True)` 的清库（见 db_seed），
  审计必须留痕；
- 写入失败**绝不影响业务**（整体 try/except 吞掉异常，只打印告警）；
- 事件类型（event）：
  - `write`      写操作执行（成功/失败都记）
  - `denied`     越权尝试被拒（这就是"越权尝试拦截率"的分子/分母来源）
  - `blocked`    上游高危拦截（敏感词/注入话术）
  - `idempotent` 幂等命中（重复 requestId / 重复确认凭证）
"""

import json
from datetime import datetime, timedelta

from services import db, security

EVENT_WRITE = "write"
EVENT_DENIED = "denied"
EVENT_BLOCKED = "blocked"
EVENT_IDEMPOTENT = "idempotent"

RESULT_SUCCESS = "success"
RESULT_DENIED = "denied"
RESULT_ERROR = "error"
RESULT_REPLAY = "replay"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_ready_path = None


def _ensure_schema(conn) -> None:
    """同一个库文件在本进程内只初始化一次。

    不要在每条审计里跑 init_schema：它内部是 executescript(DDL) + UPDATE，
    高并发（LangGraph 进程与 Flask 进程同时写）下容易拿不到写锁。
    按 DB 路径缓存（而不是布尔开关），换库（如单测的临时库）时会重新初始化。
    """
    global _ready_path
    path = str(db.DB_PATH)
    if _ready_path != path:
        db.init_schema(conn)
        _ready_path = path


def _client_ip() -> str:
    """尽力取调用方 IP；不在 Flask 请求上下文时返回空串。"""
    try:
        from flask import request, has_request_context
        if has_request_context():
            return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                    or request.remote_addr or "")
    except Exception:
        pass
    return ""


def log(event: str, action: str, result: str, *, actor: str = None, target: str = None,
        request_id: str = None, detail=None, ip: str = None) -> None:
    """写一条审计记录。任何异常都被吞掉——审计不能拖垮业务。"""
    try:
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False, default=str)
        conn = db.get_connection()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO audit_logs (created_at, event, action, actor, target, result, "
                "request_id, ip, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                (_now(), event, action, actor or security.current_actor(),
                 (target or "")[:120], result, request_id,
                 (ip if ip is not None else _client_ip())[:64], (detail or "")[:500]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover - 审计失败不影响业务
        print(f"⚠️ [审计] 写入失败：{e}")


def write_ok(action: str, target: str = None, *, actor: str = None, request_id: str = None,
             detail=None) -> None:
    log(EVENT_WRITE, action, RESULT_SUCCESS, actor=actor, target=target,
        request_id=request_id, detail=detail)


def write_error(action: str, reason: str, target: str = None, *, actor: str = None,
                request_id: str = None) -> None:
    log(EVENT_WRITE, action, RESULT_ERROR, actor=actor, target=target,
        request_id=request_id, detail=reason)


def denied(action: str, reason: str, target: str = None, *, actor: str = None) -> None:
    """越权/身份不符被拒 —— 这是"越权尝试拦截率"的统计来源。"""
    log(EVENT_DENIED, action, RESULT_DENIED, actor=actor, target=target, detail=reason)


def blocked(action: str, reason: str, target: str = None, *, actor: str = None) -> None:
    """上游高危拦截（敏感词/注入话术）。"""
    log(EVENT_BLOCKED, action, RESULT_DENIED, actor=actor, target=target, detail=reason)


def idempotent(action: str, target: str = None, *, actor: str = None, request_id: str = None,
               detail=None) -> None:
    """幂等命中（重复请求被识别为同一笔，未重复执行）。"""
    log(EVENT_IDEMPOTENT, action, RESULT_REPLAY, actor=actor, target=target,
        request_id=request_id, detail=detail)


def recent(limit: int = 100, event: str = None, action: str = None) -> list:
    """最近的审计记录（管理端排查用）。"""
    conn = db.get_connection()
    try:
        _ensure_schema(conn)
        sql = "SELECT * FROM audit_logs WHERE 1=1"
        params = []
        if event:
            sql += " AND event = ?"
            params.append(event)
        if action:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def stats(days: int = 7) -> dict:
    """审计统计：写操作数、越权尝试拦截率、高危拦截数、幂等命中数。

    越权尝试拦截率 = 被拒的越权尝试 / 全部越权尝试（被拒 + 未遂放行）。
    由于所有写操作都在仓库层校验归属，"放行"的越权请求在代码层不可能发生，
    因此分母以 denied 事件为准，拦截率恒为 100% 除非出现 denied 之外的异常路径。
    """
    since = (datetime.now() - timedelta(days=max(1, int(days or 7)))).strftime("%Y-%m-%d 00:00:00")
    conn = db.get_connection()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT event, result, COUNT(*) AS c FROM audit_logs WHERE created_at >= ? "
            "GROUP BY event, result", (since,)).fetchall()
    finally:
        conn.close()

    counts = {}
    for r in rows:
        counts[(r["event"], r["result"])] = r["c"]
    denied_n = counts.get((EVENT_DENIED, RESULT_DENIED), 0)
    writes_ok = counts.get((EVENT_WRITE, RESULT_SUCCESS), 0)
    return {
        "since": since,
        "writes_success": writes_ok,
        "writes_error": counts.get((EVENT_WRITE, RESULT_ERROR), 0),
        "denied_attempts": denied_n,
        "blocked_high_risk": counts.get((EVENT_BLOCKED, RESULT_DENIED), 0),
        "idempotent_hits": counts.get((EVENT_IDEMPOTENT, RESULT_REPLAY), 0),
        # 越权尝试只要被识别就一定被拒（仓库层硬校验），故分母=denied 数
        "denied_rate": 1.0 if denied_n else None,
    }
