"""
JWT 令牌签发与校验（多端接入通道：安卓 / 微信小程序无法依赖浏览器 Cookie）。

- 签发：登录成功后以 FLASK_SECRET_KEY 为 HMAC 密钥签出 token，载荷含身份声明与过期时间；
- 校验：客户端在 Authorization: Bearer 头中携带，服务端验签 + 验过期 + 验类型；
- Web 端 Cookie 会话照旧可用，两条通道并存互为回退，老前端零改动。

注意：JWT 载荷为 Base64 明文（仅防篡改、不加密），禁止放手机号等敏感信息；
签发后无法主动作废，演示项目用 7 天 TTL + 客户端 401 重登兜底，上生产需收紧并加吊销表。
"""

import time
from typing import Any, Dict, Optional

import jwt  # PyJWT

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 演示用 7 天，避免演示中途过期；生产建议 ≤24h


def issue_token(claims: Dict[str, Any], secret: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """签发 HS256 JWT：自动补 iat/exp，claims 里的 typ 用于区分 member/admin 通道。"""
    payload = dict(claims)
    now = int(time.time())
    payload.setdefault("iat", now)
    payload["exp"] = now + int(ttl)
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, secret: str, expected_typ: str) -> Optional[Dict[str, Any]]:
    """合法、未过期且 typ 匹配时返回 claims dict；否则一律返回 None（调用方按未登录处理）。"""
    if not token or not secret:
        return None
    try:
        # 算法白名单钉死 HS256，防止 alg 混淆攻击
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != expected_typ:
        return None
    return payload
