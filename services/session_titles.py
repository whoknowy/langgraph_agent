"""会话标题：SQLite 仓库 + 后台轻量 LLM 生成。

设计要点：
- 标题以 thread_id 为主键存 session_titles 表（事实源与业务数据同库）；
- 流式 done 后由后台线程生成（不阻塞 SSE）：轻量 LLM 生成 6~10 字标题，
  失败兜底为首条用户消息截断；LLM 依赖（langchain）延迟导入，仓库层保持零重依赖，
  纯逻辑单测不需要装 LLM 栈也能跑；
- 已有标题的线程不重复生成（首条用户消息即最佳标题依据，避免每轮烧 token）。
"""

import logging
import threading
from datetime import datetime

from services import db

logger = logging.getLogger(__name__)

# 兜底标题的最大长度（LLM 目标 6~10 字，兜底放宽到 14 字内更可读）
FALLBACK_MAX_LEN = 14


# ---------------------------------------------------------------- 仓库层

def get_title(thread_id: str) -> str:
    """取会话标题；不存在返回空串。"""
    if not thread_id:
        return ""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT title FROM session_titles WHERE thread_id = ?",
                           ((thread_id or "").strip(),)).fetchone()
        return (row["title"] or "") if row else ""
    finally:
        conn.close()


def save_title(thread_id: str, title: str) -> bool:
    """写入/覆盖会话标题。thread_id 或清洗后的 title 为空时忽略。"""
    tid = (thread_id or "").strip()
    cleaned = clean_title(title)
    if not tid or not cleaned:
        return False
    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO session_titles (thread_id, title, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(thread_id) DO UPDATE SET title = excluded.title, updated_at = excluded.updated_at",
            (tid, cleaned, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    finally:
        conn.close()


def delete_title(thread_id: str) -> None:
    """删除会话时同步清理标题行（幂等）。"""
    tid = (thread_id or "").strip()
    if not tid:
        return
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM session_titles WHERE thread_id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 纯逻辑

def clean_title(title: str) -> str:
    """标题清洗：去首尾空白、压平内部换行/连续空白。"""
    return " ".join(((title or "").split()))[:60]


def fallback_title(first_user_message: str) -> str:
    """LLM 失败兜底：首条用户消息压平后截断（超出省略号）。"""
    text = " ".join(((first_user_message or "").split()))
    if len(text) <= FALLBACK_MAX_LEN:
        return text
    return text[:FALLBACK_MAX_LEN] + "…"


# ---------------------------------------------------------------- 生成层

def _invoke_llm(text: str) -> str:
    """轻量 LLM 生成标题（延迟导入 LLM 栈；模块级函数便于单测注入失败场景）。"""
    from agents import create_llm
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = create_llm()
    resp = llm.invoke([
        SystemMessage(content="你是会话标题生成器。根据用户第一句话生成一个 6~10 字的对话标题，"
                             "概括核心诉求。只输出标题本身，不要标点结尾、不要引号、不要解释。"),
        HumanMessage(content=text),
    ])
    return clean_title(getattr(resp, "content", "") or "")


def generate_title(first_user_message: str) -> str:
    """同步生成标题：轻量 LLM 优先，任何异常兜底为首条消息截断。"""
    text = (first_user_message or "").strip()
    if not text:
        return ""
    try:
        title = _invoke_llm(text[:200])
        if title:
            return title
    except Exception as e:
        logger.warning("LLM 生成会话标题失败，使用兜底截断: %s", e)
    return fallback_title(text)


def generate_title_async(thread_id: str, first_user_message: str) -> threading.Thread | None:
    """后台线程生成并落库标题；已有标题或输入无效时直接返回 None（不烧 token）。"""
    tid = (thread_id or "").strip()
    if not tid or not (first_user_message or "").strip():
        return None
    if get_title(tid):
        return None

    def _work():
        try:
            title = generate_title(first_user_message)
            try:
                from skills import mask_sensitive
                title = mask_sensitive(title)
            except Exception:
                pass
            if save_title(tid, title):
                print(f"✅ 会话标题已生成: {tid[:8]}… → {title}")
        except Exception as e:
            logger.warning("会话标题生成任务失败: %s", e)

    t = threading.Thread(target=_work, name="session-title", daemon=True)
    t.start()
    return t
