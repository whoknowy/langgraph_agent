"""
输入守卫：敏感词过滤（AC 自动机，本地匹配，无外部依赖）

保留原三级词库与匹配器；职责收敛为两件事：
1. L1/L2（不满、抱怨等表达）→ 放行，交给意图分类与对应 Agent 处理；
2. L3（辱骂、威胁、违法等高危词）→ 直接拦截，返回固定话术，不进入后续流程。

旧版的工单创建、坐席分配、安抚话术拼装已随 workstation/ticket_system 一并移除。
"""

from typing import Dict

from agents.sensitive_words import sensitivity_knowledge_base
from agents.ac_automaton import create_matcher_from_knowledge_base

_matcher = create_matcher_from_knowledge_base(sensitivity_knowledge_base)

_BLOCK_RESPONSE = (
    "非常抱歉，您的消息中包含我们不便于处理的内容。"
    "如果您遇到了服务问题，请换一种方式描述具体问题，我们会认真对待并尽快为您解决。"
)


def run_sensitive_guard(query: str) -> Dict:
    """对用户输入做敏感词匹配，返回守卫结果。

    Returns:
        {
            "has_sensitive": bool,     # 是否命中敏感词（任意等级）
            "level": int,              # 最高命中等级（0~3）
            "matched_words": [str],
            "blocked": bool,           # L3 高危 → 拦截
            "response": str,           # 拦截时的话术，放行为 ""
        }
    """
    if not query:
        return {"has_sensitive": False, "level": 0, "matched_words": [],
                "blocked": False, "response": ""}

    has_sensitive, highest_level, matches = _matcher.match(query)
    matched_words = [w for w, _lvl in matches]
    blocked = has_sensitive and highest_level >= 3

    return {
        "has_sensitive": has_sensitive,
        "level": highest_level,
        "matched_words": matched_words,
        "blocked": blocked,
        "response": _BLOCK_RESPONSE if blocked else "",
    }
