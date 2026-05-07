"""
记忆管理包
包含会话管理和增强记忆功能
"""

from .session_manager import (
    LangChainSessionManager,
    default_session_manager,
    create_session,
    get_session,
    add_message,
    get_conversation_context,
    list_sessions,
    clear_session,
    delete_session,
    get_enhanced_context,
    check_session_summary,
    summarize_session,
    store_complaint_memory
)

from .enhanced_memory import (
    EnhancedMemoryManager,
    get_memory_manager,
    add_message as add_memory_message,
    add_long_term_memory,
    get_context as get_enhanced_context,
    clear_memory as clear_enhanced_memory
)

from .session_monitor import (
    SessionMonitor,
    start_session_monitor,
    stop_session_monitor
)

__all__ = [
    # 会话管理
    "LangChainSessionManager",
    "default_session_manager",
    "create_session",
    "get_session",
    "add_message",
    "get_conversation_context",
    "list_sessions",
    "clear_session",
    "delete_session",
    "get_enhanced_context",
    "check_session_summary",
    "summarize_session",
    "store_complaint_memory",
    
    # 会话监控
    "SessionMonitor",
    "start_session_monitor",
    "stop_session_monitor",
    
    # 增强记忆
    "EnhancedMemoryManager",
    "get_memory_manager",
    "add_memory_message",
    "add_long_term_memory",
    "clear_enhanced_memory"
]
