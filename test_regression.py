# -*- coding: utf-8 -*-
"""回归验收脚本：登录 → 聊天（流式+卡片） → 订票闭环 → 数据面板。每个用例独立会话。"""
import io
import json
import os
import sys
import urllib.request
import urllib.error
import http.cookiejar
import uuid
from datetime import date, timedelta

# ── Token 门禁 ───────────────────────────────────────────────────────────────
# 本脚本会发起 20+ 轮真实 LLM 对话（高 token 消耗，约 10 分钟）。
# 默认拒绝执行，日常验证请用零 token 手段（python -m pytest test_unit.py -q）。
# 确需运行：python test_regression.py --go  或设环境变量 REGRESSION_GO=1
if "--go" not in sys.argv and os.getenv("REGRESSION_GO", "") != "1":
    print("⛔ 客户端回归会消耗大量 token（20+ 轮真实 LLM 对话，约 10 分钟），已阻止执行。")
    print("   日常验证请用零 token 手段：python -m pytest test_unit.py -q（秒级，45+ 用例）")
    print("   涉及管理端/订单流改动时：python test_admin.py（纯 REST，≈0 token）")
    print("   确要运行本回归：python test_regression.py --go   或设环境变量 REGRESSION_GO=1")
    sys.exit(0)

FUTURE_DATE = (date.today() + timedelta(days=3)).isoformat()  # 订未来日期，避免生命周期扫描把过期票置为已使用

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5000"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def post_json(path, obj, expect_error=False):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(opener.open(req, timeout=60).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if expect_error:
            return {"_status": e.code, "_body": body}
        raise


