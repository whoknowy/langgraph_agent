# -*- coding: utf-8 -*-
"""把评测/压测结果整理成图（docs/ 交付文档配图）。

用法：
    python eval/make_charts.py                    # 用最新报告自动出全部图
    python eval/make_charts.py --tasks <json>     # 指定任务集报告
    python eval/make_charts.py --security <json>  # 指定安全探针报告

输出：docs/images/*.png（300dpi，答辩 PPT 可直接引用）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images")

# 中文与主题
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

C_MAIN = "#2563eb"      # 主色 蓝
C_GOOD = "#16a34a"      # 达标 绿
C_BAD = "#dc2626"       # 未达标 红
C_GRAY = "#94a3b8"
C_ACCENT = "#f59e0b"

CAT_CN = {
    "query": "查询", "change": "改签", "refund": "退票",
    "seat": "选座", "complaint": "投诉", "baggage": "行李(已移除)",
}


def latest(pattern: str) -> str:
    files = sorted(glob.glob(os.path.join(ROOT, "eval", "reports", pattern)))
    if not files:
        raise SystemExit(f"找不到报告：{pattern}")
    return files[-1]


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def chart_categories(tasks_report: dict, baseline_report: dict | None):
    """图1：五类任务完成率（当前版 vs 基线 v0 对比）。"""
    cur = tasks_report["by_category"]
    cats = [c for c in ("query", "change", "refund", "seat", "complaint") if c in cur]
    labels = [CAT_CN[c] for c in cats]
    cur_rate = [cur[c]["completed"] / cur[c]["total"] * 100 for c in cats]

    base_rate = None
    if baseline_report:
        bc = baseline_report.get("by_category") or {}
        if all(c in bc for c in cats):
            base_rate = [bc[c]["completed"] / bc[c]["total"] * 100 for c in cats]

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=300)
    x = range(len(cats))
    w = 0.38 if base_rate else 0.5
    if base_rate:
        ax.bar([i - w / 2 for i in x], base_rate, w, label="基线 v0（50条口径）", color=C_GRAY)
    ax.bar([i + (w / 2 if base_rate else 0) for i in x], cur_rate, w,
           label="当前版本（42条口径）", color=C_MAIN)
    for i, v in enumerate(cur_rate):
        ax.text(i + (w / 2 if base_rate else 0), v + 1.5, f"{v:.0f}%",
                ha="center", fontsize=9, color=C_MAIN, fontweight="bold")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 112)
    ax.set_ylabel("端到端完成率")
    ax.axhline(80, color=C_ACCENT, ls="--", lw=1)
    ax.text(len(cats) - 0.45, 82, "验收目标 80%", color=C_ACCENT, fontsize=8)
    ax.set_title("任务集端到端完成率（按业务类别）")
    ax.legend(loc="lower right", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "eval_categories.png"))
    plt.close(fig)


def chart_tool_accuracy(tasks_report: dict):
    """图2：工具调用三分类 + 三大指标 vs 验收目标。"""
    ta = tasks_report["tool_accuracy"]
    total = ta["denominator"]
    wrong = ta["missing"] + ta["wrong_tool"] + ta["wrong_args"]
    correct = ta["correct"]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), dpi=300,
                             gridspec_kw={"width_ratios": [1, 1.25]})

    # 左：三分类占比
    ax = axes[0]
    sizes = [correct, ta["missing"], ta["wrong_tool"], ta["wrong_args"]]
    labels = ["正确", "该调没调", "调错工具", "参数错"]
    colors = [C_GOOD, C_ACCENT, "#7c3aed", C_BAD]
    wedges, *_ = ax.pie(sizes, colors=colors, startangle=90,
                        wedgeprops=dict(width=0.42, edgecolor="white"))
    ax.legend(wedges, [f"{l}  {s}" for l, s in zip(labels, sizes) if s],
              loc="upper center", bbox_to_anchor=(0.5, 0.02), fontsize=9, frameon=False)
    ax.text(0, 0.12, f"{ta['accuracy']*100:.1f}%", ha="center",
            fontsize=17, fontweight="bold", color=C_MAIN)
    ax.text(0, -0.12, "工具调用准确率", ha="center", fontsize=9, color=C_GRAY)
    ax.set_title(f"工具调用判定分布（n={total}）")

    # 右：三大指标 vs 目标
    ax = axes[1]
    metrics = ["端到端完成率", "工具调用准确率", "工具参数准确率"]
    values = [tasks_report["completion_rate"] * 100,
              ta["accuracy"] * 100,
              ta["param_accuracy"] * 100]
    targets = [80, 80, 95]
    bars = ax.barh(metrics[::-1], values[::-1],
                   color=[C_GOOD if v >= t else C_BAD for v, t in zip(values, targets)][::-1],
                   height=0.5)
    for i, (v, t) in enumerate(zip(values[::-1], targets[::-1])):
        ax.text(v + 1.2, i, f"{v:.1f}%", va="center", fontsize=10, fontweight="bold")
        ax.axvline(t, color=C_ACCENT, ls="--", lw=1, alpha=0.6)
    ax.text(targets[2] + 1.2, 2.42, f"目标 {targets[2]}%", color=C_ACCENT, fontsize=8)
    ax.text(targets[0] + 1.2, -0.45, f"目标 {targets[0]}%", color=C_ACCENT, fontsize=8)
    ax.set_xlim(0, 108)
    ax.set_xlabel("百分比")
    ax.set_title("核心指标 vs 验收目标（绿=达标 红=未达）")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "eval_tool_accuracy.png"))
    plt.close(fig)


def chart_security(security_report: dict):
    """图3：安全探针结果。"""
    total = security_report.get("total", 6)
    comp = security_report.get("completion_rate", 0)
    sec = security_report.get("security") or {}
    leaked = sec.get("leaked")
    refunded = sec.get("auto_refunded_orders") or []

    fig, ax = plt.subplots(figsize=(6.2, 3.4), dpi=300)
    ax.axis("off")
    rows = [
        ("提示词注入 / 越权探针", f"{total} 条", ""),
        ("全部被拦截（完成=被拦下）", f"{comp*100:.0f}%", C_GOOD if comp == 1 else C_BAD),
        ("数据泄露（登机牌/手机号）", "无" if not leaked else "有!", C_GOOD if not leaked else C_BAD),
        ("越权退款订单", "0 笔" if not refunded else f"{len(refunded)} 笔!",
         C_GOOD if not refunded else C_BAD),
        ("审计留痕", "可查（/admin/api/audits）", C_GOOD),
    ]
    y = 0.92
    for name, val, color in rows:
        ax.text(0.02, y, name, fontsize=11, va="center")
        ax.text(0.98, y, val, fontsize=11, va="center", ha="right",
                color=color or "#0f172a",
                fontweight="bold" if color else "normal")
        y -= 0.19
        ax.axhline(y + 0.095, color="#e2e8f0", lw=0.8)
    ax.set_title("安全探针：越权尝试拦截率 100%（演示桥段）", fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "eval_security.png"))
    plt.close(fig)


def chart_loadtest():
    """图4：JMeter 阶梯压测（QPS/错误率/P95）。"""
    import csv
    summary_path = os.path.join(ROOT, "ops", "jmeter", "results",
                                "step_load_20260912_142822_summary.json")
    with open(summary_path, encoding="utf-8") as f:
        s = json.load(f)

    # 兼容 summary 结构：steps -> {并发数: {接口: {qps/p95_ms/error_rate/...}}}
    steps = s.get("steps") or {}
    if isinstance(steps, dict) and steps:
        xs = sorted(int(k) for k in steps)

        def _total_qps(x):
            t = steps[str(x)].get("_total") or {}
            return t.get("qps") or sum((v.get("qps") or 0)
                                       for k, v in steps[str(x)].items() if k != "_total")

        def _max_err(x):
            t = steps[str(x)].get("_total") or {}
            if t.get("error_rate") is not None:
                return t["error_rate"]
            return max((v.get("error_rate") or 0) for k, v in steps[str(x)].items()
                       if k != "_total") * 100

        def _p95(x, api):
            return next((v.get("p95_ms") for k, v in steps[str(x)].items() if k == api), None)

        qps = [_total_qps(x) for x in xs]
        err = [_max_err(x) for x in xs]
        orders_p95 = [_p95(x, "orders") for x in xs]
        search_p95 = [_p95(x, "search") for x in xs]
    else:
        raise SystemExit("summary 结构不含 steps，跳过压测图")

    fig, ax1 = plt.subplots(figsize=(8.5, 4.4), dpi=300)

    ax1.plot(xs, qps, "o-", color=C_MAIN, lw=2, label="QPS")
    for x, y in zip(xs, qps):
        ax1.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                     xytext=(0, 8), ha="center", fontsize=9, color=C_MAIN)
    ax1.set_xlabel("并发用户数（阶梯）")
    ax1.set_ylabel("QPS", color=C_MAIN)
    ax1.set_ylim(0, max(qps) * 1.3)

    ax2 = ax1.twinx()
    ax2.plot(xs, err, "s--", color=C_BAD, lw=1.6, label="错误率")
    for x, y in zip(xs, err):
        ax2.annotate(f"{y:.2f}%", (x, y), textcoords="offset points",
                     xytext=(0, -14), ha="center", fontsize=8, color=C_BAD)
    ax2.set_ylabel("错误率 %", color=C_BAD)
    ax2.set_ylim(-0.3, max(err + [1]) * 2)

    ax1.axvspan(0, 50, color=C_GOOD, alpha=0.06)
    ax1.text(28, max(qps) * 1.18, "舒适区 ≤50 并发", color=C_GOOD, fontsize=9, ha="center")
    # P95 参考线标注（orders 为瓶颈接口）
    note = " · ".join(f"{x}并发: orders P95 {p}ms" for x, p in zip(xs, orders_p95) if p)
    ax1.set_xlabel(f"并发用户数（阶梯）   [search P95: " +
                   ", ".join(f"{p}ms" if p else "-" for p in search_p95) + "]")
    ax1.text(0.99, 0.02, note, transform=ax1.transAxes, fontsize=7.5,
             color=C_GRAY, ha="right")
    lines = ax1.get_lines() + ax2.get_lines()
    ax1.legend(lines, [l.get_label() for l in lines], loc="lower left", fontsize=9)
    ax1.set_title("JMeter 阶梯压测：QPS 与错误率（本机 dev 配置）")
    ax1.spines[["top"]].set_visible(False)
    ax2.spines[["top"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "loadtest_step.png"))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks")
    ap.add_argument("--security")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    tasks_path = args.tasks or latest("report_tasks_*.json")
    tasks = load(tasks_path)
    print("任务集报告:", os.path.basename(tasks_path))

    # 基线 v0（50 条历史快照）用于对比，找不到就跳过对比柱
    base = None
    b = os.path.join(ROOT, "eval", "reports", "report_tasks_20260912_121153.json")
    if os.path.exists(b) and tasks_path != b:
        base = load(b)

    chart_categories(tasks, base)
    chart_tool_accuracy(tasks)
    print("✓ eval_categories.png / eval_tool_accuracy.png")

    sec_path = args.security or latest("report_security_*.json")
    if os.path.basename(sec_path) != os.path.basename(tasks_path).replace("tasks", "security"):
        pass  # 选取同名时间戳优先，找不到就用最新
    same = os.path.join(ROOT, "eval", "reports",
                        os.path.basename(tasks_path).replace("report_tasks", "report_security"))
    chart_security(load(same if os.path.exists(same) else sec_path))
    print("✓ eval_security.png")

    try:
        chart_loadtest()
        print("✓ loadtest_step.png")
    except SystemExit as e:
        print("跳过压测图:", e)


if __name__ == "__main__":
    main()
