"""Langfuse 轨迹观测接入（可选启用，与 LangSmith 并存）。

启用方式（.env）：
    LANGFUSE_PUBLIC_KEY=pk-lf-xxxx
    LANGFUSE_SECRET_KEY=sk-lf-xxxx
    LANGFUSE_HOST=http://localhost:3000        # 自建 Langfuse（docker compose 一键起）

设计要点：
- LangGraph 服务进程与 Flask 进程都加载同一份 .env（langgraph.json env
  指向 ./.env），两边的 LLM 调用都能上报；
- 用 OTEL span（start_as_current_span）把一个 Agent 节点内的多次 LLM 调用
  与工具调用聚成一条 trace，session_id = LangGraph 线程、user_id = 会员号，
  Langfuse UI 里按会话分组回放整轮链路；
- 未配置密钥 / 未安装 langfuse 时全部退化为 no-op，主链路零影响；
- 这是验收项④「Langfuse 链路可视化」的落地：Prompt / 工具 / 耗时 / token
  在 Langfuse UI 全展开，项目自身不再依赖外部 SaaS（弥补 LangSmith 在 Demo
  现场不可控的问题）。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional


def is_enabled() -> bool:
    """是否启用：密钥齐全且不是占位符。"""
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    sk = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not (pk and sk):
        return False
    # .env.example 里的占位符 pk-lf-xxxx / sk-lf-xxxx 不算启用
    if "xxxx" in pk.lower() or "xxxx" in sk.lower():
        return False
    if "your_" in pk.lower() or "your_" in sk.lower():
        return False
    return True


@contextmanager
def trace(name: str, session_id: Optional[str] = None,
          user_id: Optional[str] = None, tags: Optional[list] = None) -> Iterator[Optional[object]]:
    """把一段处理聚成一条 Langfuse trace；未启用时 yield None（no-op）。

    用法：
        with langfuse_setup.trace(name="agent:billing_agent",
                                  session_id=tid, user_id=mid) as handler:
            llm.invoke(messages, config=langfuse_setup.llm_config(handler))
    """
    if not is_enabled():
        yield None
        return
    try:
        from langfuse import get_client  # v3 SDK
        from langfuse.langchain import CallbackHandler
        client = get_client()
        # span 自动管理 OTEL 上下文；CallbackHandler 实例化后挂到当前 span，
        # 该 span 下游的 LLM / 工具 invoke 全部嵌套为该 trace 的子 observation。
        with client.start_as_current_span(name=name) as span:
            try:
                span.update_trace(
                    session_id=session_id,
                    user_id=user_id,
                    tags=tags or [],
                )
            except Exception:
                # span.update_trace 在 v3 老 API 名称下可能不存在；不阻断主链路
                pass
            yield CallbackHandler()
    except Exception as e:
        # 观测组件任何异常都降级，不影响业务
        print(f"⚠️ Langfuse trace 创建失败（观测降级，业务不受影响）: {e}")
        yield None


def llm_config(handler: Optional[object]) -> Optional[dict]:
    """统一组装 LLM / 工具 invoke 的 config；handler 为 None 时返回 None。"""
    if handler is None:
        return None
    return {"callbacks": [handler]}