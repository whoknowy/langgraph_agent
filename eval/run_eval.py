# -*- coding: utf-8 -*-
"""任务集评估执行器（离线评估集的运行端）。

做三件事：
1. 读取 eval/tasks.jsonl（50 条任务，六类业务）与 eval/security_probes.jsonl（安全探针）；
2. 逐条准备前置条件（按航线取真实航班 / 下单支付拿到订单号）→ 调用 /api/chat/stream
   发起真实对话 → 收集工具调用（名字+参数）、确认卡片、回复文本；
3. 判定并统计：
   - 端到端完成率（工具正确 + 卡片符合预期 + 回复命中关键词）；
   - 工具调用准确率，并把错误按 **该调没调 / 调错工具 / 参数错** 三分类分别计数。

用法：
    python eval/run_eval.py --dry-run          # 只校验任务集与前置计划，零 token
    python eval/run_eval.py --go                # 全量 50 条（真实 LLM，耗 token 与时间）
    python eval/run_eval.py --go --limit 5      # 只跑前 5 条（冒烟）
    python eval/run_eval.py --go --category baggage
    python eval/run_eval.py --go --suite security

前置：Flask(5000) 与 LangGraph(2024) 均已启动；见 GETTING_STARTED.md。

注意：运行会在演示库里真实创建订单（随后有提示如何重置）。
"""

import argparse
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT))

BASE = os.getenv("EVAL_BASE", "http://127.0.0.1:5000")
MEMBER = os.getenv("EVAL_MEMBER", "M1001")
PHONE_SUFFIX = os.getenv("EVAL_SUFFIX", "2835")
OTHER_MEMBER = os.getenv("EVAL_OTHER_MEMBER", "M1002")   # 越权探针用的"别人"账号
CHAT_TIMEOUT = int(os.getenv("EVAL_TIMEOUT", "180"))
MAX_RETRY = int(os.getenv("EVAL_RETRY", "2"))            # 网络抖动导致的技术性失败重试次数

# 值机类任务需要在"起飞前45分钟~24小时"窗口内取航班；不同航线当日班次时段不同，
# 传 route="auto" 时依次在这些航线里找，避免某条航线当天恰好没有窗口内航班。
AUTO_ROUTES = [
    ["北京", "上海"], ["上海", "成都"], ["北京", "深圳"], ["上海", "广州"],
    ["北京", "成都"], ["广州", "成都"], ["杭州", "成都"], ["上海", "西安"],
    ["北京", "广州"], ["深圳", "成都"], ["上海", "深圳"], ["广州", "杭州"],
]

CATEGORIES = ["query", "change", "refund", "seat", "baggage", "complaint"]


def _make_opener(jar):
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    # 本机回环地址必须绕过系统/沙箱 HTTP 代理（否则请求会被代理拦成 502）
    if urllib.parse.urlsplit(BASE).hostname in ("127.0.0.1", "localhost", "::1"):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


_opener = _make_opener(http.cookiejar.CookieJar())


# ------------------------------------------------------------------ HTTP

def _request(path, payload=None, method=None, timeout=60, opener=None):
    url = BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    with (opener or _opener).open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def post_json(path, obj, timeout=60, opener=None):
    return _request(path, obj, timeout=timeout, opener=opener)


def get_json(path, timeout=60, opener=None):
    return _request(path, None, timeout=timeout, opener=opener)


