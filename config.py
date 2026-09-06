"""
配置文件：LLM 连接与运行参数（全部可用环境变量覆盖，参见 .env.example）。
"""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI 兼容 API（DeepSeek / SiliconFlow 等均可）
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

# HTTP 请求超时（秒），同时作为 LLM 客户端超时
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))

# 运行环境：dev（默认）| production。生产模式禁止默认凭据、拒绝弱密钥启动，
# 并要求通过 WSGI 服务器（如 waitress）运行，详见 services/bootstrap_security.py
APP_ENV = os.getenv("APP_ENV", "dev").strip().lower()
IS_PRODUCTION = APP_ENV in ("production", "prod")
