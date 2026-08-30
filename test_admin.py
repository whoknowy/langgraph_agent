# -*- coding: utf-8 -*-
"""管理端回归：登录/门禁、退款处理、投诉处理、航班/机场/航司维护、订单与会员查询。"""
import io
import json
import sys
import urllib.request
import urllib.error
import http.cookiejar
import urllib.parse
import random

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5000"


def make_session():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


admin_op = make_session()
member_op = make_session()


def req(op, method, path, obj=None):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8") if obj is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    try:
        return json.loads(op.open(r, timeout=60).read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8", errors="replace") or "{}"), e.code


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    return bool(cond)


results = []

# ---- 登录与门禁 ----
d, code = req(admin_op, "GET", "/admin/api/stats")
results.append(check("1.未登录访问管理接口应401", code == 401))
d, code = req(admin_op, "POST", "/admin/api/login", {"username": "admin", "password": "wrong"})
results.append(check("2.错误密码应401", code == 401))
d, code = req(admin_op, "POST", "/admin/api/login", {"username": "admin", "password": "admin123"})
results.append(check("3.管理员登录成功", code is None and d.get("admin", {}).get("name") == "运营管理员"))
d, _ = req(admin_op, "GET", "/admin/api/stats")
results.append(check("4.工作台统计字段齐全",
                     all(k in d for k in ("pending_refunds", "pending_complaints", "flights_on_sale", "today_orders")),
                     f"| {d}"))

# ---- 造数：会员 M1001 下单→支付→退票，产生两笔退票中 ----
d, code = req(member_op, "POST", "/api/login", {"member_id": "M1001", "phone_suffix": "2835"})
assert code is None, "会员登录失败"


def create_pending_refund():
    d, code = req(member_op, "POST", "/api/book",
                  {"flight_no": "CA1061", "flight_date": "2026-08-31", "cabin": "经济", "passengers": 1})
    order_no = d["order_no"]
    req(member_op, "POST", "/api/pay", {"order_no": order_no})
    d, _ = req(member_op, "POST", "/api/refund", {"order_no": order_no})
    assert d.get("status") == "退票中"
    return order_no


order_a = create_pending_refund()
order_b = create_pending_refund()

d, _ = req(admin_op, "GET", "/admin/api/refunds")
nos = [o["order_no"] for o in d.get("refunds", [])]
results.append(check("5.退款队列含新退票单", order_a in nos and order_b in nos, f"| 队列={len(nos)}单"))

# ---- 退款处理 ----
d, code = req(admin_op, "POST", "/admin/api/refunds/approve",
              {"order_no": order_a, "refund_amount": 900, "admin_note": "按九折协商退款"})
results.append(check("6a.同意退款(部分金额)", code is None and d.get("status") == "已退款" and d.get("refund_amount") == 900))
d, code = req(admin_op, "POST", "/admin/api/refunds/approve", {"order_no": order_a})
results.append(check("6b.重复审批被状态机拒绝", code == 400))
d, _ = req(admin_op, "POST", "/admin/api/refunds/reject", {"order_no": order_b, "admin_note": "特价票不退"})
results.append(check("6c.驳回退票回到已出票", d.get("status") == "已出票"))
d, _ = req(member_op, "GET", "/api/my/orders")
mine = {o["order_no"]: o for o in d.get("orders", [])}
results.append(check("6d.会员侧可见退款结果",
                     mine.get(order_a, {}).get("status") == "已退款" and mine[order_a].get("refund_amount") == 900
                     and mine.get(order_b, {}).get("status") == "已出票"))

# ---- 投诉处理 ----
# 造数：直接给 M1001 插一条"处理中"投诉（保证流程用例确定性）
import sqlite3 as _sq
_conn = _sq.connect("data/flight_system.db")
_test_tn = "TF" + str(random.randint(10000, 99999))
_conn.execute("INSERT OR REPLACE INTO complaints (ticket_no, member_id, order_no, content, status, created_at) VALUES (?,?,?,?,?,?)",
              (_test_tn, "M1001", None, "联程航班延误导致后续行程受阻，要求协助处理", "处理中", __import__("datetime").date.today().isoformat()))
_conn.commit(); _conn.close()

d, _ = req(admin_op, "GET", "/admin/api/complaints?status=" + urllib.parse.quote("处理中"))
target = next((c for c in d.get("complaints", []) if c.get("ticket_no") == _test_tn), None)
results.append(check("7.投诉队列可查(处理中)", target is not None, f"| 造数单号={_test_tn}"))
if target:
    tn = target["ticket_no"]  # _test_tn
    d, code = req(admin_op, "POST", "/admin/api/complaints/resolve", {"ticket_no": tn, "reply": "已补偿200元机票券，24小时内到账"})
    results.append(check("8a.回复并解决", code is None and d.get("status") == "已解决"))
    d, code = req(admin_op, "POST", "/admin/api/complaints/reopen", {"ticket_no": tn})
    results.append(check("8b.重新打开", code is None and d.get("status") == "处理中"))
    d, code = req(admin_op, "POST", "/admin/api/complaints/escalate", {"ticket_no": tn, "note": "需专员跟进"})
    results.append(check("8c.升级", code is None and d.get("status") == "已升级"))
    d, _ = req(admin_op, "POST", "/admin/api/complaints/resolve", {"ticket_no": tn, "reply": "专员已介入，补偿500元"})
    results.append(check("8d.已升级可解决", code is None and d.get("status") == "已解决"))
    # 客户端能查到回复
    d, _ = req(member_op, "GET", "/api/my/complaints")
    row = next((c for c in d.get("complaints", []) if c["ticket_no"] == tn), {})
    results.append(check("8e.客户侧可见管理员回复", row.get("reply", "").find("500元") >= 0))

# ---- 航班维护 ----
suffix = str(random.randint(1000, 9999))
new_flight_no = "CA" + suffix
al_code = "X" + random.choice("ABCDEFGH") + str(random.randint(0, 9))[:1]
al_code = ("X" + random.choice("QWRTYP"))[:2] + str(random.randint(10, 99))[:2]
al_code = "Z" + str(random.randint(0, 9))
ap_code = "".join(random.choice("QXZJNMG") for _ in range(1)) + "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(2))
ap_city = "测试城" + suffix
d, _ = req(admin_op, "GET", "/admin/api/airports")
airports = {a["city_cn"]: a["iata3"] for a in d.get("airports", [])}
dep, arr = airports.get("北京", "PEK"), airports.get("成都", "CTU")
d, code = req(admin_op, "POST", "/admin/api/flights", {
    "flight_no": new_flight_no, "airline_code": "CA", "dep_iata": dep, "arr_iata": arr,
    "dep_time": "08:30", "arr_time": "11:45", "aircraft": "A350",
    "freq_days": "1234567", "econ_price": 980, "business_ratio": 2.2})
results.append(check("9a.新增航班并生成票价", code is None and d.get("prices_generated") == 60,
                     f"| {d.get('message', d.get('error'))}"))
d, code = req(admin_op, "POST", "/admin/api/flights", {
    "flight_no": new_flight_no, "airline_code": "CA", "dep_iata": dep, "arr_iata": arr,
    "dep_time": "08:30", "arr_time": "11:45", "econ_price": 980})
results.append(check("9b.重复航班号被拒绝", code == 400))
d, code = req(admin_op, "POST", "/admin/api/flights", {
    "flight_no": "CA9998", "airline_code": "CA", "dep_iata": dep, "arr_iata": dep,
    "dep_time": "08:30", "arr_time": "11:45", "econ_price": 980})
results.append(check("9c.出发=到达被拒绝", code == 400))

# 新航班客户端可查（repo 层直接验证）
sys.path.insert(0, ".")
from services import flight_repo  # noqa: E402
import datetime  # noqa: E402
tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
sr = flight_repo.search_flights(departure="北京", destination="成都", date_str=tomorrow)
found = [f for f in (sr.get("flights") or []) if f.get("flight_no") == new_flight_no]
results.append(check("9d.新航班客户端可搜到(真实票价)", bool(found), f"| 明日在售{len(sr.get('flights') or [])}班"))

# ---- 机场 / 航司 / 订单 / 会员 ----
d, code = req(admin_op, "POST", "/admin/api/airports", {"iata3": ap_code, "city_cn": ap_city, "city_en": "TestCity" + suffix, "lat": 36.67, "lon": 117.12})
results.append(check("10a.新增机场", code is None))
d, code = req(admin_op, "POST", "/admin/api/airports", {"iata3": ap_code, "city_cn": ap_city, "city_en": "T", "lat": 1, "lon": 1})
results.append(check("10b.重复机场被拒绝", code == 400))
d, code = req(admin_op, "POST", "/admin/api/airlines", {"code": al_code, "name_cn": "测试航空" + suffix, "is_lcc": True})
results.append(check("10c.新增航司", code is None))

d, _ = req(admin_op, "GET", "/admin/api/orders?status=" + urllib.parse.quote("已退款"))
results.append(check("11.订单全局查询(按状态)", any(o["order_no"] == order_a for o in d.get("orders", []))))
d, _ = req(admin_op, "GET", "/admin/api/customers?q=M1001")
results.append(check("12.会员查询", d.get("count", 0) >= 1 and d["customers"][0]["name"] == "李磊"))

# ---- 越权：会员会话访问管理接口 ----
d, code = req(member_op, "GET", "/admin/api/stats")
results.append(check("13.会员会话访问管理接口应401", code == 401))

print()
print(f"=== 通过 {sum(results)}/{len(results)} ===")
sys.exit(0 if all(results) else 1)
