"""
工具层硬安全上下文。

登录会员身份通过 ContextVar（受信通道）传入工具执行上下文：
- web 层在请求开始时从服务端 session 绑定（web_app.before_request）；
- 图节点在执行 Agent 前从 state.member_id 绑定（BaseAgent._run）；
- 仓库层（flight_repo）读写会员数据前用 enforce_owner 校验归属。

关键点：身份不经过 LLM 决定的工具参数，模型无法通过提示词诱导越权——
即使模型把别人的会员号传给工具，仓库层也会在代码层拒绝。
"""

from contextvars import ContextVar
from typing import Optional

_current_member_id: ContextVar = ContextVar("current_member_id", default=None)


def set_current_member(member_id: Optional[str]):
    """绑定当前受信登录身份，返回 token 供 reset 使用。"""
    return _current_member_id.set((member_id or "").strip().upper() or None)


def reset_current_member(token) -> None:
    _current_member_id.reset(token)


def get_current_member() -> Optional[str]:
    return _current_member_id.get()


def normalize(member_id: Optional[str]) -> str:
    return (member_id or "").strip().upper()


def enforce_owner(member_id: Optional[str], action: str = "查询") -> Optional[dict]:
    """校验目标 member_id 与登录身份一致。

    Returns:
        None 表示通过；否则返回可直接作为工具结果的错误 dict。
    """
    login_id = get_current_member()
    if not login_id:
        return {"error": "未登录：请先登录会员账号后再" + action}
    target = normalize(member_id)
    if not target:
        # 未指明目标会员的场景由调用方自行处理（如默认取登录身份）
        return None
    if target != login_id:
        return {"error": (
            f"无权限：会员号 {target} 与登录身份（{login_id}）不一致，"
            f"只能{action}登录会员本人的数据。如需查询本人数据，可直接说\"查我的订单/账单\"。"
        )}
    return None
