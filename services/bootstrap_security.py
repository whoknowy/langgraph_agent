"""启动期安全策略：Flask 密钥解析与管理员口令策略。

零重依赖（仅标准库），供 web_app 启动与 db_seed 调用，便于零 token 单测。
原则：
- 生产环境（APP_ENV=production）拒绝一切默认/弱凭据——直接拒绝启动；
- 开发环境对默认值自动降级为生成密钥并持久化，首次登录的管理员强制改密。
"""

import os
import secrets
from pathlib import Path
from typing import Optional

# 众所周知不得用于生产的默认值（含本项目 .env.example 中的占位值）
DEFAULT_SECRETS_DENYLIST = {
    "your-secret-key-here",
    "change-me-to-a-random-string",
    "secret",
    "changeme",
    "password",
    "admin123",
}
SECRET_KEY_MIN_LEN = 32
ADMIN_DEFAULT_PASSWORD = "admin123"
ADMIN_DEFAULT_USERNAME = "admin"
ADMIN_PASSWORD_MIN_LEN = 8


def _load_or_generate(key_file: Path) -> str:
    """读取已持久化密钥；不存在时生成并落盘（保证重启后 session 不失效）。"""
    key_file = Path(key_file)
    if key_file.exists():
        existing = key_file.read_text(encoding="utf-8").strip()
        if len(existing) >= SECRET_KEY_MIN_LEN:
            return existing
    key = secrets.token_hex(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass  # Windows FAT 等场景无 POSIX 权限位
    return key


def resolve_flask_secret(env_value: Optional[str], is_production: bool, key_file) -> str:
    """解析 Flask 会话签名密钥。

    - 生产：必须显式配置、不在默认值黑名单、长度 ≥ 32，否则拒绝启动；
    - 开发：缺省或黑名单值 → 自动生成并持久化到 key_file（并给出提示）。
    """
    value = (env_value or "").strip()
    weak = (not value
            or value.lower() in DEFAULT_SECRETS_DENYLIST
            or len(value) < SECRET_KEY_MIN_LEN)
    if is_production:
        if weak:
            raise SystemExit(
                "⛔ 生产环境必须配置强 FLASK_SECRET_KEY（≥32 位随机串，不得使用默认值），拒绝启动。"
                "生成方式：python -c \"import secrets; print(secrets.token_hex(32))\"")
        return value
    if weak:
        if value:
            print("⚠️ FLASK_SECRET_KEY 为默认/弱值，已忽略并改用自动生成的持久化密钥"
                  f"（{key_file}）；生产环境请显式配置强密钥。")
        return _load_or_generate(Path(key_file))
    return value


def validate_admin_password(password: Optional[str]) -> Optional[str]:
    """新管理员口令策略：通过返回 None，否则返回错误说明。"""
    pw = password or ""
    if len(pw) < ADMIN_PASSWORD_MIN_LEN:
        return f"密码长度至少 {ADMIN_PASSWORD_MIN_LEN} 位"
    if pw.lower() in DEFAULT_SECRETS_DENYLIST:
        return "不得使用默认/常见弱口令"
    return None


def admin_uses_default_password(password_hash: str,
                                check_password_hash_fn) -> bool:
    """判断存量口令哈希是否仍为默认口令 admin123。"""
    try:
        return bool(check_password_hash_fn(password_hash, ADMIN_DEFAULT_PASSWORD))
    except Exception:
        return False
