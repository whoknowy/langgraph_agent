"""
敏感词过滤器
整合敏感词知识库、AC自动机、工单系统和工作台
实现完整的敏感词过滤流程
"""

from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from .sensitive_words import SensitivityKnowledgeBase, SensitivityLevel, sensitivity_knowledge_base
from .ac_automaton import SimpleSensitiveWordMatcher, create_matcher_from_knowledge_base
from .ticket_system import TicketSystem, TicketType, ticket_system
from .workstation import Workstation, AgentLevel, workstation

class FilterResult:
    """过滤结果类"""

    def __init__(self):
        self.has_sensitive_word: bool = False
        self.highest_level: int = 0  # 0=无, 1=L1, 2=L2, 3=L3
        self.matched_words: List[Tuple[str, int]] = []
        self.action: str = ""  # 放行/安抚/转客服/终止
        self.response: str = ""  # 响应内容
        self.mood_tag: str = ""  # 情绪标签
        self.ticket_id: Optional[str] = None  # 工单ID
        self.task_id: Optional[str] = None  # 外呼任务ID
        self.proceed_to_intent: bool = True  # 是否继续意图识别

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "has_sensitive_word": self.has_sensitive_word,
            "highest_level": self.highest_level,
            "matched_words": self.matched_words,
            "action": self.action,
            "response": self.response,
            "mood_tag": self.mood_tag,
            "ticket_id": self.ticket_id,
            "task_id": self.task_id,
            "proceed_to_intent": self.proceed_to_intent
        }


class SensitiveWordFilter:
    """
    敏感词过滤器
    实现三级敏感词处理流程
    """

    def __init__(self):
        self.knowledge_base: SensitivityKnowledgeBase = sensitivity_knowledge_base
        self.matcher: SimpleSensitiveWordMatcher = create_matcher_from_knowledge_base(self.knowledge_base)
        self.ticket_system: TicketSystem = ticket_system
        self.workstation: Workstation = workstation

    def filter(self, text: str, session_id: str, customer_info: Optional[Dict] = None) -> FilterResult:
        """
        过滤用户输入

        Args:
            text: 用户输入文本
            session_id: 会话ID
            customer_info: 客户信息

        Returns:
            FilterResult 过滤结果
        """
        result = FilterResult()

        # 执行敏感词匹配
        has_sensitive, highest_level, matched_words = self.matcher.match(text)

        result.has_sensitive_word = has_sensitive
        result.highest_level = highest_level
        result.matched_words = matched_words

        if not has_sensitive or highest_level == 0:
            # 无敏感词，正常流程
            result.action = "放行"
            result.proceed_to_intent = True
            return result

        # L3 高危处理
        if highest_level == SensitivityLevel.L3_HIGH.value:
            result = self._handle_l3(text, session_id, matched_words, customer_info, result)
            return result

        # L2 中危处理
        if highest_level == SensitivityLevel.L2_MEDIUM.value:
            result = self._handle_l2(text, session_id, matched_words, customer_info, result)
            return result

        # L1 低危处理
        if highest_level == SensitivityLevel.L1_LOW.value:
            result = self._handle_l1(text, matched_words, result)
            return result

        return result

    def _handle_l3(
        self,
        text: str,
        session_id: str,
        matched_words: List[Tuple[str, int]],
        customer_info: Optional[Dict],
        result: FilterResult
    ) -> FilterResult:
        """
        处理L3高危敏感词
        直接终止流程，转入投诉专员列表
        """
        result.action = "终止并转入投诉专员"
        result.response = "您的反馈我们已经收到，会尽快安排专人与您联系处理。给您带来不便请谅解。"
        result.mood_tag = "high_risk"
        result.proceed_to_intent = False

        # 创建工单
        ticket = self.ticket_system.create_ticket(
            session_id=session_id,
            customer_query=text,
            sensitivity_level=3,
            matched_words=matched_words,
            customer_info=customer_info
        )
        result.ticket_id = ticket.ticket_id

        # 创建外呼任务
        task = self.workstation.create_call_task(
            ticket_id=ticket.ticket_id,
            session_id=session_id,
            customer_query=text,
            sensitivity_level=3,
            customer_info=customer_info
        )
        result.task_id = task.task_id

        # 自动分配任务给投诉专员
        assignment = self.workstation.auto_assign_task(sensitivity_level=3)
        if assignment:
            assigned_task, agent = assignment
            result.response = f"您的反馈我们已经收到，会尽快安排专人与您联系处理（工单号：{ticket.ticket_id}）。给您带来不便请谅解。"

        return result

    def _handle_l2(
        self,
        text: str,
        session_id: str,
        matched_words: List[Tuple[str, int]],
        customer_info: Optional[Dict],
        result: FilterResult
    ) -> FilterResult:
        """
        处理L2中危敏感词
        跳过意图识别，转入普通客服列表
        """
        result.action = "转入普通客服列表"
        result.response = "感谢您的反馈，我们非常重视您的问题，会尽快安排客服人员与您联系。"
        result.mood_tag = "medium_risk"
        result.proceed_to_intent = False

        # 创建工单
        ticket = self.ticket_system.create_ticket(
            session_id=session_id,
            customer_query=text,
            sensitivity_level=2,
            matched_words=matched_words,
            customer_info=customer_info
        )
        result.ticket_id = ticket.ticket_id

        # 创建外呼任务
        task = self.workstation.create_call_task(
            ticket_id=ticket.ticket_id,
            session_id=session_id,
            customer_query=text,
            sensitivity_level=2,
            customer_info=customer_info
        )
        result.task_id = task.task_id

        # 自动分配任务给普通客服
        assignment = self.workstation.auto_assign_task(sensitivity_level=2)
        if assignment:
            assigned_task, agent = assignment
            result.response = f"感谢您的反馈，我们非常重视您的问题（工单号：{ticket.ticket_id}），会尽快安排客服人员与您联系。"

        return result

    def _handle_l1(
        self,
        text: str,
        matched_words: List[Tuple[str, int]],
        result: FilterResult
    ) -> FilterResult:
        """
        处理L1低危敏感词
        放行但打上标签，使用安抚话术
        """
        result.action = "放行并安抚"
        result.mood_tag = "dissatisfied"
        result.proceed_to_intent = True

        # 获取安抚话术
        comfort_template = self.knowledge_base.get_comfort_template()
        result.response = comfort_template

        return result

    def get_level_name(self, level: int) -> str:
        """获取等级名称"""
        return self.knowledge_base.get_level_name(level)

    def get_action_by_level(self, level: int) -> str:
        """获取等级对应的处理动作"""
        return self.knowledge_base.get_action_by_level(level)


