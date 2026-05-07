"""
Skill基类定义
提供Skill的标准接口和调度机制
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable
from enum import Enum
import uuid

class SkillType(Enum):
    """Skill类型枚举"""
    FILTER = "filter"  # 过滤器类Skill
    TOOL = "tool"  # 工具类Skill
    AGENT = "agent"  # 代理类Skill
    WORKFLOW = "workflow"  # 工作流类Skill

class SkillResult:
    """Skill执行结果"""

    def __init__(self):
        self.success: bool = False
        self.data: Any = None
        self.error: str = ""
        self.metadata: Dict[str, Any] = {}
        self.next_action: str = ""  # continue, stop, route_to
        self.route_to: str = ""  # 路由目标Skill名称

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
            "next_action": self.next_action,
            "route_to": self.route_to
        }

    @staticmethod
    def success_result(data: Any = None, metadata: Dict = None, next_action: str = "continue", route_to: str = "") -> "SkillResult":
        """创建成功结果"""
        result = SkillResult()
        result.success = True
        result.data = data
        result.metadata = metadata or {}
        result.next_action = next_action
        result.route_to = route_to
        return result

    @staticmethod
    def error_result(error: str) -> "SkillResult":
        """创建错误结果"""
        result = SkillResult()
        result.success = False
        result.error = error
        result.next_action = "stop"
        return result

class Skill(ABC):
    """
    Skill基类
    所有Skill都必须继承此类并实现标准接口
    """

    def __init__(
        self,
        name: str,
        description: str,
        skill_type: SkillType,
        keywords: List[str] = None,
        priority: int = 0
    ):
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.description = description
        self.skill_type = skill_type
        self.keywords = keywords or []
        self.priority = priority  # 优先级，数字越大优先级越高

    @abstractmethod
    def can_handle(self, context: Dict[str, Any]) -> bool:
        """
        判断当前Skill是否可以处理该请求
        基于关键词或上下文判断

        Args:
            context: 包含query和其他上下文信息

        Returns:
            True if this skill can handle the request
        """
        pass

    @abstractmethod
    def execute(self, context: Dict[str, Any]) -> SkillResult:
        """
        执行Skill逻辑

        Args:
            context: 执行上下文，包含输入和状态信息

        Returns:
            SkillResult 执行结果
        """
        pass

    def get_metadata(self) -> Dict[str, Any]:
        """获取Skill元信息"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "type": self.skill_type.value,
            "keywords": self.keywords,
            "priority": self.priority
        }


class SkillRegistry:
    """
    Skill注册表
    管理所有注册的Skill，提供查询和调度功能
    """

    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self._skill_by_keyword: Dict[str, List[str]] = {}  # 关键词到Skill名称的映射

    def register(self, skill: Skill) -> None:
        """注册Skill"""
        self.skills[skill.name] = skill

        # 建立关键词映射
        for keyword in skill.keywords:
            if keyword not in self._skill_by_keyword:
                self._skill_by_keyword[keyword] = []
            self._skill_by_keyword[keyword].append(skill.name)

    def unregister(self, skill_name: str) -> bool:
        """注销Skill"""
        if skill_name in self.skills:
            skill = self.skills[skill_name]
            # 清理关键词映射
            for keyword in skill.keywords:
                if keyword in self._skill_by_keyword:
                    if skill_name in self._skill_by_keyword[keyword]:
                        self._skill_by_keyword[keyword].remove(skill_name)
            del self.skills[skill_name]
            return True
        return False

    def get_skill(self, name: str) -> Optional[Skill]:
        """获取指定名称的Skill"""
        return self.skills.get(name)

    def find_skills_by_keyword(self, keyword: str) -> List[Skill]:
        """根据关键词查找Skill"""
        skill_names = self._skill_by_keyword.get(keyword, [])
        return [self.skills[name] for name in skill_names if name in self.skills]

    def find_skills_by_intent(self, query: str) -> List[Skill]:
        """根据用户意图查找匹配的Skill"""
        query_lower = query.lower()
        matched_skills = []

        for skill in self.skills.values():
            if skill.can_handle({"query": query, "query_lower": query_lower}):
                matched_skills.append(skill)

        # 按优先级排序
        matched_skills.sort(key=lambda s: s.priority, reverse=True)
        return matched_skills

    def get_all_skills(self) -> List[Skill]:
        """获取所有Skill"""
        return list(self.skills.values())

    def get_skills_by_type(self, skill_type: SkillType) -> List[Skill]:
        """获取指定类型的所有Skill"""
        return [s for s in self.skills.values() if s.skill_type == skill_type]


class SkillDispatcher:
    """
    Skill调度器
    负责协调多个Skill的执行顺序和结果处理
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def dispatch(self, context: Dict[str, Any]) -> SkillResult:
        """
        调度Skill执行

        Args:
            context: 执行上下文，包含query和其他信息

        Returns:
            SkillResult 最终执行结果
        """
        # 按优先级排序找到第一个可以处理的Skill
        skills = self.registry.find_skills_by_intent(context.get("query", ""))

        if not skills:
            return SkillResult.error_result(f"No skill can handle the query: {context.get('query', '')}")

        # 执行第一个匹配的Skill
        skill = skills[0]
        result = skill.execute(context)

        # 如果需要继续路由
        while result.success and result.next_action == "route_to" and result.route_to:
            next_skill = self.registry.get_skill(result.route_to)
            if not next_skill:
                return SkillResult.error_result(f"Skill not found: {result.route_to}")

            # 更新上下文，添加上一个Skill的结果
            context[f"{skill.name}_result"] = result.data
            result = next_skill.execute(context)

        return result

    def dispatch_pipeline(self, context: Dict[str, Any], skill_names: List[str]) -> List[SkillResult]:
        """
        按顺序执行一系列Skill

        Args:
            context: 执行上下文
            skill_names: 要执行的Skill名称列表

        Returns:
            List[SkillResult] 所有Skill的执行结果
        """
        results = []
        current_context = context.copy()

        for skill_name in skill_names:
            skill = self.registry.get_skill(skill_name)
            if not skill:
                results.append(SkillResult.error_result(f"Skill not found: {skill_name}"))
                continue

            result = skill.execute(current_context)
            results.append(result)

            # 更新上下文
            current_context[f"{skill_name}_result"] = result.data

            # 如果执行失败，停止Pipeline
            if not result.success:
                break

        return results


# 创建全局Skill注册表和调度器
skill_registry = SkillRegistry()
skill_dispatcher = SkillDispatcher(skill_registry)