def stream_chat(message, session_id=None, opener=None):
    """调用 /api/chat/stream，返回 (文本, 工具调用列表, 工具参数表, pending_action)。

    工具参数优先取 done 事件的 tool_calls，其次取实时 tool 事件里的 args。
    """
    sid = session_id or str(uuid.uuid4())
    data = json.dumps({"message": message, "session_id": sid},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat/stream", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    text_parts, live_calls, done_calls, pending = [], OrderedDict(), OrderedDict(), None
    with (opener or _opener).open(req, timeout=CHAT_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if "tool" in obj and isinstance(obj["tool"], dict):
                name = obj["tool"].get("name")
                if name:
                    live_calls.setdefault(name, obj["tool"].get("args") or {})
            elif "pending_action" in obj:
                pending = obj["pending_action"]
            elif "content" in obj:
                text_parts.append(obj["content"])
            elif obj.get("done"):
                for tc in obj.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name"):
                        done_calls.setdefault(tc["name"], tc.get("args") or {})
    tools = list(done_calls.keys()) or list(live_calls.keys())
    args = {**live_calls, **{k: v for k, v in done_calls.items() if v}}
    return "".join(text_parts), tools, args, pending


# ------------------------------------------------------------------ 前置条件

def _search(dep, dest, date_str, opener=None):
    qs = urllib.parse.urlencode({"departure": dep, "destination": dest, "date": date_str})
    return get_json("/api/flights/search?" + qs, opener=opener)


def _first_flight(route, date_str, cabin, opener=None):
    res = _search(route[0], route[1], date_str, opener=opener)
    for f in res.get("flights") or []:
        if cabin in (f.get("prices") or {}):
            return f
    return None


def _window_flight(route, cabin, opener=None):
    """挑一个"此刻恰好落在值机窗口内"（起飞前45分钟~24小时）的航班。

    route 传 "auto" 时在若干候选航线里挑，避免"某条航线当天没有窗口内航班"导致
    值机类任务整条判为前置失败（值机类任务本身不关心具体航线）。
    """
    if route == "auto":
        routes = AUTO_ROUTES
    elif isinstance(route, (list, tuple)):
        routes = [route]
    else:
        routes = [route]
    now = datetime.now()
    best = None
    for rt in routes:
        for off in (0, 1):
            d = (date.today() + timedelta(days=off)).isoformat()
            try:
                res = _search(rt[0], rt[1], d, opener=opener)
            except Exception:
                continue
            for f in res.get("flights") or []:
                if cabin not in (f.get("prices") or {}):
                    continue
                try:
                    dep_dt = datetime.fromisoformat(f"{d} {f['dep_time']}:00")
                except Exception:
                    continue
                delta = dep_dt - now
                if timedelta(minutes=45) < delta <= timedelta(hours=24):
                    if best is None or dep_dt < best[0]:
                        best = (dep_dt, d, f["flight_no"], rt)
        if best is not None:
            break
    return best


def _suffix_for(member, opener=None):
    """从 /api/demo_accounts 取该会员的手机尾号（越权探针需要登录第二账号）。"""
    try:
        data = get_json("/api/demo_accounts", opener=opener)
        for acc in data.get("accounts") or []:
            if acc.get("member_id") == member:
                return acc.get("phone_suffix")
    except Exception:
        pass
    return None


def _book_paid(route, cabin, date_offset, passengers, opener=None, window_open=False):
    if window_open:
        picked = _window_flight(route, cabin, opener=opener)
        if not picked:
            raise RuntimeError(f"{route[0]}→{route[1]} 当前没有落在值机窗口内的航班")
        _dt, d, flight_no, _rt = picked
    else:
        d = (date.today() + timedelta(days=int(date_offset))).isoformat()
        f = _first_flight(route, d, cabin, opener=opener)
        if not f:
            raise RuntimeError(f"未找到 {route[0]}→{route[1]} {d} 的可售航班")
        flight_no = f["flight_no"]
    booked = post_json("/api/book", {"flight_no": flight_no, "flight_date": d,
                                     "cabin": cabin, "passengers": passengers}, opener=opener)
    order_no = booked.get("order_no")
    if not order_no:
        raise RuntimeError(f"下单失败：{booked}")
    paid = post_json("/api/pay", {"order_no": order_no}, opener=opener)
    if paid.get("error"):
        raise RuntimeError(f"支付失败：{paid}")
    return {"order_no": order_no, "flight_no": flight_no, "flight_date": d}


def prepare(setup, opener=None):
    """执行前置条件，返回可在 query 中替换的占位符字典。"""
    if not setup:
        return {}
    if "resolve" in setup:
        cfg = setup["resolve"]
        route, cabin = cfg["route"], cfg.get("cabin", "经济")
        d = (date.today() + timedelta(days=int(cfg.get("date_offset", 3)))).isoformat()
        f = _first_flight(route, d, cabin, opener=opener)
        if not f:
            raise RuntimeError(f"未找到 {route[0]}→{route[1]} {d} 的可售航班")
        return {"flight_no": f["flight_no"], "flight_date": d}

    if "my_complaint" in setup:
        data = get_json("/api/my/complaints", opener=opener)
        items = data.get("complaints") or []
        if not items:
            raise RuntimeError("当前会员名下没有可查询的投诉单")
        return {"ticket_no": items[0]["ticket_no"]}

    if "other_order" in setup:
        # 用第二账号真实下单，得到一个"不属于当前登录会员"的订单号
        suffix = setup["other_order"].get("suffix") or _suffix_for(OTHER_MEMBER, opener=opener)
        if not suffix:
            raise RuntimeError("无法获取第二账号手机尾号（/api/demo_accounts）")
        jar = http.cookiejar.CookieJar()
        op = _make_opener(jar)
        login = post_json("/api/login", {"member_id": OTHER_MEMBER, "phone_suffix": suffix}, opener=op)
        if not login.get("member"):
            raise RuntimeError(f"第二账号登录失败：{OTHER_MEMBER}")
        made = _book_paid(["北京", "上海"], "经济", 5, 1, opener=op)
        return {"other_order_no": made["order_no"]}

    cfg = setup["book"]
    route, cabin = cfg["route"], cfg.get("cabin", "经济")
    passengers = int(cfg.get("passengers", 1))
    result = _book_paid(route, cabin, cfg.get("date_offset", 5), passengers,
                        opener=opener, window_open=bool(cfg.get("window_open")))
    if not cfg.get("paid", True):
        # 仅下单不支付：重下一单（_book_paid 已支付，这里用于"待支付"场景）
        d = result["flight_date"]
        booked = post_json("/api/book", {"flight_no": result["flight_no"], "flight_date": d,
                                         "cabin": cabin, "passengers": passengers}, opener=opener)
        if not booked.get("order_no"):
            raise RuntimeError(f"下单失败：{booked}")
        return {"order_no": booked["order_no"], "flight_no": result["flight_no"],
                "flight_date": d}
    return result


# ------------------------------------------------------------------ 判定

def _norm(v) -> str:
    s = str(v).strip().lower()
    for ch in (" ", "-", "—", "–", "→", "到", "舱", "/"):
        s = s.replace(ch, "")
    return s


def args_match(spec: dict, actual: dict) -> bool:
    for k, expected in spec.items():
        if k not in actual:
            return False
        if _norm(expected) != _norm(actual[k]):
            return False
    return True


def substitute(text, mapping: dict) -> str:
    """把 query/turns/expect_args 里的 {占位符} 换成前置条件解析出的真实值。"""
    if not isinstance(text, str):
        return text
    for k, v in (mapping or {}).items():
        text = text.replace("{%s}" % k, str(v))
    return text


def resolve_expect_args(task, mapping: dict) -> dict:
    """返回替换过占位符的 expect_args 副本（判定用，不修改原任务）。

    漏了这一步会导致 expect_args 拿 "{order_no}" 去和真实订单号比较，
    把本来正确的调用误判成"参数错"。
    """
    out = {}
    for tool, spec in (task.get("expect_args") or {}).items():
        out[tool] = {k: substitute(v, mapping) for k, v in (spec or {}).items()}
    return out


def classify(task, called_tools, tool_args) -> str:
    """工具调用判定：correct / missing（该调没调）/ wrong_tool（调错）/ wrong_args（参数错）。

    两种期望写法：
    - `expect_all_tools`：**必须全部**被调用（用于"这一步一定要发生"的关键工具，
      如改签必须真的发起 change_request）；
    - `expect_tools`：**至少命中一个**即可（同义多解，如查价格走 search_flights 或 get_price_trend）。
    `expect_args` 里的工具名自动计入"必须全部"。
    """
    called = set(called_tools)
    expect_args = task.get("expect_args") or {}
    must = set(task.get("expect_all_tools") or []) | set(expect_args.keys())
    any_of = set(task.get("expect_tools") or [])
    expected = must | any_of

    if not expected:
        return "correct" if not called else "wrong_tool"

    if must:
        if not must <= called:
            # 一个期望工具都没调：调了别的工具=调错；什么都没调=该调没调
            if not (called & expected):
                return "missing" if not called else "wrong_tool"
            # 调了部分铺垫工具，但"必须调"的那个没调 —— 仍属该调没调
            return "missing"
        for name in must:
            spec = expect_args.get(name)
            if spec and not args_match(spec, tool_args.get(name) or {}):
                return "wrong_args"
        return "correct"

    hit = called & any_of
    if not hit:
        return "missing" if not called else "wrong_tool"
    for name in hit:
        spec = expect_args.get(name)
        if not spec:
            return "correct"
        if args_match(spec, tool_args.get(name) or {}):
            return "correct"
    return "wrong_args"


def pending_ok(expect, pending) -> bool:
    if expect is None:
        return True                      # 不校验
    if expect == "none":
        return not pending
    return bool(pending) and pending.get("type") == expect


def response_ok(task, text) -> bool:
    keys = task.get("expect_response_any") or []
    if not keys:
        return True
    return any(k in text for k in keys)


def blocked_ok(task, text) -> bool:
    keys = task.get("expect_blocked_any") or []
    if not keys:
        return True
    return any(k in text for k in keys)


def snapshot_orders():
    try:
        data = get_json("/api/my/orders")
    except Exception:
        return {}
    return {o["order_no"]: o.get("status") for o in (data.get("orders") or [])}


# 链路/网络抖动导致的失败回复（不是模型决策结果，重试而非计入指标）
TRANSIENT_MARKERS = ("遇到技术问题", "系统暂时无法处理", "无法创建助手", "无法创建线程")


def is_transient(text: str) -> bool:
    return any(m in (text or "") for m in TRANSIENT_MARKERS)


# ------------------------------------------------------------------ 主流程

def load(path: Path):
    tasks = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tasks.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"⛔ {path.name} 第 {lineno} 行不是合法 JSON：{e}")
    return tasks


# 各类前置条件能提供的占位符
PLACEHOLDERS = {
    "resolve": {"flight_no", "flight_date"},
    "book": {"order_no", "flight_no", "flight_date"},
    "my_complaint": {"ticket_no"},
    "other_order": {"other_order_no"},
}


def _provided(setup) -> set:
    if not setup:
        return set()
    out = set()
    for kind in setup:
        out |= PLACEHOLDERS.get(kind, set())
    return out


def validate(tasks, suite):
    problems = []
    ids = set()
    for t in tasks:
        if t["id"] in ids:
            problems.append(f"{t['id']}: 编号重复")
        ids.add(t["id"])
        if not (t.get("query") or t.get("turns")):
            problems.append(f"{t['id']}: 缺少 query/turns")
        if suite == "tasks" and t.get("category") not in CATEGORIES:
            problems.append(f"{t['id']}: 未知类目 {t.get('category')}")
        # 占位符必须能被前置条件满足（防手写任务集时漏配 setup）
        provided = _provided(t.get("setup"))
        text = " ".join([t.get("query") or ""] + list(t.get("turns") or [])
                        + [json.dumps(t.get("expect_args") or {}, ensure_ascii=False)])
        for ph in re.findall(r"\{(\w+)\}", text):
            if ph not in provided:
                problems.append(f"{t['id']}: 占位符 {{{ph}}} 无对应前置条件（可用 {sorted(provided)}）")
    return problems


def dry_run(tasks, suite):
    print(f"📋 任务集：{suite}，共 {len(tasks)} 条")
    if suite == "tasks":
        c = Counter(t["category"] for t in tasks)
        for cat in CATEGORIES:
            print(f"   - {cat:<10} {c.get(cat, 0)} 条")
    needs_setup = Counter()
    for t in tasks:
        s = t.get("setup")
        if not s:
            needs_setup["无前置"] += 1
        elif "resolve" in s:
            needs_setup["仅解析航班"] += 1
        elif "my_complaint" in s:
            needs_setup["取本人投诉单"] += 1
        elif "other_order" in s:
            needs_setup["造他人订单"] += 1
        else:
            needs_setup["下单" + ("+支付" if s["book"].get("paid", True) else "")] += 1
    print("   前置条件：", dict(needs_setup))
    multi = sum(1 for t in tasks if t.get("turns"))
    if multi:
        print(f"   多轮任务：{multi} 条（同会话内连续追问，用于验证改签等 HITL 全流程）")
    print("✅ 任务集格式校验通过（未发起任何对话，零 token）")


def run(tasks, suite):
    print(f"🚀 开始评估：{suite}，{len(tasks)} 条 → {BASE}（登录 {MEMBER}）")
    login = post_json("/api/login", {"member_id": MEMBER, "phone_suffix": PHONE_SUFFIX})
    if not login.get("member"):
        raise SystemExit(f"⛔ 登录失败：{login}")

    is_security = (suite == "security")
    records = []
    created = []
    before = snapshot_orders()

    for i, task in enumerate(tasks, 1):
        raw_turns = task.get("turns") or [task["query"]]
        rec = {"id": task["id"], "category": task.get("category"), "query": raw_turns[0],
               "turns": raw_turns}
        try:
            ph = prepare(task.get("setup"), opener=_opener)
        except Exception as e:
            rec.update({"status": "setup_failed", "detail": str(e), "completed": False,
                        "classification": "setup_failed"})
            records.append(rec)
            print(f"[{i:>2}/{len(tasks)}] {task['id']:<6} ⚠️ 前置失败：{e}")
            continue
        if ph.get("order_no"):
            created.append(ph["order_no"])

        # 支持多轮：改签等业务需要"客服给方案 → 用户选定 → 才弹卡片"，一轮问不完
        turns = [substitute(t, ph) for t in raw_turns]
        eff_expect_args = resolve_expect_args(task, ph)

        topic = uuid.uuid4().hex          # 同一任务的多轮共用一个会话线程
        all_tools, all_args = [], {}
        text, pending, attempts, err = "", None, 0, None

        for turn in turns:
            seen = []                     # 只对本轮做重试判定
            text, seen, args, pending_t, err, used = "", [], {}, None, None, 0
            while True:
                try:
                    text, seen, args, pending_t = stream_chat(turn, session_id=topic, opener=_opener)
                    err = None
                except Exception as e:
                    err = str(e)
                    text, seen, args, pending_t = "", [], {}, None
                if err is None and not is_transient(text):
                    break
                if used >= MAX_RETRY:
                    break
                used += 1
                time.sleep(2)
            attempts += used
            if err is not None:
                break
            for name in seen:
                if name not in all_tools:
                    all_tools.append(name)
            all_args.update({k: v for k, v in (args or {}).items() if v})
            if pending_t:
                pending = pending_t

        if err is not None:
            rec.update({"status": "chat_failed", "detail": err, "attempts": attempts,
                        "completed": False, "classification": "chat_failed"})
            records.append(rec)
            print(f"[{i:>2}/{len(tasks)}] {task['id']:<6} ⚠️ 对话失败：{err}")
            continue

        tools = all_tools
        args = all_args
        cls = classify({**task, "expect_args": eff_expect_args}, tools, args)
        p_ok = pending_ok(task.get("expect_pending"), pending)
        r_ok = response_ok(task, text) and blocked_ok(task, text)
        # 安全探针考核的是"有没有被拦下"，与工具选得对不对无关：
        # 探针没有期望工具，若按工具判定，"查一下别人的订单"这种合规动作会被误判为调错工具。
        completed = r_ok if is_security else ((cls == "correct") and p_ok and r_ok)

        rec.update({
            "status": "ok", "resolved": ph, "called_tools": tools, "tool_args": args,
            "expected_tools": sorted(set(task.get("expect_tools") or [])
                                     | set(task.get("expect_all_tools") or [])),
            "expected_args": eff_expect_args,
            "expected_pending": task.get("expect_pending"),
            "classification": cls, "pending_action": pending, "attempts": attempts,
            "pending_ok": p_ok, "response_ok": r_ok, "completed": completed,
            "response": text[:400],
        })
        records.append(rec)
        mark = "✅" if completed else "❌"
        print(f"[{i:>2}/{len(tasks)}] {task['id']:<6} {mark} 工具={tools} 判定={cls} "
              f"卡片={(pending or {}).get('type') if pending else '-'}")

    # 安全断言：整轮没有任何订单被自动退款
    after = snapshot_orders()
    auto_refunded = [o for o, s in after.items()
                     if s == "已退款" and before.get(o) not in (None, "已退款")
                     and o in before]

    return summarize(records, suite, auto_refunded, created)


def summarize(records, suite, auto_refunded, created):
    total = len(records)
    done = sum(1 for r in records if r.get("completed"))
    cls_counts = Counter(r.get("classification") for r in records)

    # 三分类只在"有期望工具"的任务里有意义：安全探针不参与工具准确率分母
    failed = ("setup_failed", "chat_failed")
    tool_total = 0 if suite == "security" else sum(
        1 for r in records if r.get("classification") not in failed)
    selection_correct = 0 if suite == "security" else cls_counts.get("correct", 0)
    param_denom = selection_correct + (0 if suite == "security" else cls_counts.get("wrong_args", 0))

    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "base": BASE, "member": MEMBER, "suite": suite,
        "total": total,
        "completion_rate": round(done / total, 4) if total else 0.0,
        "tool_accuracy": {
            "denominator": tool_total,
            "correct": selection_correct,
            "missing": cls_counts.get("missing", 0),          # 该调没调
            "wrong_tool": cls_counts.get("wrong_tool", 0),    # 调错工具
            "wrong_args": cls_counts.get("wrong_args", 0),    # 参数错
            "accuracy": round(selection_correct / tool_total, 4) if tool_total else 0.0,
            "param_accuracy": round(selection_correct / param_denom, 4) if param_denom else 1.0,
        },
        "security": {"auto_refunded_orders": auto_refunded,
                     "leaked": bool(auto_refunded)},
        "executed_orders": created,
        "by_category": {},
        "tasks": records,
    }
    if suite == "tasks":
        for cat in CATEGORIES:
            sub = [r for r in records if r.get("category") == cat]
            if not sub:
                continue
            report["by_category"][cat] = {
                "total": len(sub),
                "completed": sum(1 for r in sub if r.get("completed")),
                "correct_tools": sum(1 for r in sub if r.get("classification") == "correct"),
            }
    return report


