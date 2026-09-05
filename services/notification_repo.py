"""
站内通知：管理端动作与系统事件触达会员的轻量通道。

写侧（同事务）：退款审批通过/驳回、投诉回复、订单超时自动取消等事件发生时插入一条；
读侧：会员端「我的数据」面板展示、query_notifications 工具供智能体转告。
"""

from datetime import datetime

from services import db


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_notification(member_id: str, content: str, title: str = "服务通知",
                        ntype: str = "system", conn=None) -> None:
    """写入一条通知。传入 conn 时不提交（与调用方的业务写操作同事务）。"""
    own = conn is None
    c = conn or db.get_connection()
    try:
        c.execute(
            "INSERT INTO notifications (member_id, title, content, ntype, is_read, created_at) "
            "VALUES (?,?,?,?,0,?)",
            (member_id, title or "服务通知", content, ntype or "system", _now()))
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def list_notifications(member_id: str, limit: int = 50, unread_only: bool = False) -> list:
    """某会员的通知列表（最新在前）。"""
    conn = db.get_connection()
    try:
        sql = ("SELECT id, title, content, ntype, is_read, created_at FROM notifications "
               "WHERE member_id = ?")
        if unread_only:
            sql += " AND is_read = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = conn.execute(sql, (member_id, int(limit))).fetchall()
        return [
            {
                "id": r["id"], "title": r["title"], "content": r["content"],
                "ntype": r["ntype"], "is_read": bool(r["is_read"]), "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def unread_count(member_id: str) -> int:
    conn = db.get_connection()
    try:
        r = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE member_id = ? AND is_read = 0",
                         (member_id,)).fetchone()
        return int(r["n"]) if r else 0
    finally:
        conn.close()


def mark_all_read(member_id: str) -> int:
    """全部置为已读，返回影响行数。"""
    conn = db.get_connection()
    try:
        cur = conn.execute("UPDATE notifications SET is_read = 1 WHERE member_id = ? AND is_read = 0",
                           (member_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
