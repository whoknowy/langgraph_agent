"""支付渠道装配：按配置返回 provider 实例。

真实渠道（支付宝 SDK）延迟导入——没装 SDK 或未配凭据时，
默认的 mock 通道依然可用，不会因 import 失败拖垮整个 Web 服务。

渠道实例按名称缓存：支付宝客户端构造时要读密钥文件并解析 RSA，
每次请求重建既浪费又慢。
"""

from .base import (MODE_DIRECT, MODE_REDIRECT, PROVIDER_ALIPAY,
                   PROVIDER_ALIPAY_SANDBOX, PROVIDER_MOCK, PaymentProvider)
from .mock_provider import MockProvider

_CACHE: dict = {}


def _build(key: str) -> PaymentProvider:
    """按渠道名构造实例。支付宝需要注入配置，不能无参构造。"""
    if key == PROVIDER_MOCK:
        return MockProvider()
    if key in (PROVIDER_ALIPAY_SANDBOX, PROVIDER_ALIPAY):
        from config import (ALIPAY_APP_ID, ALIPAY_PRIVATE_KEY_PATH,
                            ALIPAY_PUBLIC_KEY_PATH, ALIPAY_SCENE,
                            ALIPAY_SIGN_TYPE)
        from .alipay_provider import AlipayProvider
        return AlipayProvider(
            app_id=ALIPAY_APP_ID,
            private_key_path=ALIPAY_PRIVATE_KEY_PATH,
            public_key_path=ALIPAY_PUBLIC_KEY_PATH,
            sign_type=ALIPAY_SIGN_TYPE,
            # page=电脑网站支付（PC 收银台）；wap=手机网站支付（唤起 App）
            scene=ALIPAY_SCENE,
            # 沙箱/生产由渠道名决定；ALIPAY_DEBUG 用于上层选择用哪个渠道名
            debug=(key == PROVIDER_ALIPAY_SANDBOX),
        )
    raise ValueError(f"未知支付渠道：{key}")


def get_provider(name: str = None) -> PaymentProvider:
    """按名称取渠道实例；name 为空时取配置默认值（PAY_PROVIDER，缺省 mock）。"""
    if not name:
        from config import PAY_PROVIDER
        name = PAY_PROVIDER
    key = (name or PROVIDER_MOCK).strip().lower()
    if key not in _CACHE:
        _CACHE[key] = _build(key)
    return _CACHE[key]


def reset_cache() -> None:
    """清空实例缓存（测试或配置热更新用）。"""
    _CACHE.clear()


__all__ = ["PaymentProvider", "MockProvider", "get_provider", "reset_cache",
           "MODE_DIRECT", "MODE_REDIRECT"]
