"""
LLM 意图分类器（单次调用）

替代旧版"关键词规则 + embedding 向量 + 正则参数解析"的三层路由：
只做一件事——把用户这句话分给 4 个业务 Agent 中的一个。
工具选择与参数解析由 Agent 内部的 ReAct 循环（模型自主 function calling）完成。
"""

import json
import re
from typing import Dict, List, Optional

from agents import create_llm
from langchain_core.messages import HumanMessage, SystemMessage

_CLASSIFIER_LLM = None

# 4 个 Agent 的职责边界（与 agents/ 下 4 个实现一一对应）
AGENT_OPTIONS = {
    "product": "机票业务：航班查询/比价/预订咨询、机票价格构成、目的地天气、航班延误预测、价格走势",
    "billing": "订单与账单：查订单、账单明细、支付、退款、改签费用、发票",
    "complaint": "投诉与不满：投诉查询/提交、索赔、要求道歉或赔偿、表达强烈不满",
    "general": "问候闲聊、感谢、或与上述三类都无关的其他咨询",
}

_SYSTEM_PROMPT = (
    "你是航空客服系统的意图分类器。根据对话历史和用户最新消息，"
    "从下面四个客服专员中选择最合适的一个处理用户最新消息：\n"
    + "\n".join(f"- {k}: {v}" for k, v in AGENT_OPTIONS.items())
    + "\n\n判定规则：\n"
    "1. 只依据用户最新消息的主要诉求选择；一条消息里有多个诉求时，选最主要的一个；\n"
    "2. 涉及不满、投诉、赔偿的，优先选 complaint；\n"
    "3. 上下文连续追问（如\"那明天呢\"）沿用上一轮所属的业务线；\n"
    "4. 只输出 JSON，不要输出任何其他文字：{\"agent\": \"product|billing|complaint|general\", \"note\": \"一句话依据\"}"
)

_FALLBACK: Dict[str, str] = {"agent": "general", "note": "分类失败，兜底综合客服"}


def _get_llm():
    global _CLASSIFIER_LLM
    if _CLASSIFIER_LLM is None:
        _CLASSIFIER_LLM = create_llm()
    return _CLASSIFIER_LLM


def classify_intent(query: str, history: Optional[List[Dict]] = None) -> Dict[str, str]:
    """单次 LLM 调用完成意图分类。

    Args:
        query: 用户最新消息
        history: 该线程的历史消息 [{"role": "user"|"assistant", "content": ...}]（不含当前消息）

    Returns:
        {"agent": "product|billing|complaint|general", "note": str}
        解析失败时兜底 general。
    """
    try:
        lines = []
        for msg in (history or [])[-6:]:
            role = "用户" if msg.get("role") == "user" else "客服"
            content = (msg.get("content") or "").strip()
            if content:
                lines.append(f"{role}: {content[:120]}")
        lines.append(f"用户最新消息: {query}")
        payload = "\n".join(lines)

        raw = _get_llm().invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=payload),
        ]).content or ""

        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        agent = str(data.get("agent", "")).strip().lower()
        if agent not in AGENT_OPTIONS:
            return dict(_FALLBACK)
        return {"agent": agent, "note": str(data.get("note", "")).strip()}
    except Exception as e:
        print(f"[意图分类] LLM 分类失败，兜底 general: {e}")
        return dict(_FALLBACK)