# 创建全局过滤器实例
sensitive_word_filter = SensitiveWordFilter()


class FilterPipeline:
    """
    过滤管道
    整合敏感词过滤和意图识别的完整流程
    """

    def __init__(self):
        self.filter = sensitive_word_filter

    def process(
        self,
        text: str,
        session_id: str,
        customer_info: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        处理用户输入的完整流程

        Args:
            text: 用户输入
            session_id: 会话ID
            customer_info: 客户信息

        Returns:
            处理结果字典
        """
        # 第一关：敏感词过滤
        filter_result = self.filter.filter(text, session_id, customer_info)

        # 构建返回结果
        response = {
            "session_id": session_id,
            "original_query": text,
            "filter_result": filter_result.to_dict(),
            "proceed_to_intent": filter_result.proceed_to_intent
        }

        if not filter_result.proceed_to_intent:
            # 不继续意图识别，直接返回过滤结果
            response["final_response"] = filter_result.response
            response["action"] = filter_result.action
            response["mood_tag"] = filter_result.mood_tag
            if filter_result.ticket_id:
                response["ticket_id"] = filter_result.ticket_id
            if filter_result.task_id:
                response["task_id"] = filter_result.task_id
            return response

        # 继续意图识别（第二关）
        # 这里返回需要继续的标识，具体意图识别由主流程处理
        response["continue_intent"] = True
        response["mood_tag"] = filter_result.mood_tag
        response["filter_response"] = filter_result.response

        return response


# 创建全局过滤管道实例
filter_pipeline = FilterPipeline()