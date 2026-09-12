"""零依赖阶梯压测（JMeter 的本地平替，与 ops/jmeter/flight_agent.jmx 同口径）。

阶梯：10 → 50 → 100 → 200 并发线程，每档持续 STEP_SECONDS 秒；
每轮循环依次采样 4 个端点（与 JMeter 公共采样流一致）：
    POST /api/login          （尾号登录，DB 读 + JWT 签发）
    GET  /api/health         （无鉴权基线）
    GET  /api/my/orders      （JWT + 订单聚合）
    GET  /api/flights/search （JWT + 航班检索）

用法（在仓库根目录）：
    .venv/Scripts/python.exe ops/loadtest/step_load.py \
        --base http://127.0.0.1:5000 --member M1001 --suffix 0001

输出（ops/jmeter/results/）：
    step_load_<时间戳>.csv           逐请求明细（step,endpoint,latency_ms,status,ok）
    step_load_<时间戳>_summary.json  分档汇总（QPS / P50 / P95 / P99 / 错误率）

注意：本机回环请求必须绕过系统/沙箱代理（trust_env=False），
否则 127.0.0.1 会被代理拦成 502（见 eval/README.md 同款坑）。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

STEPS = [10, 50, 100, 200]
STEP_SECONDS = 30
WARMUP_SECONDS = 5
ENDPOINTS = ["login", "health", "orders", "search"]
REQUEST_TIMEOUT = 10


def make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 回环地址绕开沙箱/系统代理
    return s


class Recorder:
    """线程安全的逐请求记录器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: list = []

    def add(self, step: int, endpoint: str, latency_ms: float, status: int, ok: bool) -> None:
        with self._lock:
            self.rows.append({
                "ts": round(time.time(), 3),
                "step": step,
                "endpoint": endpoint,
                "latency_ms": round(latency_ms, 1),
                "status": status,
                "ok": 1 if ok else 0,
            })


