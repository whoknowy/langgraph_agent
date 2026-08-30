"""
智能体包初始化文件
"""

from .base_agent import BaseAgent
from .product_agent import ProductAgent
from .billing_agent import BillingAgent
from .complaint_agent import ComplaintAgent
from .general_agent import GeneralAgent

# 敏感词过滤系统
from .sensitive_words import SensitivityKnowledgeBase, SensitivityLevel, sensitivity_knowledge_base
from .ac_automaton import SimpleSensitiveWordMatcher, ACAutomaton, create_matcher_from_knowledge_base
from .ticket_system import TicketSystem, Ticket, TicketStatus, TicketType, ticket_system
from .workstation import Workstation, Agent, AgentStatus, AgentLevel, workstation
from .filter_pipeline import SensitiveWordFilter, FilterResult, FilterPipeline, sensitive_word_filter, filter_pipeline

from .tech_agent import TechAgent

import requests
import json
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


# 官方 ChatOpenAI 同时兼容旧服务名（OpenAICompatibleLLM），供存量代码引用
OpenAICompatibleLLM = ChatOpenAI


_AGENTS_CACHE: dict = None


def initialize_agents() -> dict:
    """初始化所有业务 Agent 并返回名称到实例的映射（进程内缓存，供图节点复用）。"""
    global _AGENTS_CACHE
    if _AGENTS_CACHE is None:
        llm = create_llm()
        agents = {
            "product_agent": ProductAgent(),
            "billing_agent": BillingAgent(),
            "complaint_agent": ComplaintAgent(),
            "general_agent": GeneralAgent(),
            "tech_agent": TechAgent(),
        }
        for agent in agents.values():
            agent.set_llm(llm)
        _AGENTS_CACHE = agents
    return _AGENTS_CACHE


__all__ = [
    # 基础智能体
    "BaseAgent",
    "ProductAgent",
    "BillingAgent",
    "ComplaintAgent",
    "GeneralAgent",
    "TechAgent",
    "initialize_agents",
    "create_llm",
    "OpenAICompatibleLLM",

    # 敏感词系统
    "SensitivityKnowledgeBase",
    "SensitivityLevel",
    "sensitivity_knowledge_base",

    # AC自动机
    "SimpleSensitiveWordMatcher",
    "ACAutomaton",
    "create_matcher_from_knowledge_base",

    # 工单系统
    "TicketSystem",
    "Ticket",
    "TicketStatus",
    "TicketType",
    "ticket_system",

    # 工作台
    "Workstation",
    "Agent",
    "AgentStatus",
    "AgentLevel",
    "workstation",

    # 过滤管道
    "SensitiveWordFilter",
    "FilterResult",
    "FilterPipeline",
    "sensitive_word_filter",
    "filter_pipeline"
]