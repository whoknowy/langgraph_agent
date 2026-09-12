"""Prometheus /metrics 指标采集（验收 1.5 + 阶段四 T4.x 的看板数据源）。

采集内容：
- Flask 每请求总量 / 状态码 / 时延（Histogram，可算 P95/P99）；
- LangGraph 服务连通性（gauge，抓取时探测 /ok）；
- 审计越权拦截统计（gauge，来自 audit_logs 当日统计——审计写入多发生在
  LangGraph 进程，跨进程只能以"抓取时查库"的方式汇到 Flask 的 /metrics）。

设计要点：
- prometheus-client 未安装时全部降级：render() 返回 None，/metrics 返回
  提示文本，主业务零影响；
- endpoint 标签用 Flask 的 url_rule（如 /api/my/orders），不用原始 path，
  避免 /api/sessions/<id> 之类的动态段造成标签基数爆炸；
- 本机回环探测必须绕过 HTTP 代理（沙箱代理会把 127.0.0.1 拦成 502）。
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    AVAILABLE = True
except Exception:  # pragma: no cover - 未安装 prometheus-client 时的降级
    AVAILABLE = False

if AVAILABLE:
    http_requests_total = Counter(
        "http_requests_total", "HTTP 请求总量", ["method", "endpoint", "status"])
    http_request_duration_seconds = Histogram(
        "http_request_duration_seconds", "HTTP 请求时延（秒）", ["endpoint"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))
    langgraph_up = Gauge("langgraph_up", "LangGraph 服务连通性（1=通 0=断）")
    audit_denied_total = Gauge(
        "audit_denied_total", "近1天被拦截的越权/注入尝试数（audit_logs）")
    audit_writes_total = Gauge(
        "audit_writes_total", "近1天成功写操作数（audit_logs）")


def record_request(method: str, endpoint: str, status: int, duration_seconds: float) -> None:
    """Flask after_request 钩子调用：累计请求量与时延。"""
    if not AVAILABLE:
        return
    try:
        http_requests_total.labels(method=method, endpoint=endpoint,
                                   status=str(status)).inc()
        http_request_duration_seconds.labels(endpoint=endpoint).observe(duration_seconds)
    except Exception:
        # 指标采集失败不影响业务响应
        pass


def _probe_langgraph(url: str, timeout: float = 1.5) -> bool:
    """探测 LangGraph /ok（绕过代理）。"""
    try:
        import requests
        session = requests.Session()
        session.trust_env = False  # 回环地址必须绕开沙箱/系统代理
        resp = session.get(f"{url.rstrip('/')}/ok", timeout=timeout)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def refresh_runtime_gauges(langgraph_url: str, days: int = 1) -> None:
    """/metrics 抓取时刷新跨进程指标（LangGraph 连通性 + 审计统计）。"""
    if not AVAILABLE:
        return
    try:
        langgraph_up.set(1 if _probe_langgraph(langgraph_url) else 0)
    except Exception:
        pass
    try:
        from services import audit
        st = audit.stats(days=days)
        audit_denied_total.set(float(st.get("denied_attempts", 0)))
        audit_writes_total.set(float(st.get("writes_success", 0)))
    except Exception:
        pass


def render() -> Tuple[Optional[bytes], Optional[str]]:
    """输出 Prometheus 文本格式；未安装依赖时返回 (None, None)。"""
    if not AVAILABLE:
        return None, None
    return generate_latest(), CONTENT_TYPE_LATEST


def now() -> float:
    return time.time()