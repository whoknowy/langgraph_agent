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

# ===== 支付渠道 =====
# mock（默认：站内一步付讫，未配凭据时也能完整演示）
# alipay_sandbox（支付宝沙箱）/ alipay（生产）
PAY_PROVIDER = os.getenv("PAY_PROVIDER", "mock").strip().lower()

# 支付宝（沙箱与生产共用同一组密钥位，靠 ALIPAY_DEBUG 切换网关）
ALIPAY_APP_ID = os.getenv("ALIPAY_APP_ID", "").strip()
# True → 沙箱网关 openapi-sandbox.dl.alipaydev.com；False → 生产网关 openapi.alipay.com
ALIPAY_DEBUG = os.getenv("ALIPAY_DEBUG", "true").strip().lower() in ("1", "true", "yes", "on")
ALIPAY_SIGN_TYPE = os.getenv("ALIPAY_SIGN_TYPE", "RSA2").strip()
# 支付场景：page=电脑网站支付（PC 收银台）；wap=手机网站支付（手机浏览器唤起支付宝 App）
# 移动端（安卓/小程序）务必用 wap：沙箱对 page 的收银台页面引用了内网资源
# （stable.alipay.net），公网加载不全导致"能进收银台但点付款请求失败"。
ALIPAY_SCENE = os.getenv("ALIPAY_SCENE", "page").strip().lower()
# 密钥：环境变量 ALIPAY_PRIVATE_KEY / ALIPAY_PUBLIC_KEY 优先（生产推荐，不落盘），
# 未设置时回退到下面这两个密钥文件（本地开发方便）。详见 keys/README.md
ALIPAY_PRIVATE_KEY_PATH = os.getenv("ALIPAY_PRIVATE_KEY_PATH", "keys/app_private.txt").strip()
ALIPAY_PUBLIC_KEY_PATH = os.getenv("ALIPAY_PUBLIC_KEY_PATH", "keys/alipay_public.txt").strip()
# 回调地址：留空则按当前请求 host 自动拼接（本地开发友好）；公网部署建议显式写死
ALIPAY_NOTIFY_URL = os.getenv("ALIPAY_NOTIFY_URL", "").strip()
ALIPAY_RETURN_URL = os.getenv("ALIPAY_RETURN_URL", "").strip()
# 支付超时（分钟），与 lifecycle 的待支付订单超时保持一致
try:
    PAY_TIMEOUT_MINUTES = int(os.getenv("PAY_TIMEOUT_MINUTES", "15"))
except ValueError:
    PAY_TIMEOUT_MINUTES = 15