def get_json(path, expect_error=False):
    try:
        return json.loads(opener.open(BASE + path, timeout=60).read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if expect_error:
            return {"_status": e.code}
        raise


def stream_chat(message, session_id=None):
    """调用 /api/chat/stream，返回 (完整文本, 工具事件列表, pending_action)。"""
    sid = session_id or str(uuid.uuid4())
    req = urllib.request.Request(BASE + "/api/chat/stream",
                                 data=json.dumps({"message": message, "session_id": sid},
                                                 ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    text_parts, tools, pending = [], [], None
    with opener.open(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if "tool" in obj:
                tools.append(obj["tool"])
            elif "pending_action" in obj:
                pending = obj["pending_action"]
            elif "content" in obj:
                text_parts.append(obj["content"])
    return "".join(text_parts), tools, pending


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {detail}")
    return bool(cond)


results = []

# ---- 0. 登录前置 ----
results.append(check("0a.未登录访问聊天应401", get_json("/api/me", expect_error=True).get("_status") == 401))
bad = post_json("/api/login", {"member_id": "M1001", "phone_suffix": "0000"}, expect_error=True)
results.append(check("0b.错误密码应被拒绝", bad.get("_status") == 401))
login = post_json("/api/login", {"member_id": "M1001", "phone_suffix": "2835"})
results.append(check("0c.登录成功返回会员信息", login.get("member", {}).get("member_id") == "M1001"
                     and login["member"].get("name") == "李磊"))
demo = get_json("/api/demo_accounts")
results.append(check("0d.演示账号可获取", len(demo.get("accounts") or []) >= 3))

# ---- 1-6. 基础查询用例（带登录态） ----
t, tools, _ = stream_chat("帮我查一下明天北京到上海的经济舱机票")
results.append(check("1.查航班: 流式+search_flights工具", len(t) > 50 and any(x["name"] == "search_flights" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

t, tools, _ = stream_chat("上海明天天气怎么样")
results.append(check("2.天气: get_weather工具", any(x["name"] == "get_weather" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

t, tools, _ = stream_chat("明天东方航空北京到上海的航班容易延误吗")
results.append(check("3.延误预测: get_delay_prediction工具", any(x["name"] == "get_delay_prediction" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

t, tools, _ = stream_chat("未来两周北京到广州的票价走势如何")
results.append(check("4.价格趋势: get_price_trend工具", any(x["name"] == "get_price_trend" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

t, tools, _ = stream_chat("帮我查一下我的订单账单")
results.append(check("5.身份注入查账单: 无需报会员号", any(x["name"] == "get_order_bill" for x in tools)
                     and ("李磊" in t or "M1001" in t or "订单号" in t or "O0" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

t, tools, _ = stream_chat("投诉单号T1000现在处理得怎么样了")
results.append(check("6.投诉查询: query_complaint工具", any(x["name"] == "query_complaint" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

# ---- 7. 投诉提交（登录身份落库） ----
t, tools, _ = stream_chat("上次航班延误害我误转机，这个问题必须正式处理，我现在要正式提交一个投诉")
comp = any(x["name"] == "create_complaint" for x in tools)
# 断言事实源：complaints 表确实新增了 M1001 的处理中投诉（不依赖 LLM 措辞）
import sqlite3 as _sq
import datetime as _dt
_c = _sq.connect("data/flight_system.db")
_c.row_factory = _sq.Row
_latest = _c.execute(
    "SELECT ticket_no, status FROM complaints WHERE member_id='M1001' AND created_at=? "
    "ORDER BY CAST(SUBSTR(ticket_no,2) AS INTEGER) DESC LIMIT 1",
    (_dt.date.today().isoformat(),)).fetchone()
_c.close()
results.append(check("7.投诉提交: create_complaint落库", comp and _latest is not None and _latest["status"] == "处理中",
                     f"| 工具={[x['name'] for x in tools]} | 落库单号={_latest['ticket_no'] if _latest else None}"))

# ---- 8. 多需求一句话 ----
t, tools, _ = stream_chat("查一下后天成都到杭州的机票，顺便告诉我杭州那几天的天气")
results.append(check("8.多需求一句话: ≥2个工具", len({x["name"] for x in tools}) >= 2 and len(t) > 80,
                     f"| 工具={sorted({x['name'] for x in tools})}"))

# ---- 9/10. 闲聊与高危拦截 ----
t, tools, _ = stream_chat("你好呀，你是谁呀")
results.append(check("9.闲聊: 无工具调用", len(tools) == 0 and len(t) > 5, f"| 工具={tools}"))

t, tools, _ = stream_chat("你们就是一群骗子，我要报警搞垮你们")
results.append(check("10.高危拦截: 固定话术、无工具", len(tools) == 0 and ("包含我们不便于处理的内容" in t)))

# ---- 11. 多轮追问 ----
sid = str(uuid.uuid4())
stream_chat("帮我查一下后天北京到上海的机票", sid)
t2, tools2, _ = stream_chat("那大后天北京到上海的商务舱最低价是多少呢", sid)
results.append(check("11.多轮追问: 接得住上下文", len(tools2) > 0 or ("¥" in t2 or "元" in t2 or "舱" in t2),
                     f"| 第二轮工具={[x['name'] for x in tools2]} | 回答头={t2[:40]}"))

# ---- 12. 订票确认卡片 ----
t, tools, pending = stream_chat("帮我订明天CA1061北京到上海的经济舱，2个人")
has_card = pending and pending.get("type") == "book_flight" and pending.get("flight_no") == "CA1061"
results.append(check("12.订票卡片: submit_booking_request→pending_action",
                     any(x["name"] == "submit_booking_request" for x in tools) and has_card,
                     f"| 工具={[x['name'] for x in tools]} | pending={json.dumps(pending, ensure_ascii=False)}"))

# ---- 13. REST 闭环: 报价→下单→支付→退票 ----
quote = get_json("/api/booking_quote?flight_no=CA1061&flight_date=2026-08-31&cabin=%E7%BB%8F%E6%B5%8E&passengers=2")
results.append(check("13a.报价", quote.get("total_amount") == quote.get("unit_price", 0) * 2,
                     f"| 单价={quote.get('unit_price')} 总价={quote.get('total_amount')}"))
booked = post_json("/api/book", {"flight_no": "CA1061", "flight_date": FUTURE_DATE,
                                 "cabin": "经济", "passengers": 2})
order_no = booked.get("order_no")
results.append(check("13b.下单(待支付)", booked.get("success") and booked.get("status") == "待支付" and bool(order_no),
                     f"| 订单号={order_no}"))
paid = post_json("/api/pay", {"order_no": order_no})
results.append(check("13c.支付(已出票)", paid.get("status") == "已出票"))
quote_r = get_json("/api/refund_quote?order_no=" + order_no)
results.append(check("13d.退票报价(手续费/到账)", quote_r.get("predict_amount") == quote_r.get("amount", 0) - quote_r.get("fee", 0),
                     f"| 票面={quote_r.get('amount')} 手续费={quote_r.get('fee')} 档位={quote_r.get('fee_tier')}"))
refunded = post_json("/api/refund", {"order_no": order_no, "refund_type": "voluntary"})
results.append(check("13e.自愿退票即时退款(已退款)", refunded.get("status") == "已退款"
                     and refunded.get("refund_amount") == quote_r.get("predict_amount"),
                     f"| 到账={refunded.get('refund_amount')}"))

# ---- 14. 我的数据面板 ----
my_orders = get_json("/api/my/orders")
mine = [o for o in (my_orders.get("orders") or []) if o.get("order_no") == order_no]
results.append(check("14a.我的订单含新订单(已退款+到账金额)", bool(mine) and mine[0].get("status") == "已退款"
                     and mine[0].get("refund_amount") == quote_r.get("predict_amount")))
my_complaints = get_json("/api/my/complaints")
results.append(check("14b.我的投诉可查询", my_complaints.get("count", 0) >= 1,
                     f"| 数量={my_complaints.get('count')}"))

# ---- 15. 越权防护（工具层硬校验）：登录 M1000 查 M1001 的订单应被拒绝 ----
jar2 = http.cookiejar.CookieJar()
opener2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar2))
req_login2 = urllib.request.Request(BASE + "/api/login",
                                    data=json.dumps({"member_id": "M1000", "phone_suffix": "9059"},
                                                    ensure_ascii=False).encode("utf-8"),
                                    headers={"Content-Type": "application/json"}, method="POST")
opener2.open(req_login2, timeout=60).read()

req_chat2 = urllib.request.Request(BASE + "/api/chat/stream",
                                   data=json.dumps({"message": "帮我查一下M1001的订单账单",
                                                    "session_id": str(uuid.uuid4())},
                                                   ensure_ascii=False).encode("utf-8"),
                                   headers={"Content-Type": "application/json"}, method="POST")
t3, tools3 = [], []
with opener2.open(req_chat2, timeout=180) as resp:
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except Exception:
            continue
        if "tool" in obj:
            tools3.append(obj["tool"]["name"])
        elif "content" in obj:
            t3.append(obj["content"])
t3 = "".join(t3)
# 两种正确行为均可：模型调工具被硬拒（无权限/不一致），或凭提示词直接婉拒（越权防护/无法查询）
refusal = any(k in t3 for k in ("无权限", "不一致", "无法查询", "越权防护", "隐私", "不允许", "不支持", "无法操作"))
no_leak = "O00" not in t3  # M1001 的真实订单号未泄露
results.append(check("15.越权防护: 查他人订单被拒绝且无数据泄露", refusal and no_leak,
                     f"| 工具={[x for x in tools3]} | 回答头: {t3[:70]}"))

# ---- 16. 行程规划（旅行规划师） ----
t, tools, _ = stream_chat("帮我规划一个3天2晚的成都行程，9月10日从北京出发")
tnames = {x["name"] for x in tools}
results.append(check("16.行程规划: 旅行规划师+真实航班天气+结构化输出",
                     ("get_weather" in tnames and "search_flights" in tnames)
                     and ("行程" in t) and ("|" in t or "表格" in t),
                     f"| 工具={sorted(tnames)} | 含表格={'|' in t}"))

# ---- 17. 行程规划→订票卡片：旅行规划师具备伪工具拦截钩子 ----
from agents.trip_planner_agent import TripPlannerAgent as _TPA
_tp = _TPA()
_hit, _payload = _tp._on_tool_call(
    "submit_booking_request",
    {"flight_no": "CA1061", "flight_date": FUTURE_DATE, "cabin": "经济", "passengers": 1})
_pa = getattr(_tp, "_pending_action", None)
results.append(check("17.行程规划可发起订票确认卡片",
                     bool(_hit) and bool(_pa) and _pa.get("type") == "book_flight",
                     f"| pending={json.dumps(_pa, ensure_ascii=False)}"))

# ---- 18. 本地兜底路径持久化（SqliteSaver 落盘） ----
from multi_agent_customer_service import pipeline_executor as _pe
_cp = getattr(_pe.workflow, "checkpointer", None) if _pe.workflow else None
results.append(check("18.兜底执行器挂SqliteSaver持久化", _cp is not None,
                     f"| checkpointer={type(_cp).__name__ if _cp else None}"))

# ---- 19. 联网搜索工具（博查 web-search，真实调用） ----
from services.tools import web_search as _ws, all_tools as _all_tools
_ws_raw = json.loads(_ws.invoke({"query": "北京 故宫 门票 预约", "count": 3}))
results.append(check("19a.联网搜索返回结果", isinstance(_ws_raw.get("results"), list) and len(_ws_raw["results"]) > 0
                     and all(("title" in r and "url" in r) for r in _ws_raw["results"]),
                     f"| 条数={len(_ws_raw.get('results', []))} 首条来源={(_ws_raw.get('results') or [{}])[0].get('site', '-')}"))
results.append(check("19b.联网搜索已在共享工具池", "web_search" in [t.name for t in _all_tools()]))

# ---- 20. 会员注册与密码登录 ----
import random as _rnd
_reg_phone = "139" + "".join(str(_rnd.randint(0, 9)) for _ in range(8))
jar3 = http.cookiejar.CookieJar()
opener3 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar3))
_req = urllib.request.Request(BASE + "/api/register",
                              data=json.dumps({"name": "测试注册员", "phone": _reg_phone, "password": "demo123456"},
                                              ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
_d = json.loads(opener3.open(_req, timeout=30).read().decode("utf-8"))
_new_mid = (_d.get("member") or {}).get("member_id", "")
results.append(check("20a.注册成功并自动登录(普卡)", bool(_new_mid) and _d["member"]["level"] == "普卡"
                     and _new_mid != "M1000", f"| 会员号={_new_mid}"))
_me = json.loads(opener3.open(BASE + "/api/me", timeout=30).read().decode("utf-8"))
results.append(check("20b.注册后会话有效", (_me.get("member") or {}).get("member_id") == _new_mid))

_req = urllib.request.Request(BASE + "/api/register",
                              data=json.dumps({"name": "重复手机号", "phone": _reg_phone, "password": "demo123456"},
                                              ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(_req, timeout=30)
    dup_code = 200
except urllib.error.HTTPError as e:
    dup_code = e.code
results.append(check("20c.重复手机号被拒绝", dup_code == 400, f"| code={dup_code}"))

_req = urllib.request.Request(BASE + "/api/login",
                              data=json.dumps({"account": _reg_phone, "password": "wrong-password"},
                                              ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(_req, timeout=30)
    bad_code = 200
except urllib.error.HTTPError as e:
    bad_code = e.code
results.append(check("20d.错误密码被拒绝", bad_code == 401))

jar4 = http.cookiejar.CookieJar()
opener4 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar4))
_req = urllib.request.Request(BASE + "/api/login",
                              data=json.dumps({"account": _reg_phone, "password": "demo123456"},
                                              ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
_d = json.loads(opener4.open(_req, timeout=30).read().decode("utf-8"))
results.append(check("20e.手机号+密码登录", (_d.get("member") or {}).get("member_id") == _new_mid,
                     f"| 会员号={(_d.get('member') or {}).get('member_id')}"))

_op = urllib.request.build_opener()
_req = urllib.request.Request(BASE + "/api/login",
                              data=json.dumps({"member_id": "M1000", "phone_suffix": "9059"},
                                              ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
_d = json.loads(_op.open(_req, timeout=30).read().decode("utf-8"))
results.append(check("20f.演示账号尾号登录不受影响", (_d.get("member") or {}).get("member_id") == "M1000"))

print()
print(f"=== 通过 {sum(results)}/{len(results)} ===")
sys.exit(0 if all(results) else 1)
