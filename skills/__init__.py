"""
Skill 模块

仅保留两个图节点所需的轻量组件：
- sensitive_word_filter.run_sensitive_guard : 输入守卫（敏感词拦截）
- intent_classifier.classify_intent         : 单次 LLM 意图分类

旧版的 Skill/SkillResult 注册与调度机制已随工具模拟层一并移除。
"""

from .sensitive_word_filter import run_sensitive_guard, mask_sensitive, StreamMasker
from .intent_classifier import classify_intent, AGENT_OPTIONS

__all__ = [
    "run_sensitive_guard", "mask_sensitive", "StreamMasker",
    "classify_intent",
    "AGENT_OPTIONS",
]
