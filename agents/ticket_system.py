"""
工单系统
用于生成和管理投诉工单
"""

import uuid
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum

class TicketStatus(Enum):
    """工单状态枚举"""
    PENDING = "pending"  # 待处理
    ASSIGNED = "assigned"  # 已分配
    IN_PROGRESS = "in_progress"  # 处理中
    RESOLVED = "resolved"  # 已解决
    CLOSED = "closed"  # 已关闭
    ESCALATED = "escalated"  # 已升级

class TicketPriority(Enum):
    """工单优先级枚举"""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4

class TicketType(Enum):
    """工单类型枚举"""
    L2_COMPLAINT = "l2_complaint"  # L2中危投诉
    L3_COMPLAINT = "l3_complaint"  # L3高危投诉

class Ticket:
    """
    工单类
    表示一个投诉工单
    """

    def __init__(
        self,
        session_id: str,
        customer_query: str,
        sensitivity_level: int,
        matched_words: List[tuple],
        customer_info: Optional[Dict] = None
    ):
        self.ticket_id = str(uuid.uuid4())[:8]
        self.session_id = session_id
        self.customer_query = customer_query
        self.sensitivity_level = sensitivity_level
        self.matched_words = matched_words
        self.customer_info = customer_info or {}

        # 工单元数据
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.updated_at = self.created_at

        # 工单状态
        if sensitivity_level == 2:
            self.ticket_type = TicketType.L2_COMPLAINT
            self.priority = TicketPriority.MEDIUM
            self.required_agent_level = "普通客服"
        else:  # level == 3
            self.ticket_type = TicketType.L3_COMPLAINT
            self.priority = TicketPriority.HIGH
            self.required_agent_level = "投诉专员"

        self.status = TicketStatus.PENDING
        self.assigned_to = None
        self.assigned_at = None
        self.resolved_at = None
        self.closed_at = None

        # 处理记录
        self.action_history: List[Dict] = []
        self.notes: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "ticket_id": self.ticket_id,
            "session_id": self.session_id,
            "customer_query": self.customer_query,
            "sensitivity_level": self.sensitivity_level,
            "matched_words": [(w, l) for w, l in self.matched_words],
            "customer_info": self.customer_info,
            "ticket_type": self.ticket_type.value,
            "priority": self.priority.value,
            "required_agent_level": self.required_agent_level,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "closed_at": self.closed_at,
            "action_history": self.action_history,
            "notes": self.notes
        }

    def add_action(self, action: str, operator: str = "system"):
        """添加处理动作"""
        self.action_history.append({
            "action": action,
            "operator": operator,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def add_note(self, note: str, operator: str = "agent"):
        """添加备注"""
        self.notes.append({
            "note": note,
            "operator": operator,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def assign(self, agent_id: str) -> bool:
        """分配工单"""
        if self.status != TicketStatus.PENDING:
            return False

        self.assigned_to = agent_id
        self.assigned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = TicketStatus.ASSIGNED
        self.add_action(f"工单已分配给 {agent_id}")
        return True

    def start_processing(self, agent_id: str) -> bool:
        """开始处理"""
        if self.assigned_to != agent_id:
            return False

        self.status = TicketStatus.IN_PROGRESS
        self.add_action(f"{agent_id} 开始处理工单")
        return True

    def resolve(self, agent_id: str, resolution: str) -> bool:
        """解决工单"""
        if self.status != TicketStatus.IN_PROGRESS:
            return False

        self.resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = TicketStatus.RESOLVED
        self.add_action(f"{agent_id} 解决工单: {resolution}")
        return True

    def close(self, agent_id: str) -> bool:
        """关闭工单"""
        if self.status != TicketStatus.RESOLVED:
            return False

        self.closed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = TicketStatus.CLOSED
        self.add_action(f"{agent_id} 关闭工单")
        return True

    def escalate(self, agent_id: str, reason: str) -> bool:
        """升级工单"""
        if self.status in [TicketStatus.CLOSED, TicketStatus.ESCALATED]:
            return False

        self.status = TicketStatus.ESCALATED
        self.priority = TicketPriority.URGENT
        self.required_agent_level = "投诉专员"
        self.add_action(f"{agent_id} 升级工单: {reason}")
        return True


class TicketSystem:
    """
    工单系统
    管理所有投诉工单
    """

    def __init__(self):
        self.tickets: Dict[str, Ticket] = {}
        self.session_to_ticket: Dict[str, str] = {}

    def create_ticket(
        self,
        session_id: str,
        customer_query: str,
        sensitivity_level: int,
        matched_words: List[tuple],
        customer_info: Optional[Dict] = None
    ) -> Ticket:
        """
        创建新工单

        Args:
            session_id: 会话ID
            customer_query: 客户查询
            sensitivity_level: 敏感词等级
            matched_words: 匹配到的敏感词
            customer_info: 客户信息

        Returns:
            创建的工单对象
        """
        # 如果已存在该会话的工单，返回现有工单
        if session_id in self.session_to_ticket:
            ticket_id = self.session_to_ticket[session_id]
            return self.tickets[ticket_id]

        # 创建新工单
        ticket = Ticket(
            session_id=session_id,
            customer_query=customer_query,
            sensitivity_level=sensitivity_level,
            matched_words=matched_words,
            customer_info=customer_info
        )

        # 存储工单
        self.tickets[ticket.ticket_id] = ticket
        self.session_to_ticket[session_id] = ticket.ticket_id

        return ticket

    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        """获取工单"""
        return self.tickets.get(ticket_id)

    def get_ticket_by_session(self, session_id: str) -> Optional[Ticket]:
        """根据会话ID获取工单"""
        ticket_id = self.session_to_ticket.get(session_id)
        if ticket_id:
            return self.tickets.get(ticket_id)
        return None

    def get_tickets_by_status(self, status: TicketStatus) -> List[Ticket]:
        """获取指定状态的所有工单"""
        return [t for t in self.tickets.values() if t.status == status]

    def get_tickets_by_type(self, ticket_type: TicketType) -> List[Ticket]:
        """获取指定类型的所有工单"""
        return [t for t in self.tickets.values() if t.ticket_type == ticket_type]

    def get_tickets_by_agent(self, agent_id: str) -> List[Ticket]:
        """获取分配给指定客服的工单"""
        return [t for t in self.tickets.values() if t.assigned_to == agent_id]

    def get_pending_l2_tickets(self) -> List[Ticket]:
        """获取待处理的L2投诉工单"""
        return [
            t for t in self.tickets.values()
            if t.ticket_type == TicketType.L2_COMPLAINT
            and t.status == TicketStatus.PENDING
        ]

    def get_pending_l3_tickets(self) -> List[Ticket]:
        """获取待处理的L3投诉工单"""
        return [
            t for t in self.tickets.values()
            if t.ticket_type == TicketType.L3_COMPLAINT
            and t.status == TicketStatus.PENDING
        ]

    def assign_ticket(self, ticket_id: str, agent_id: str) -> bool:
        """分配工单"""
        ticket = self.get_ticket(ticket_id)
        if ticket:
            return ticket.assign(agent_id)
        return False

    def get_ticket_summary(self) -> Dict[str, Any]:
        """获取工单统计摘要"""
        summary = {
            "total": len(self.tickets),
            "by_status": {},
            "by_type": {
                "l2_complaint": len(self.get_tickets_by_type(TicketType.L2_COMPLAINT)),
                "l3_complaint": len(self.get_tickets_by_type(TicketType.L3_COMPLAINT))
            },
            "pending": len(self.get_tickets_by_status(TicketStatus.PENDING)),
            "in_progress": len(self.get_tickets_by_status(TicketStatus.IN_PROGRESS)),
            "resolved": len(self.get_tickets_by_status(TicketStatus.RESOLVED)),
            "closed": len(self.get_tickets_by_status(TicketStatus.CLOSED))
        }

        for status in TicketStatus:
            summary["by_status"][status.value] = len(self.get_tickets_by_status(status))

        return summary


# 创建全局工单系统实例
ticket_system = TicketSystem()