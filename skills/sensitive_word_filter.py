"""
敏感词过滤Skill
基于敏感词知识库进行内容安全过滤
"""

from typing import Dict, List, Any
from skills.skill_base import Skill, SkillType, SkillResult
from agents.sensitive_words import sensitivity_knowledge_base
from agents.ac_automaton import create_matcher_from_knowledge_base

class SensitiveWordFilterSkill(Skill):
    """
    敏感词过滤Skill
    识别用户输入中的敏感词并采取相应措施
    """

    def __init__(self):
        super().__init__(
            name="sensitive_word_filter",
            description="敏感词过滤器：识别用户输入中的敏感词（L1/L2/L3），根据等级采取相应措施",
            skill_type=SkillType.FILTER,
            keywords=["敏感词", "过滤", "审核", "安全"],
            priority=100  # 高优先级，在其他Skill之前执行
        )
        self.knowledge_base = sensitivity_knowledge_base
        self.matcher = create_matcher_from_knowledge_base(self.knowledge_base)

    def can_handle(self, context: Dict[str, Any]) -> bool:
        """所有输入都需要经过敏感词过滤"""
        return True  # 始终可以处理，用于前置过滤

    def execute(self, context: Dict[str, Any]) -> SkillResult:
        """
        执行敏感词过滤

        Args:
            context: {
                "query": 用户输入,
                "session_id": 会话ID,
                "customer_info": 客户信息(可选)
            }

        Returns:
            SkillResult: {
                "success": 是否成功,
                "data": {
                    "has_sensitive": 是否有敏感词,
                    "level": 最高敏感等级,
                    "matched_words": 匹配到的敏感词列表,
                    "action": 处理动作,
                    "response": 回复内容,
                    "mood_tag": 情绪标签,
                    "proceed": 是否继续后续处理
                },
                "next_action": "continue"或"stop"
            }
        """
        try:
            query = context.get("query", "")
            session_id = context.get("session_id", "")
            customer_info = context.get("customer_info", {})

            if not query:
                return SkillResult.error_result("Query is empty")

            # 执行敏感词匹配
            has_sensitive, highest_level, matched_words = self.matcher.match(query)

            if not has_sensitive or highest_level == 0:
                # 无敏感词，正常流程
                return SkillResult.success_result(
                    data={
                        "has_sensitive": False,
                        "level": 0,
                        "matched_words": [],
                        "action": "放行",
                        "response": "",
                        "mood_tag": "",
                        "proceed": True
                    },
                    metadata={"level_name": "无"},
                    next_action="continue"
                )

            # L3 高危处理
            if highest_level == 3:
                return self._handle_l3(query, session_id, matched_words, customer_info)

            # L2 中危处理
            if highest_level == 2:
                return self._handle_l2(query, session_id, matched_words, customer_info)

            # L1 低危处理
            if highest_level == 1:
                return self._handle_l1(query, matched_words)

            return SkillResult.success_result(
                data={
                    "has_sensitive": False,
                    "level": 0,
                    "matched_words": [],
                    "action": "放行",
                    "response": "",
                    "mood_tag": "",
                    "proceed": True
                },
                next_action="continue"
            )

        except Exception as e:
            return SkillResult.error_result(f"Sensitive word filter error: {str(e)}")

    def _handle_l3(self, query: str, session_id: str, matched_words: List, customer_info: Dict) -> SkillResult:
        """处理L3高危敏感词"""
        from agents.ticket_system import ticket_system
        from agents.workstation import workstation

        # 创建工单
        ticket = ticket_system.create_ticket(
            session_id=session_id,
            customer_query=query,
            sensitivity_level=3,
            matched_words=matched_words,
            customer_info=customer_info
        )

        # 创建外呼任务
        task = workstation.create_call_task(
            ticket_id=ticket.ticket_id,
            session_id=session_id,
            customer_query=query,
            sensitivity_level=3,
            customer_info=customer_info
        )

        # 尝试自动分配
        workstation.auto_assign_task(sensitivity_level=3)

        return SkillResult.success_result(
            data={
                "has_sensitive": True,
                "level": 3,
                "matched_words": matched_words,
                "action": "标记高危并继续处理",
                "response": "您的反馈我们已经收到，我们会认真对待并尽快处理。",
                "mood_tag": "high_risk",
                "proceed": True,
                "ticket_id": ticket.ticket_id,
                "task_id": task.task_id
            },
            metadata={
                "level_name": "L3高危",
                "action": "标记高危并继续处理"
            },
            next_action="continue"
        )

    def _handle_l2(self, query: str, session_id: str, matched_words: List, customer_info: Dict) -> SkillResult:
        """处理L2中危敏感词"""
        from agents.ticket_system import ticket_system
        from agents.workstation import workstation

        # 创建工单
        ticket = ticket_system.create_ticket(
            session_id=session_id,
            customer_query=query,
            sensitivity_level=2,
            matched_words=matched_words,
            customer_info=customer_info
        )

        # 创建外呼任务
        task = workstation.create_call_task(
            ticket_id=ticket.ticket_id,
            session_id=session_id,
            customer_query=query,
            sensitivity_level=2,
            customer_info=customer_info
        )

        # 尝试自动分配
        workstation.auto_assign_task(sensitivity_level=2)

        return SkillResult.success_result(
            data={
                "has_sensitive": True,
                "level": 2,
                "matched_words": matched_words,
                "action": "标记中危并继续处理",
                "response": "感谢您的反馈，我们非常重视您的问题，会尽快安排处理。",
                "mood_tag": "medium_risk",
                "proceed": True,
                "ticket_id": ticket.ticket_id,
                "task_id": task.task_id
            },
            metadata={
                "level_name": "L2中危",
                "action": "标记中危并继续处理"
            },
            next_action="continue"
        )

    def _handle_l1(self, query: str, matched_words: List) -> SkillResult:
        """处理L1低危敏感词"""
        comfort_template = self.knowledge_base.get_comfort_template()

        return SkillResult.success_result(
            data={
                "has_sensitive": True,
                "level": 1,
                "matched_words": matched_words,
                "action": "放行并安抚",
                "response": comfort_template,
                "mood_tag": "dissatisfied",
                "proceed": True
            },
            metadata={
                "level_name": "L1低危",
                "action": "放行并安抚"
            },
            next_action="continue"
        )


# 创建全局敏感词过滤Skill实例
sensitive_word_filter_skill = SensitiveWordFilterSkill()