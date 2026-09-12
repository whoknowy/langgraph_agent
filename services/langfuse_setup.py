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

    SDK 兼容：langfuse python SDK v3 用 client.start_as_current_span，
    v4 改名为 start_as_current_observation(as_type="span")，这里两者都支持。
    """
    if not is_enabled():
        yield None
        return
    try:
        from langfuse import get_client  # v3/v4 SDK
        from langfuse.langchain import CallbackHandler
        client = get_client()
        if hasattr(client, "start_as_current_span"):          # v3
            span_cm = client.start_as_current_span(name=name)
        else:                                                  # v4
            span_cm = client.start_as_current_observation(name=name, as_type="span")
        with span_cm as span:
            try:
                if hasattr(span, "update_trace"):              # v3
                    span.update_trace(session_id=session_id, user_id=user_id,
                                      tags=tags or [])
                else:                                          # v4: update(**kwargs) 承接 trace 属性
                    span.update(session_id=session_id, user_id=user_id,
                                tags=tags or [])
            except Exception:
                # trace 属性写入失败不影响观测主链路
                pass
            # CallbackHandler 实例化后挂到当前 span 上下文，下游 LLM / 工具
            # invoke 全部自动嵌套为该 trace 的子 observation。
            yield CallbackHandler()
    except Exception as e:
        # 观测组件任何异常都降级，不影响业务
        print(f"⚠️ Langfuse trace 创建失败（观测降级，业务不受影响）: {e}")
        yield None


def llm_config(handler: Optional[object]) -> Optional[dict]:
    """统一组装 LLM / 工具 invoke 的 config；handler 为 None 时返回 None。

    注意：必须与调用点继承到的上下文 config 合并（merge_configs），
    不能直接返回 {"callbacks": [handler]}——显式 callbacks 会整体替换掉
    LangGraph 注入的流式回调（StreamMessagesHandler），导致
    stream_mode=["messages-tuple"] 一个 token 都收不到（前端失去打字效果）。
    合并后 Langfuse 观测与 LangGraph token 流可以共存。
    """
    if handler is None:
        return None
    try:
        from langchain_core.runnables.config import ensure_config, merge_configs
        return merge_configs(ensure_config(), {"callbacks": [handler]})
    except Exception:
        # 合并失败也不影响观测主链路，退化为原行为
        return {"callbacks": [handler]}