"""
一次性确认凭证（confirm_token）：把"用户确认过了"这件事做成**服务端可验证**的凭据。

背景：HITL 原本只靠前端自觉——用户点确认卡片才调写接口，但服务端不验证，
任何登录态都能直接 POST 写接口执行。本模块让服务端强制这一点。

流程：
1. **签发**（准备阶段）：报价接口 / 聊天确认卡片（伪工具钩子）调用 `issue()`，
   凭证绑定 `会员 + 动作 + 目标 + 参数指纹 + 过期时间`；
2. **消费**（执行阶段）：写接口调用 `consume()`，四项全对且未用过才放行，
   放行即原子置 `used_at`，**用完即废**。

安全性质：
- 没有凭证 → 写接口一律 403，裸调不再可执行；
- 凭证跨会员/跨动作/跨订单都不可用（归属与目标绑定）；
- 参数被篡改（如确认改签到 A 班却提交 B 班）会被指纹挡下；
- 重放无效（一次性）；过期无效。
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from services import db, security

DEFAULT_TTL_SECONDS = 900   # 15 分钟，覆盖"看到卡片后去想一会儿"

# 各动作需要纳入指纹的参数（这些参数一旦在确认后被改动就必须拒绝）
FINGERPRINT_KEYS = {
    "book": ("flight_no", "flight_date", "cabin", "passengers"),
    "change": ("new_flight_no", "new_date", "new_cabin"),
    "refund": ("refund_type",),
    "pay": (),
    "checkin": (),
}

ACTION_LABEL = {
    "book": "订票", "pay": "支付", "change": "改签", "refund": "退票", "checkin": "值机选座",
}


def _now() -> datetime:
    return datetime.now()


_ready_path = None


def _ensure_schema(conn) -> None:
    """同一个库文件在本进程内只初始化一次（按路径缓存，换库会重新初始化）。"""
    global _ready_path
    path = str(db.DB_PATH)
    if _ready_path != path:
        db.init_schema(conn)
        _ready_path = path


def _norm_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip().lower()
    for ch in (" ", "舱"):
        s = s.replace(ch, "")
    return s


def fingerprint(action: str, params: dict) -> str:
    """参数指纹：只取该动作关心的字段，排序后哈希，用于检测确认后的参数篡改。"""
    keys = FINGERPRINT_KEYS.get(action, ())
    raw = "|".join(f"{k}={_norm_value((params or {}).get(k))}" for k in sorted(keys))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def issue(action: str, member_id: str, target: str = "", params: dict = None,
          ttl: int = DEFAULT_TTL_SECONDS) -> dict:
    """签发一次性确认凭证。返回含 `confirm_token` 的字典，供前端回传。"""
    member_id = security.normalize(member_id)
    if not member_id:
        return {"error": "未登录：无法签发确认凭证"}
    token = secrets.token_urlsafe(24)
    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl or DEFAULT_TTL_SECONDS)))
    conn = db.get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO confirm_tokens (token, member_id, action, target, fingerprint, "
            "created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
            (token, member_id, action, security.normalize(target),
             fingerprint(action, params),
             now.strftime("%Y-%m-%d %H:%M:%S"),
             expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()
    return {"confirm_token": token,
            "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "action_label": ACTION_LABEL.get(action, action)}


def consume(token: str, action: str, member_id: str, target: str = "",
            params: dict = None) -> dict:
    """校验并消费凭证。通过返回 `{"ok": True}`；否则返回 `{"error": ...}`。"""
    label = ACTION_LABEL.get(action, action)
    if not token:
        return {"error": f"{label}需要用户确认：缺少确认凭证（confirm_token）"}
    conn = db.get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute("SELECT * FROM confirm_tokens WHERE token = ?", (token,)).fetchone()
        if not row:
            return {"error": f"确认凭证无效：请重新发起{label}并在页面上确认"}
        if row["used_at"]:
            return {"error": f"该确认凭证已被使用：请重新发起{label}并再次确认"}
        if row["expires_at"] < _now().strftime("%Y-%m-%d %H:%M:%S"):
            return {"error": f"确认凭证已过期：请重新发起{label}并再次确认"}
        if row["action"] != action:
            return {"error": f"确认凭证与操作不符（凭证为{ACTION_LABEL.get(row['action'], row['action'])}）"}
        login_id = security.normalize(member_id)
        if row["member_id"] != login_id:
            return {"error": "确认凭证不属于当前登录会员"}
        if row["target"] and row["target"] != security.normalize(target):
            return {"error": f"确认凭证与目标不符：确认的是 {row['target']}"}
        if row["fingerprint"] != fingerprint(action, params):
            return {"error": f"提交内容与确认时不一致：请重新发起{label}并确认"}

        cur = conn.execute(
            "UPDATE confirm_tokens SET used_at = ? WHERE token = ? AND used_at IS NULL",
            (_now().strftime("%Y-%m-%d %H:%M:%S"), token))
        if cur.rowcount != 1:
            return {"error": f"确认凭证刚刚已被使用：请重新发起{label}"}
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def purge_expired(keep_days: int = 2) -> int:
    """清理过期凭证（保留最近几天便于排查）。返回删除行数。"""
    cutoff = (_now() - timedelta(days=max(1, int(keep_days or 2)))).strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_connection()
    try:
        _ensure_schema(conn)
        cur = conn.execute("DELETE FROM confirm_tokens WHERE expires_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