def _get(sess, base, path, headers=None, params=None):
    t0 = time.perf_counter()
    try:
        resp = sess.get(f"{base}{path}", headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        ok = 200 <= resp.status_code < 300
        return (time.perf_counter() - t0) * 1000, resp.status_code, ok, resp
    except Exception:
        return (time.perf_counter() - t0) * 1000, 0, False, None


def _post(sess, base, path, payload):
    t0 = time.perf_counter()
    try:
        resp = sess.post(f"{base}{path}", json=payload, timeout=REQUEST_TIMEOUT)
        ok = 200 <= resp.status_code < 300
        return (time.perf_counter() - t0) * 1000, resp.status_code, ok, resp
    except Exception:
        return (time.perf_counter() - t0) * 1000, 0, False, None


def worker(step_threads: int, base: str, member: str, suffix: str,
           stop: threading.Event, recorder: Recorder) -> None:
    sess = make_session()
    while not stop.is_set():
        # 1) 登录（每轮取新 token）
        lat, status, ok, resp = _post(sess, base, "/api/login",
                                      {"member_id": member, "phone_suffix": suffix})
        token = ""
        if ok and resp is not None:
            try:
                token = resp.json().get("token", "")
            except Exception:
                token = ""
        recorder.add(step_threads, "login", lat, status, ok and bool(token))

        headers = {"Authorization": f"Bearer {token}"}

        # 2) 健康检查
        lat, status, ok, _ = _get(sess, base, "/api/health")
        recorder.add(step_threads, "health", lat, status, ok)

        # 3) 我的订单
        lat, status, ok, _ = _get(sess, base, "/api/my/orders", headers=headers)
        recorder.add(step_threads, "orders", lat, status, ok)

        # 4) 航班搜索（北京→上海）
        lat, status, ok, _ = _get(sess, base, "/api/flights/search", headers=headers,
                                  params={"departure": "北京", "destination": "上海"})
        recorder.add(step_threads, "search", lat, status, ok)


def percentile(values, p):
    if not values:
        return 0.0
    data = sorted(values)
    k = max(0, min(len(data) - 1, int(round(len(data) * p))))
    return data[k]


def run_step(threads: int, base: str, member: str, suffix: str,
             duration: float, recorder: Recorder, step_label: int) -> None:
    stop = threading.Event()
    pool = [threading.Thread(target=worker, daemon=True,
                             args=(step_label, base, member, suffix, stop, recorder))
            for _ in range(threads)]
    t0 = time.time()
    for t in pool:
        t.start()
    while time.time() - t0 < duration:
        time.sleep(2)
        print(f"  step {step_label:>3} 并发 | {threads} 线程 | "
              f"{time.time() - t0:5.1f}s / {duration}s", end="\r", flush=True)
    stop.set()
    print()


def summarize(recorder: Recorder) -> dict:
    """按 (step, endpoint) 汇总：请求数 / QPS / 均值 / P50 / P95 / P99 / 错误率。"""
    out = {}
    by_step: dict = {}
    for r in recorder.rows:
        by_step.setdefault(r["step"], {}).setdefault(r["endpoint"], []).append(r)

    for step, per_ep in sorted(by_step.items()):
        # 每档实际持续时长按首尾时间戳估
        all_ts = [r["ts"] for rs in per_ep.values() for r in rs]
        duration = max(all_ts) - min(all_ts) if len(all_ts) > 1 else 1.0
        step_stat = {}
        for ep, rows in sorted(per_ep.items()):
            lats = [r["latency_ms"] for r in rows]
            ok_n = sum(r["ok"] for r in rows)
            step_stat[ep] = {
                "requests": len(rows),
                "qps": round(len(rows) / duration, 1) if duration > 0 else 0.0,
                "avg_ms": round(statistics.fmean(lats), 1) if lats else 0.0,
                "p50_ms": round(percentile(lats, 0.50), 1),
                "p95_ms": round(percentile(lats, 0.95), 1),
                "p99_ms": round(percentile(lats, 0.99), 1),
                "error_rate": round(1 - ok_n / len(rows), 4) if rows else 0.0,
            }
        total_reqs = sum(s["requests"] for s in step_stat.values())
        total_err = sum(s["error_rate"] * s["requests"] for s in step_stat.values())
        step_stat["_total"] = {
            "requests": total_reqs,
            "qps": round(total_reqs / duration, 1) if duration > 0 else 0.0,
            "error_rate": round(total_err / total_reqs, 4) if total_reqs else 0.0,
        }
        out[str(step)] = step_stat
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="零依赖阶梯压测")
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--member", default="M1001")
    ap.add_argument("--suffix", default="0001")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "jmeter" / "results"))
    ap.add_argument("--steps", default=",".join(str(s) for s in STEPS))
    ap.add_argument("--step-seconds", type=int, default=STEP_SECONDS)
    args = ap.parse_args()

    base = args.base.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 预检：服务可用 + 登录可用
    sess = make_session()
    lat, status, ok, resp = _get(sess, base, "/api/health")
    if not ok:
        raise SystemExit(f"✗ 服务不可达：{base}/api/health -> HTTP {status}（先启动 web_app.py）")
    lat, status, ok, resp = _post(sess, base, "/api/login",
                                  {"member_id": args.member, "phone_suffix": args.suffix})
    if not ok:
        raise SystemExit(f"✗ 登录失败：HTTP {status} {getattr(resp, 'text', '')[:100]}")
    print(f"✓ 预检通过 {base}（登录 {lat:.0f}ms）")

    recorder = Recorder()

    # 预热（不计入结果）：5 线程 5 秒
    print(f"预热 {WARMUP_SECONDS}s（5 并发，不计入）…")
    warm = Recorder()
    run_step(5, base, args.member, args.suffix, WARMUP_SECONDS, warm, step_label=0)
    warm_rows = len(warm.rows)
    recorder.rows.extend(r for r in warm.rows if False)  # 预热不入正式结果
    print(f"预热完成（{warm_rows} 样本，丢弃）")

    steps = [int(s) for s in args.steps.split(",") if s.strip()]
    for s in steps:
        print(f"▶ 阶梯 {s} 并发，持续 {args.step_seconds}s …")
        run_step(s, base, args.member, args.suffix, args.step_seconds,
                 recorder, step_label=s)

    # 落盘
    csv_path = out_dir / f"step_load_{ts}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "step", "endpoint", "latency_ms", "status", "ok"])
        w.writeheader()
        w.writerows(recorder.rows)

    summary = {
        "captured_at": ts,
        "base": base,
        "member": args.member,
        "steps": summarize(recorder),
        "note": "Python 阶梯压测（ops/loadtest/step_load.py），与 JMeter .jmx 同口径；"
                "每轮循环=登录+健康检查+订单+航班搜索 4 请求",
        "total_samples": len(recorder.rows),
    }
    json_path = out_dir / f"step_load_{ts}_summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✓ 完成 {len(recorder.rows)} 样本")
    print(f"  明细 {csv_path}")
    print(f"  汇总 {json_path}")
    print("\n分档总览（整体 QPS / 错误率）：")
    for step, stat in summary["steps"].items():
        t = stat.get("_total", {})
        print(f"  {step:>4} 并发: QPS={t.get('qps')}  err={t.get('error_rate')}")


if __name__ == "__main__":
    main()
