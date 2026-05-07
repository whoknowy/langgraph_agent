"""
Skill模块
提供Skill的标准接口、注册和调度机制
"""

from .skill_base import (
    Skill,
    SkillType,
    SkillResult,
    SkillRegistry,
    SkillDispatcher,
    skill_registry,
    skill_dispatcher
)

from .sensitive_word_filter import (
    SensitiveWordFilterSkill,
    sensitive_word_filter_skill
)

from .intent_router import (
    IntentRoutingSkill,
    AgentDispatchSkill,
    intent_routing_skill,
    agent_dispatch_skill
)

from .tool_dispatcher import (
    ToolDispatchSkill,
    tool_dispatch_skill
)

from .embedding_intent_classifier import (
    IntentKnowledgeBase,
    EmbeddingService,
    EmbeddingIntentClassifier,
    embedding_intent_classifier
)

def register_all_skills():
    """注册所有Skill到全局注册表"""
    skill_registry.register(sensitive_word_filter_skill)
    skill_registry.register(intent_routing_skill)
    skill_registry.register(tool_dispatch_skill)
    skill_registry.register(agent_dispatch_skill)

__all__ = [
    # 基类
    "Skill",
    "SkillType",
    "SkillResult",
    "SkillRegistry",
    "SkillDispatcher",

    # 注册表实例
    "skill_registry",
    "skill_dispatcher",

    # 敏感词过滤Skill
    "SensitiveWordFilterSkill",
    "sensitive_word_filter_skill",

    # 意图路由Skill
    "IntentRoutingSkill",
    "AgentDispatchSkill",
    "intent_routing_skill",
    "agent_dispatch_skill",

    # 工具调度Skill
    "ToolDispatchSkill",
    "tool_dispatch_skill",

    # Embedding意图分类器
    "IntentKnowledgeBase",
    "EmbeddingService",
    "EmbeddingIntentClassifier",
    "embedding_intent_classifier",

    # 注册函数
    "register_all_skills"
]