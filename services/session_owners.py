"""会话线程归属仓库：thread_id ↔ member_id 的绑定与校验。

安全设计：LangGraph 线程创建后即在此登记归属；会话的列表/详情/删除/清空/
聊天复用线程前，一律以本表的归属关系为准（数据库为事实源，重启不丢）。
未登记的存量线程视为无主，不暴露给任何会员（历史测试残留自然隐藏）。
"""

from datetime import datetime

from services import db


def _norm(s: str) -> str:
    return (s or "").strip().upper()


def bind(thread_id: str, member_id: str) -> None:
    """登记线程归属（幂等；首次绑定后后续调用不改变归属）。"""
    tid, mid = (thread_id or "").strip(), _norm(member_id)
    if not tid or not mid:
        return
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO session_owners (thread_id, member_id, created_at) VALUES (?,?,?) "
            "ON CONFLICT(thread_id) DO NOTHING",
            (tid, mid, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()


def owner_of(thread_id: str) -> str:
    """查询线程归属会员号；未登记返回空串。"""
    if not (thread_id or "").strip():
        return ""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT member_id FROM session_owners WHERE thread_id = ?",
                           ((thread_id or "").strip(),)).fetchone()
        return (row["member_id"] or "") if row else ""
    finally:
        conn.close()


def owned_by(member_id: str) -> set:
    """某会员名下的全部线程 ID 集合（会话列表过滤用）。"""
    mid = _norm(member_id)
    if not mid:
        return set()
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT thread_id FROM session_owners WHERE member_id = ?",
                            (mid,)).fetchall()
        return {r["thread_id"] for r in rows}
    finally:
        conn.close()


def owned(thread_id: str, member_id: str) -> bool:
    """校验线程是否属于指定会员（未登记线程一律视为不属于任何人）。"""
    return owner_of(thread_id) == _norm(member_id) and bool(_norm(member_id))


def release(thread_id: str) -> None:
    """删除线程时同步清除归属记录（幂等）。"""
    if not (thread_id or "").strip():
        return
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM session_owners WHERE thread_id = ?",
                     ((thread_id or "").strip(),))
        conn.commit()
    finally:
        conn.close()
