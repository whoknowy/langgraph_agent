"""
智能体包初始化文件
"""

from .base_agent import BaseAgent
from .product_agent import ProductAgent
from .billing_agent import BillingAgent
from .complaint_agent import ComplaintAgent
from .general_agent import GeneralAgent
from .trip_planner_agent import TripPlannerAgent

# 输入守卫依赖（敏感词词库与 AC 自动机）
from .sensitive_words import SensitivityKnowledgeBase, SensitivityLevel, sensitivity_knowledge_base
from .ac_automaton import SimpleSensitiveWordMatcher, ACAutomaton, create_matcher_from_knowledge_base

import os
from dotenv import load_dotenv
load_dotenv()
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL, HTTP_TIMEOUT

from langchain_openai import ChatOpenAI


def create_llm() -> ChatOpenAI:
    """创建官方 ChatOpenAI 客户端（deepseek 兼容接口，原生 function calling + 流式）。"""
    return ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0.3,
        max_tokens=2048,
        timeout=HTTP_TIMEOUT,
    )


_AGENTS_CACHE: dict = None


def initialize_agents() -> dict:
    """初始化 4 个业务 Agent 并返回名称到实例的映射（进程内缓存，供图节点复用）。"""
    global _AGENTS_CACHE
    if _AGENTS_CACHE is None:
        llm = create_llm()
        agents = {
            "product_agent": ProductAgent(),
            "billing_agent": BillingAgent(),
            "complaint_agent": ComplaintAgent(),
            "general_agent": GeneralAgent(),
            "trip_planner_agent": TripPlannerAgent(),
        }
        for agent in agents.values():
            agent.set_llm(llm)
        _AGENTS_CACHE = agents
    return _AGENTS_CACHE


__all__ = [
    # 业务智能体
    "BaseAgent",
    "ProductAgent",
    "BillingAgent",
    "ComplaintAgent",
    "GeneralAgent",
    "TripPlannerAgent",
    "initialize_agents",
    "create_llm",

    # 输入守卫（敏感词）
    "SensitivityKnowledgeBase",
    "SensitivityLevel",
    "sensitivity_knowledge_base",
    "SimpleSensitiveWordMatcher",
    "ACAutomaton",
    "create_matcher_from_knowledge_base",
]