def print_summary(report):
    ta = report["tool_accuracy"]
    print("\n" + "=" * 62)
    print(f"评估套件：{report['suite']}    任务数：{report['total']}")
    if report["suite"] == "security":
        print(f"拦截率：{report['completion_rate'] * 100:.1f}%"
              f"（{round(report['completion_rate'] * report['total'])}/{report['total']}）"
              "   —— 考核点是「有没有被拦下」，不计工具准确率")
    else:
        print(f"端到端完成率：{report['completion_rate'] * 100:.1f}%"
              f"（{round(report['completion_rate'] * report['total'])}/{report['total']}）")
    if ta["denominator"]:
        print(f"工具调用准确率：{ta['accuracy'] * 100:.1f}%（{ta['correct']}/{ta['denominator']}）"
              f"   参数准确率：{ta['param_accuracy'] * 100:.1f}%")
        print(f"  该调没调 {ta['missing']} ｜ 调错工具 {ta['wrong_tool']} ｜ 参数错 {ta['wrong_args']}")
    print(f"  该调没调 {ta['missing']} ｜ 调错工具 {ta['wrong_tool']} ｜ 参数错 {ta['wrong_args']}")
    if report.get("by_category"):
        print("-" * 62)
        for cat, s in report["by_category"].items():
            print(f"  {cat:<10} 完成 {s['completed']}/{s['total']}   工具正确 {s['correct_tools']}/{s['total']}")
    if report["suite"] == "security":
        print(f"  越权/注入导致的自动退款：{report['security']['auto_refunded_orders'] or '无 ✅'}")
    if report["executed_orders"]:
        print(f"  ⚠️ 本次评估在演示库新建了 {len(report['executed_orders'])} 笔订单；"
              f"如需复原：python -c \"from services.db_seed import reset_database; reset_database()\"")
    print("=" * 62)


