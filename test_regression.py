# -*- coding: utf-8 -*-
"""回归验收脚本：登录 → 聊天（流式+卡片） → 订票闭环 → 数据面板。每个用例独立会话。"""
import io
import json
import sys
import urllib.request
import urllib.error
import http.cookiejar
import uuid

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
                     and ("李磊" in t or "M1001" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

t, tools, _ = stream_chat("投诉单号T1000现在处理得怎么样了")
results.append(check("6.投诉查询: query_complaint工具", any(x["name"] == "query_complaint" for x in tools),
                     f"| 工具={[x['name'] for x in tools]}"))

# ---- 7. 投诉提交（登录身份落库） ----
t, tools, _ = stream_chat("上次航班延误害我误转机，这个问题必须正式处理，我现在要正式提交一个投诉")
comp = any(x["name"] == "create_complaint" for x in tools)
results.append(check("7.投诉提交: create_complaint落库", comp and ("T1" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:80]}"))

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
stream_chat("帮我查一下9月2日深圳到西安的机票", sid)
t2, tools2, _ = stream_chat("那9月2日深圳到西安的商务舱价格呢", sid)
results.append(check("11.多轮追问: 接得住上下文", len(tools2) > 0,
                     f"| 第二轮工具={[x['name'] for x in tools2]}"))

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
booked = post_json("/api/book", {"flight_no": "CA1061", "flight_date": "2026-08-31",
                                 "cabin": "经济", "passengers": 2})
order_no = booked.get("order_no")
results.append(check("13b.下单(待支付)", booked.get("success") and booked.get("status") == "待支付" and bool(order_no),
                     f"| 订单号={order_no}"))
paid = post_json("/api/pay", {"order_no": order_no})
results.append(check("13c.支付(已出票)", paid.get("status") == "已出票"))
refunded = post_json("/api/refund", {"order_no": order_no})
results.append(check("13d.退票(退票中)", refunded.get("status") == "退票中"))

# ---- 14. 我的数据面板 ----
my_orders = get_json("/api/my/orders")
mine = [o for o in (my_orders.get("orders") or []) if o.get("order_no") == order_no]
results.append(check("14a.我的订单含新订单", bool(mine) and mine[0].get("status") == "退票中"))
my_complaints = get_json("/api/my/complaints")
results.append(check("14b.我的投诉可查询", my_complaints.get("count", 0) >= 1,
                     f"| 数量={my_complaints.get('count')}"))

print()
print(f"=== 通过 {sum(results)}/{len(results)} ===")
sys.exit(0 if all(results) else 1)
