"""
人工客服工作台
管理外呼列表、客服状态和工单分配
"""

import uuid
import time
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum

class AgentStatus(Enum):
    """客服状态枚举"""
    ONLINE = "online"  # 在线
    BUSY = "busy"  # 忙碌
    OFFLINE = "offline"  # 离线
    BREAK = "break"  # 休息

class AgentLevel(Enum):
    """客服级别枚举"""
    NORMAL = "normal"  # 普通客服
    SENIOR = "senior"  # 高级客服
    SPECIALIST = "specialist"  # 投诉专员

class CallTask:
    """
    外呼任务类
    表示一个需要人工客服处理的任务
    """

    def __init__(
        self,
        ticket_id: str,
        session_id: str,
        customer_query: str,
        sensitivity_level: int,
        customer_info: Optional[Dict] = None
    ):
        self.task_id = str(uuid.uuid4())[:8]
        self.ticket_id = ticket_id
        self.session_id = session_id
        self.customer_query = customer_query
        self.sensitivity_level = sensitivity_level
        self.customer_info = customer_info or {}

        # 任务状态
        self.status = "pending"  # pending, assigned, in_progress, completed, cancelled
        self.priority = sensitivity_level  # 敏感词等级决定优先级
        self.required_agent_level = "投诉专员" if sensitivity_level == 3 else "普通客服"

        # 分配信息
        self.assigned_to = None
        self.assigned_at = None
        self.started_at = None
        self.completed_at = None

        # 联系结果
        self.contact_result = None  # contacted, no_answer, refused, unreachable
        self.notes = []
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "ticket_id": self.ticket_id,
            "session_id": self.session_id,
            "customer_query": self.customer_query,
            "sensitivity_level": self.sensitivity_level,
            "customer_info": self.customer_info,
            "status": self.status,
            "priority": self.priority,
            "required_agent_level": self.required_agent_level,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "contact_result": self.contact_result,
            "notes": self.notes,
            "created_at": self.created_at
        }

    def assign(self, agent_id: str) -> bool:
        """分配任务"""
        if self.status != "pending":
            return False

        self.assigned_to = agent_id
        self.assigned_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = "assigned"
        return True

    def start(self) -> bool:
        """开始处理"""
        if self.status != "assigned":
            return False

        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status = "in_progress"
        return True

    def complete(self, contact_result: str, notes: str = "") -> bool:
        """完成任务"""
        if self.status != "in_progress":
            return False

        self.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.contact_result = contact_result
        self.status = "completed"
        if notes:
            self.add_note(notes)
        return True

    def add_note(self, note: str) -> None:
        """添加备注"""
        self.notes.append({
            "note": note,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


class Agent:
    """
    客服类
    表示一个人工客服
    """

    def __init__(self, agent_id: str, name: str, level: AgentLevel = AgentLevel.NORMAL):
        self.agent_id = agent_id
        self.name = name
        self.level = level
        self.status = AgentStatus.OFFLINE

        # 工作统计
        self.total_calls = 0
        self.completed_calls = 0
        self.failed_calls = 0

        # 当前处理的工单
        self.current_task = None

        # 登录时间
        self.login_at = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "level": self.level.value,
            "status": self.status.value,
            "total_calls": self.total_calls,
            "completed_calls": self.completed_calls,
            "failed_calls": self.failed_calls,
            "current_task": self.current_task.task_id if self.current_task else None,
            "login_at": self.login_at
        }

    def login(self) -> None:
        """登录"""
        self.status = AgentStatus.ONLINE
        self.login_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def logout(self) -> None:
        """登出"""
        self.status = AgentStatus.OFFLINE
        self.current_task = None

    def set_busy(self) -> None:
        """设置为忙碌"""
        self.status = AgentStatus.BUSY

    def set_available(self) -> None:
        """设置为可用"""
        self.status = AgentStatus.ONLINE

    def set_break(self) -> None:
        """设置为休息"""
        self.status = AgentStatus.BREAK
        self.current_task = None

    def accept_task(self, task: CallTask) -> bool:
        """接受任务"""
        if self.status != AgentStatus.ONLINE:
            return False

        task.assign(self.agent_id)
        self.current_task = task
        self.set_busy()
        return True

    def complete_task(self, contact_result: str, notes: str = "") -> bool:
        """完成任务"""
        if not self.current_task:
            return False

        self.current_task.complete(contact_result, notes)
        self.total_calls += 1

        if contact_result == "contacted":
            self.completed_calls += 1
        else:
            self.failed_calls += 1

        self.current_task = None
        self.set_available()
        return True


class Workstation:
    """
    人工客服工作台
    管理外呼列表、客服状态和工单分配
    """

    def __init__(self):
        # 客服列表
        self.agents: Dict[str, Agent] = {}

        # 外呼任务列表
        self.call_tasks: Dict[str, CallTask] = {}

        # 待分配队列（按优先级排序）
        self.pending_l2_queue: List[str] = []  # L2中危投诉队列
        self.pending_l3_queue: List[str] = []  # L3高危投诉队列

    def register_agent(self, agent_id: str, name: str, level: AgentLevel = AgentLevel.NORMAL) -> Agent:
        """
        注册客服

        Args:
            agent_id: 客服ID
            name: 客服姓名
            level: 客服级别

        Returns:
            注册的客服对象
        """
        agent = Agent(agent_id, name, level)
        self.agents[agent_id] = agent
        return agent

    def unregister_agent(self, agent_id: str) -> bool:
        """注销客服"""
        if agent_id in self.agents:
            del self.agents[agent_id]
            return True
        return False

    def get_available_agents(self, required_level: str = None) -> List[Agent]:
        """
        获取可用的客服列表

        Args:
            required_level: 需要的客服级别

        Returns:
            可用客服列表
        """
        available = [
            agent for agent in self.agents.values()
            if agent.status == AgentStatus.ONLINE and agent.current_task is None
        ]

        if required_level:
            if required_level == "投诉专员":
                available = [
                    agent for agent in available
                    if agent.level in [AgentLevel.SENIOR, AgentLevel.SPECIALIST]
                ]
            elif required_level == "普通客服":
                available = available

        return available

    def create_call_task(
        self,
        ticket_id: str,
        session_id: str,
        customer_query: str,
        sensitivity_level: int,
        customer_info: Optional[Dict] = None
    ) -> CallTask:
        """
        创建外呼任务

        Args:
            ticket_id: 工单ID
            session_id: 会话ID
            customer_query: 客户查询
            sensitivity_level: 敏感词等级
            customer_info: 客户信息

        Returns:
            创建的外呼任务
        """
        task = CallTask(
            ticket_id=ticket_id,
            session_id=session_id,
            customer_query=customer_query,
            sensitivity_level=sensitivity_level,
            customer_info=customer_info
        )

        self.call_tasks[task.task_id] = task

        # 根据敏感词等级加入不同的队列
        if sensitivity_level == 2:
            self.pending_l2_queue.append(task.task_id)
            self.pending_l2_queue.sort(key=lambda x: self.call_tasks[x].created_at)
        elif sensitivity_level == 3:
            self.pending_l3_queue.append(task.task_id)
            self.pending_l3_queue.sort(key=lambda x: self.call_tasks[x].created_at)

        return task

    def get_pending_tasks(self, sensitivity_level: int = None) -> List[CallTask]:
        """
        获取待处理的外呼任务

        Args:
            sensitivity_level: 敏感词等级筛选

        Returns:
            待处理任务列表
        """
        if sensitivity_level == 2:
            return [self.call_tasks[tid] for tid in self.pending_l2_queue]
        elif sensitivity_level == 3:
            return [self.call_tasks[tid] for tid in self.pending_l3_queue]
        else:
            l2_tasks = [self.call_tasks[tid] for tid in self.pending_l2_queue]
            l3_tasks = [self.call_tasks[tid] for tid in self.pending_l3_queue]
            return l3_tasks + l2_tasks

    def assign_task(self, task_id: str, agent_id: str) -> bool:
        """
        分配任务给客服

        Args:
            task_id: 任务ID
            agent_id: 客服ID

        Returns:
            是否分配成功
        """
        task = self.call_tasks.get(task_id)
        agent = self.agents.get(agent_id)

        if not task or not agent:
            return False

        if task.status != "pending":
            return False

        # 检查客服级别是否满足要求
        if task.required_agent_level == "投诉专员":
            if agent.level == AgentLevel.NORMAL:
                return False

        # 分配任务
        if agent.accept_task(task):
            # 从待分配队列中移除
            if task_id in self.pending_l2_queue:
                self.pending_l2_queue.remove(task_id)
            elif task_id in self.pending_l3_queue:
                self.pending_l3_queue.remove(task_id)
            return True

        return False

    def auto_assign_task(self, sensitivity_level: int = None) -> Optional[tuple]:
        """
        自动分配任务给可用客服

        Args:
            sensitivity_level: 敏感词等级

        Returns:
            (任务, 客服) 或 None
        """
        # 获取待分配队列
        if sensitivity_level == 3:
            queue = self.pending_l3_queue
            required_level = "投诉专员"
        elif sensitivity_level == 2:
            queue = self.pending_l2_queue
            required_level = "普通客服"
        else:
            # 优先处理L3任务
            if self.pending_l3_queue:
                queue = self.pending_l3_queue
                required_level = "投诉专员"
            elif self.pending_l2_queue:
                queue = self.pending_l2_queue
                required_level = "普通客服"
            else:
                return None

        if not queue:
            return None

        # 获取可用客服
        available_agents = self.get_available_agents(required_level)
        if not available_agents:
            return None

        # 分配任务给第一个可用客服
        task_id = queue[0]
        task = self.call_tasks[task_id]
        agent = available_agents[0]

        if self.assign_task(task_id, agent.agent_id):
            return task, agent

        return None

    def get_agent_tasks(self, agent_id: str) -> List[CallTask]:
        """
        获取客服的任务列表

        Args:
            agent_id: 客服ID

        Returns:
            任务列表
        """
        return [
            task for task in self.call_tasks.values()
            if task.assigned_to == agent_id
        ]

    def get_workstation_summary(self) -> Dict[str, Any]:
        """
        获取工作台统计摘要

        Returns:
            统计信息字典
        """
        summary = {
            "total_agents": len(self.agents),
            "online_agents": len([a for a in self.agents.values() if a.status == AgentStatus.ONLINE]),
            "busy_agents": len([a for a in self.agents.values() if a.status == AgentStatus.BUSY]),
            "total_tasks": len(self.call_tasks),
            "pending_l2_tasks": len(self.pending_l2_queue),
            "pending_l3_tasks": len(self.pending_l3_queue),
            "in_progress_tasks": len([t for t in self.call_tasks.values() if t.status == "in_progress"]),
            "completed_tasks": len([t for t in self.call_tasks.values() if t.status == "completed"])
        }

        # 各客服状态
        summary["agents_status"] = {
            agent.agent_id: {
                "name": agent.name,
                "level": agent.level.value,
                "status": agent.status.value,
                "current_task": agent.current_task.task_id if agent.current_task else None
            }
            for agent in self.agents.values()
        }

        return summary


# 创建全局工作台实例
workstation = Workstation()