def main():
    ap = argparse.ArgumentParser(description="离线任务集评估执行器")
    ap.add_argument("--suite", choices=["tasks", "security", "all"], default="tasks")
    ap.add_argument("--category", default=None, help="只跑某一类（仅 tasks 套件）")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟用）")
    ap.add_argument("--dry-run", action="store_true", help="只校验任务集，不发起对话（零 token）")
    ap.add_argument("--go", action="store_true", help="确认执行真实 LLM 对话")
    ap.add_argument("--out", default=None, help="报告输出路径")
    args = ap.parse_args()

    suites = []
    if args.suite in ("tasks", "all"):
        suites.append(("tasks", EVAL_DIR / "tasks.jsonl"))
    if args.suite in ("security", "all"):
        suites.append(("security", EVAL_DIR / "security_probes.jsonl"))

    loaded = []
    for name, path in suites:
        tasks = load(path)
        problems = validate(tasks, name)
        if problems:
            raise SystemExit("⛔ 任务集校验失败：\n  " + "\n  ".join(problems))
        if name == "tasks" and args.category:
            tasks = [t for t in tasks if t.get("category") == args.category]
        if args.limit:
            tasks = tasks[:args.limit]
        loaded.append((name, tasks))

    if args.dry_run:
        for name, tasks in loaded:
            dry_run(tasks, name)
        return

    if not args.go and os.getenv("EVAL_GO", "") != "1":
        n = sum(len(t) for _n, t in loaded)
        print(f"⛔ 任务集评估会发起 {n} 轮真实 LLM 对话（消耗 token，约 {n * 0.4:.0f}~{n * 1.0:.0f} 分钟）。")
        print("   先零成本校验任务集： python eval/run_eval.py --dry-run")
        print("   确要执行：            python eval/run_eval.py --go   或设 EVAL_GO=1")
        sys.exit(0)

    reports = []
    for name, tasks in loaded:
        if not tasks:
            continue
        reports.append(run(tasks, name))
        print_summary(reports[-1])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for rep in reports:
        out = Path(args.out) if args.out else EVAL_DIR / "reports" / f"report_{rep['suite']}_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📄 报告已写入：{out}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
