# -*- coding: utf-8 -*-
"""
纯函数单元测试：零 LLM、零 HTTP 调用，秒级完成——日常迭代随便跑，不消耗 token。

覆盖：退票费率档位、订单状态机、待支付超时取消、会员注册/密码校验、
越权硬校验、站内通知仓库、确认卡片钩子、博查搜索解析（mock）、SSE 状态解析。

运行：
    python -m pytest test_unit.py -q        # pytest 方式（推荐）
    python test_unit.py                     # 直接运行亦可

说明：所有用例跑在 pytest 临时目录的独立 SQLite 上，不污染 data/flight_system.db。
与 test_regression.py（走真实 LLM 流式对话，消耗 token）分工见 README「测试」一节。
"""

import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from services import db, security  # noqa: E402

FUTURE = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d")   # 恒 >72h
NEAR = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")     # 恒 <48h
PAST = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 固件

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """每个用例独立临时库；结束后还原路径并清空身份上下文。"""
    old_path = db.DB_PATH
    db.DB_PATH = str(tmp_path / "unit.db")
    conn = db.get_connection()
    db.init_schema(conn)
    conn.executescript("""
        INSERT INTO airports (iata3, icao4, city_cn, city_en, lat, lon) VALUES
            ('PEK','ZBAA','北京','Beijing',40.08,116.58),
            ('SHA','ZSSS','上海','Shanghai',31.20,121.34);
        INSERT INTO airlines (code, name_cn, is_lcc) VALUES ('CA','中国国航',0);
        INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, duration_min, aircraft, freq_days)
            VALUES ('CA1061','CA','PEK','SHA','06:40','08:50',130,'B737','1234567');
        INSERT INTO flight_prices (flight_no, flight_date, cabin, price) VALUES
            ('CA1061','{f}','经济',600), ('CA1061','{f}','商务',1500),
            ('CA1061','{n}','经济',600), ('CA1061','{n}','商务',1500),
            ('CA1061','{p}','经济',600), ('CA1061','{p}','商务',1500);
        INSERT INTO customers (member_id, name, phone, email, level) VALUES
            ('M1001','李磊','13800000001',NULL,'银卡'),
            ('M1002','王明','13800000002',NULL,'银卡');
    """.format(f=FUTURE, n=NEAR, p=PAST))
    conn.commit()
    conn.close()
    yield
    db.DB_PATH = old_path
    security.reset_current_member(security.set_current_member(None))
    security.reset_current_admin(security.set_current_admin(None))


def _insert_order(order_no, member_id="M1001", status="已出票", flight_date=FUTURE,
                  amount=1000, created=None, flight_no="CA1061"):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, status, created_at, passengers) "
        "VALUES (?,?,?,?,?,?,?,?,1)",
        (order_no, member_id, flight_no, flight_date, "经济", amount, status,
         created or datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- 安全层

class TestSecurity:
    def test_normalize(self):
        assert security.normalize(" m1001 ") == "M1001"
        assert security.normalize("") == ""      # 空值统一返回空串，由调用方 not target 兜底
        assert security.normalize(None) == ""

    def test_enforce_owner_requires_login(self):
        r = security.enforce_owner("M1002")
        assert r and "未登录" in r["error"]

    def test_enforce_owner_rejects_other(self):
        security.set_current_member("M1001")
        r = security.enforce_owner("M1002")
        assert r and "无权限" in r["error"]

    def test_enforce_owner_self_passes(self):
        security.set_current_member("M1001")
        assert security.enforce_owner("M1001") is None
        assert security.enforce_owner("") is None  # 未指定目标 = 查自己

    def test_enforce_owner_admin_exempt(self):
        security.set_current_admin("admin")
        assert security.enforce_owner("M1002") is None


# ---------------------------------------------------------------- 退票费率

class TestRefundFeeRate:
    @pytest.mark.parametrize("hours,rate,tier", [
        (72, 0.05, "72小时以上"), (200, 0.05, "72小时以上"),
        (48, 0.10, "48-72小时"), (71.9, 0.10, "48-72小时"),
        (24, 0.20, "24-48小时"), (47.9, 0.20, "24-48小时"),
        (0, 0.30, "24小时以内"), (23.9, 0.30, "24小时以内"),
    ])
    def test_tiers(self, hours, rate, tier):
        from services.flight_repo import _refund_fee_rate
        r, t = _refund_fee_rate(hours)
        assert r == rate and tier in t

    def test_refund_quote_above_72h(self):
        from services import flight_repo
        security.set_current_member("M1001")
        _insert_order("OTEST01", amount=1000)
        q = flight_repo.refund_quote("OTEST01", member_id="M1001")
        assert q.get("fee") == 50 and q.get("predict_amount") == 950
        assert "72小时以上" in q.get("fee_tier", "")


# ---------------------------------------------------------------- 状态机

class TestTransition:
    def test_pay_transition(self):
        from services import flight_repo
        _insert_order("OTEST02", status="待支付")
        r = flight_repo._transition_order("OTEST02", "M1001", "待支付", "已出票")
        assert r.get("success")

    def test_wrong_from_status_rejected(self):
        from services import flight_repo
        _insert_order("OTEST03", status="已出票")
        r = flight_repo._transition_order("OTEST03", "M1001", "待支付", "已出票")
        assert r.get("error")

    def test_special_refund_records_prev_status(self):
        from services import flight_repo
        _insert_order("OTEST04", status="已改签")
        r = flight_repo._transition_order("OTEST04", "M1001", "已改签", "退票中")
        assert r.get("success")
        conn = db.get_connection()
        row = conn.execute("SELECT prev_status FROM orders WHERE order_no='OTEST04'").fetchone()
        conn.close()
        assert row["prev_status"] == "已改签"


# ---------------------------------------------------------------- 生命周期

class TestLifecycle:
    def test_parse_created_at(self):
        from services.lifecycle import _parse_created_at
        assert _parse_created_at("2026-09-05 16:34:11").hour == 16
        assert _parse_created_at("2026-09-05").hour == 23  # 旧格式按当天末尾计
        with pytest.raises(ValueError):
            _parse_created_at("garbage")

    def test_cancel_stale_pending(self):
        from services.lifecycle import cancel_stale_pending_orders
        stale = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_order("OSTALE", status="待支付", created=stale)
        _insert_order("OFRESH", status="待支付")
        n = cancel_stale_pending_orders()
        conn = db.get_connection()
        s1 = conn.execute("SELECT status FROM orders WHERE order_no='OSTALE'").fetchone()["status"]
        s2 = conn.execute("SELECT status FROM orders WHERE order_no='OFRESH'").fetchone()["status"]
        noti = conn.execute("SELECT content FROM notifications WHERE member_id='M1001'").fetchone()
        conn.close()
        assert n == 1 and s1 == "已取消" and s2 == "待支付"
        assert noti and "OSTALE" in noti["content"]

    def test_mark_flown(self):
        from services.lifecycle import mark_flown_orders
        _insert_order("OPAST", status="已出票", flight_date=PAST)
        _insert_order("OFUT", status="已出票", flight_date=FUTURE)
        n = mark_flown_orders()
        conn = db.get_connection()
        s1 = conn.execute("SELECT status FROM orders WHERE order_no='OPAST'").fetchone()["status"]
        s2 = conn.execute("SELECT status FROM orders WHERE order_no='OFUT'").fetchone()["status"]
        conn.close()
        assert n == 1 and s1 == "已使用" and s2 == "已出票"


# ---------------------------------------------------------------- 注册与密码

class TestRegister:
    def test_register_ok(self):
        from services import flight_repo
        r = flight_repo.register_member("测试员", "13912345678", "secret66")
        assert r.get("member_id", "").startswith("M") and r["level"] == "普卡"

    def test_register_duplicate_phone(self):
        from services import flight_repo
        flight_repo.register_member("甲", "13912345678", "secret66")
        r = flight_repo.register_member("乙", "13912345678", "secret66")
        assert "已注册" in r.get("error", "")

    @pytest.mark.parametrize("name,phone,pwd", [
        ("", "13912345678", "secret66"),        # 缺姓名
        ("张三", "1391234567", "secret66"),      # 手机号10位
        ("张三", "23912345678", "secret66"),     # 手机号非1开头
        ("张三", "13912345678", "12345"),        # 密码过短
        ("张三", "13912345678", "secret66"),     # 正常（email 用例见下）
    ])
    def test_register_validation(self, name, phone, pwd):
        from services import flight_repo
        r = flight_repo.register_member(name, phone, pwd)
        if name and phone == "13912345678" and pwd == "secret66":
            assert r.get("member_id")
        else:
            assert r.get("error")

    def test_verify_password(self):
        from services import flight_repo
        flight_repo.register_member("测试员", "13912345678", "secret66")
        assert flight_repo.verify_member_password("13912345678", "secret66").get("member_id")
        assert flight_repo.verify_member_password("M1001", "wrong") .get("error")  # 无密码账户引导尾号登录
        assert "不正确" in flight_repo.verify_member_password("13912345678", "bad!")["error"]

    def test_verify_by_member_id(self):
        from services import flight_repo
        r = flight_repo.register_member("测试员", "13912345678", "secret66")
        mid = r["member_id"]
        assert flight_repo.verify_member_password(mid, "secret66").get("member_id") == mid

    def test_find_customer_by_account(self):
        from services import flight_repo
        assert flight_repo.find_customer_by_account("m1001")["name"] == "李磊"
        assert flight_repo.find_customer_by_account("13800000002")["member_id"] == "M1002"
        assert flight_repo.find_customer_by_account("M9999").get("error")


# ---------------------------------------------------------------- 站内通知

class TestNotifications:
    def test_create_list_unread_mark(self):
        from services import notification_repo
        notification_repo.create_notification("M1001", "通知A", ntype="refund")
        notification_repo.create_notification("M1001", "通知B", ntype="system")
        items = notification_repo.list_notifications("M1001")
        assert [i["content"] for i in items] == ["通知B", "通知A"]  # 最新在前
        assert notification_repo.unread_count("M1001") == 2
        assert len(notification_repo.list_notifications("M1001", unread_only=True)) == 2
        assert notification_repo.mark_all_read("M1001") == 2
        assert notification_repo.unread_count("M1001") == 0

    def test_isolation_between_members(self):
        from services import notification_repo
        notification_repo.create_notification("M1001", "只给M1001")
        assert notification_repo.unread_count("M1002") == 0
        assert notification_repo.unread_count("M1001") == 1


# ---------------------------------------------------------------- 确认卡片钩子（无 LLM）

class TestConfirmCardHooks:
    def test_product_agent_booking(self):
        from agents.product_agent import ProductAgent
        a = ProductAgent()
        hit, payload = a._on_tool_call("submit_booking_request",
                                       {"flight_no": "CA1061", "flight_date": FUTURE, "cabin": "经济", "passengers": 2})
        assert hit and a._pending_action["type"] == "book_flight"
        assert a._pending_action["passengers"] == 2 and payload["status"] == "awaiting_user_confirmation"

    def test_product_agent_passthrough(self):
        from agents.product_agent import ProductAgent
        a = ProductAgent()
        assert a._on_tool_call("search_flights", {}) == (False, None)

    def test_billing_agent_refund_type(self):
        from agents.billing_agent import BillingAgent
        a = BillingAgent()
        a._on_tool_call("refund_request", {"order_no": "O1", "refund_type": "special", "reason": "延误"})
        assert a._pending_action["type"] == "refund" and a._pending_action["refund_type"] == "special"
        a._on_tool_call("refund_request", {"order_no": "O2", "refund_type": "别乱填"})
        assert a._pending_action["refund_type"] == "voluntary"  # 非法值收敛为自愿退
        a._on_tool_call("change_request", {"order_no": "O1", "new_flight_no": "MU1",
                                           "new_date": FUTURE, "new_cabin": "商务"})
        assert a._pending_action["type"] == "change_flight"

    def test_trip_planner_booking(self):
        from agents.trip_planner_agent import TripPlannerAgent
        a = TripPlannerAgent()
        hit, _ = a._on_tool_call("submit_booking_request",
                                 {"flight_no": "CA1061", "flight_date": FUTURE, "cabin": "经济", "passengers": 1})
        assert hit and a._pending_action["type"] == "book_flight"


class TestSeatMapHook:
    """账单专家 open_seat_map 伪工具钩子（座位图卡片）。"""

    def _add_window_order(self, order_no, hours_ahead=3.0, member_id="M1001"):
        dep = datetime.now() + timedelta(hours=hours_ahead)
        fdate, ftime = dep.date().isoformat(), dep.strftime("%H:%M")
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, "
            "duration_min, aircraft, freq_days) VALUES ('CA9001','CA','PEK','SHA',?,'23:59',120,'B737','1234567')",
            (ftime,))
        conn.execute(
            "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, "
            "status, created_at, passengers) VALUES (?,'M1001','CA9001',?,'经济',800,'已出票',?,1)",
            (order_no, fdate, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return fdate

    def test_open_seat_map_creates_pending_action(self):
        from agents.billing_agent import BillingAgent
        from services import security
        fdate = self._add_window_order("S1")
        a = BillingAgent()
        security.set_current_member("M1001")
        try:
            hit, payload = a._on_tool_call("open_seat_map", {"order_no": "S1"})
            assert hit and payload["status"] == "awaiting_user_confirmation"
            assert a._pending_action["type"] == "seat_map"
            assert a._pending_action["flight_no"] == "CA9001"
            assert a._pending_action["flight_date"] == fdate
        finally:
            security.reset_current_member(security.set_current_member(None))

    def test_open_seat_map_rejects_out_of_window(self):
        from agents.billing_agent import BillingAgent
        from services import security
        self._add_window_order("S2", hours_ahead=40)
        a = BillingAgent()
        security.set_current_member("M1001")
        try:
            hit, payload = a._on_tool_call("open_seat_map", {"order_no": "S2"})
            assert hit and "尚未开放" in payload["error"]
            assert a._pending_action is None
        finally:
            security.reset_current_member(security.set_current_member(None))

    def test_open_seat_map_rejects_other_member(self):
        from agents.billing_agent import BillingAgent
        from services import security
        self._add_window_order("S3")
        a = BillingAgent()
        security.set_current_member("M1002")   # 登录身份与订单归属不符
        try:
            hit, payload = a._on_tool_call("open_seat_map", {"order_no": "S3"})
            assert hit and "无权限" in payload["error"]
        finally:
            security.reset_current_member(security.set_current_member(None))

    def test_open_seat_map_requires_login(self):
        from agents.billing_agent import BillingAgent
        from services import security
        self._add_window_order("S4")
        a = BillingAgent()
        security.reset_current_member(security.set_current_member(None))
        hit, payload = a._on_tool_call("open_seat_map", {"order_no": "S4"})
        assert hit and "error" in payload


# ---------------------------------------------------------------- 博查搜索解析（mock 网络）

class _FakeResp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self._p


class TestBochaSearch:
    def test_missing_key(self, monkeypatch):
        from services import tools
        monkeypatch.delenv("BOCHA_API_KEY", raising=False)
        r = tools._fetch_bocha_web_search("任何查询")
        assert r.get("error") and "BOCHA_API_KEY" in r["error"]

    def test_success_parse(self, monkeypatch):
        from services import tools
        monkeypatch.setenv("BOCHA_API_KEY", "sk-test")
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _FakeResp({"code": 200, "data": {"webPages": {"value": [
                {"name": "标题1", "summary": "摘要很长很长" * 60, "url": "https://a.com", "siteName": "站点A",
                 "datePublished": "2026-09-01T00:00:00+08:00"},
                {"name": "标题2", "snippet": "无summary用snippet", "url": "https://b.com", "siteName": ""},
            ]}}})

        monkeypatch.setattr(tools.requests, "post", fake_post)
        r = tools._fetch_bocha_web_search("兵马俑 门票", count=99)
        assert [x["title"] for x in r["results"]] == ["标题1", "标题2"]
        assert len(r["results"][0]["summary"]) <= 300            # 摘要截断
        assert r["results"][0]["date"] == "2026-09-01"
        body = captured["data"]
        assert '"count": 10' in body                              # count 收敛到上限10
        assert captured["url"] == tools.BOCHA_WEB_SEARCH_URL

    def test_bad_code(self, monkeypatch):
        from services import tools
        monkeypatch.setenv("BOCHA_API_KEY", "sk-test")
        monkeypatch.setattr(tools.requests, "post", lambda *a, **k: _FakeResp({"code": 401, "msg": "key无效"}))
        assert "key无效" in tools._fetch_bocha_web_search("q")["error"]

    def test_invalid_count_defaults(self, monkeypatch):
        from services import tools
        monkeypatch.setenv("BOCHA_API_KEY", "sk-test")
        seen = {}
        monkeypatch.setattr(tools.requests, "post",
                            lambda url, headers=None, data=None, timeout=None: seen.update(data=data) or _FakeResp({"code": 200, "data": {}}))
        tools._fetch_bocha_web_search("q", count="abc")
        assert '"count": 5' in seen["data"]                       # 非法 count 回落默认5

    def test_web_search_in_tool_pool(self):
        from services.tools import all_tools
        assert "web_search" in [t.name for t in all_tools()]


# ---------------------------------------------------------------- SSE 状态解析纯函数

class TestSseParsers:
    def test_normalize_created_at(self):
        from chat_web_service import _normalize_created_at
        assert _normalize_created_at("2026-09-05T09:06:30.859779+00:00") > 1.7e9
        assert _normalize_created_at(123) == 123.0
        now = time.time()
        assert abs(_normalize_created_at("garbage") - now) < 5
        assert abs(_normalize_created_at(None) - now) < 5

    def test_message_count(self):
        from chat_web_service import _message_count_from_state_data
        assert _message_count_from_state_data({"values": {"conversation_history": [{}, {}]}}) == 2
        assert _message_count_from_state_data({"values": {"messages": [{}]}}) == 1
        assert _message_count_from_state_data({"values": {"response": "x"}}) == 1
        assert _message_count_from_state_data({}) == 0

    def test_history_parser_messages(self):
        from chat_web_service import conversation_history_from_state_data
        st = {"values": {"messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "您好"},
            {"role": "assistant", "content": ""},   # 空内容跳过
        ]}}
        h = conversation_history_from_state_data(st)
        assert [(x["is_user"], x["content"]) for x in h] == [(True, "你好"), (False, "您好")]

    def test_history_parser_garbage(self):
        from chat_web_service import conversation_history_from_state_data
        assert conversation_history_from_state_data("not a dict") == []
        assert conversation_history_from_state_data(42) == []


# ---------------------------------------------------------------- 敏感词守卫与打码

class TestSensitiveFilter:
    def test_emotion_l2_passes(self):
        from skills import run_sensitive_guard
        g = run_sensitive_guard("我对航班延误非常不满意")
        assert g["level"] == 2 and not g["blocked"] and g["response"] == ""

    def test_l3_blocks(self):
        from skills import run_sensitive_guard
        g = run_sensitive_guard("你们就是骗子")
        assert g["blocked"] and g["level"] == 3 and "不便于处理" in g["response"]

    def test_compliance_lexicon_blocks(self):
        """合规词库（政治/暴恐/色情/广告）任取文件首个词条应按 L3 拦截。"""
        from pathlib import Path
        from skills import run_sensitive_guard
        lex = Path("skills/lexicons")
        assert lex.exists(), "合规词库文件缺失"
        tested = 0
        for f in sorted(lex.glob("*.txt")):
            first = next((w for w in f.read_text(encoding="utf-8").splitlines() if w.strip()), "")
            if not first:
                continue
            g = run_sensitive_guard(f"这句话里有{first}这个词")
            assert g["blocked"] and g["level"] == 3, f"{f.stem} 首词条未拦截: {first}"
            tested += 1
        assert tested >= 4

    def test_mask_sensitive(self):
        from skills import mask_sensitive
        out = mask_sensitive("这是骗子说的法轮功内容")
        assert "*" in out and "骗子" not in out and "法轮功" not in out
        assert mask_sensitive("正常航班查询没有问题") == "正常航班查询没有问题"

    def test_mask_keeps_l1_l2_readable(self):
        from skills import mask_sensitive
        # 情绪词（L1/L2）不打码——输出侧只拦合规/高危
        assert "不满意" in mask_sensitive("我们非常不满意这个结果")

    def test_stream_masker_split_tokens(self):
        """敏感词被拆在多个 token 里也能完整打码。"""
        from skills import StreamMasker
        m = StreamMasker()
        out = ""
        for ch in "你们就是骗子":  # 逐字符流式
            out += m.feed(ch)
        out += m.flush()
        assert "*" in out and "骗子" not in out

    def test_stream_masker_plain_text_intact(self):
        from skills import StreamMasker
        m = StreamMasker()
        text = "明天北京到上海的航班一共有12班，经济舱价格600元起。"
        out = "".join(m.feed(text[i:i + 3]) for i in range(0, len(text), 3)) + m.flush()
        assert out == text

    def test_complaint_content_masked(self):
        from services import flight_repo, db
        security.set_current_member("M1001")
        r = flight_repo.create_complaint("M1001", None, "服务太差，你们就是骗子公司")
        assert r.get("success")
        conn = db.get_connection()
        row = conn.execute("SELECT content FROM complaints WHERE ticket_no=?", (r["ticket_no"],)).fetchone()
        conn.close()
        assert "骗子" not in row["content"] and "*" in row["content"]


# ---------------------------------------------------------------- 会话标题（功能A）

class TestSessionTitles:
    """session_titles 仓库 + 标题清洗/兜底逻辑（不触 LLM）。"""

    def test_clean_title(self):
        from services.session_titles import clean_title
        assert clean_title("  查询  航班\n信息  ") == "查询 航班 信息"
        assert clean_title(None) == ""
        assert clean_title("正常标题") == "正常标题"
        assert clean_title("x" * 100).endswith("x") and len(clean_title("x" * 100)) == 60

    def test_fallback_title_short_kept(self):
        from services.session_titles import fallback_title
        assert fallback_title("查机票") == "查机票"

    def test_fallback_title_long_truncated(self):
        from services.session_titles import fallback_title, FALLBACK_MAX_LEN
        t = fallback_title("帮我查一下明天北京到上海的经济舱机票有哪些航班可以选择")
        assert len(t) == FALLBACK_MAX_LEN + 1 and t.endswith("…")
        assert t.startswith("帮我查一下明天北京到上海的")

    def test_fallback_title_flattens_whitespace(self):
        from services.session_titles import fallback_title
        assert fallback_title("查询\n航班\t信息") == "查询 航班 信息"

    def test_fallback_title_empty(self):
        from services.session_titles import fallback_title
        assert fallback_title("") == "" and fallback_title(None) == ""

    def test_save_get_roundtrip_and_upsert(self):
        from services import session_titles
        assert session_titles.get_title("T1") == ""
        assert session_titles.save_title(" T1 ", "查询航班") is True
        assert session_titles.get_title("T1") == "查询航班"
        # 覆盖更新
        session_titles.save_title("T1", "改签咨询")
        assert session_titles.get_title("T1") == "改签咨询"

    def test_save_rejects_blank(self):
        from services import session_titles
        assert session_titles.save_title("", "标题") is False
        assert session_titles.save_title("T2", "   ") is False
        assert session_titles.save_title("T2", None) is False

    def test_delete_title(self):
        from services import session_titles
        session_titles.save_title("T3", "投诉进度")
        session_titles.delete_title("T3")
        assert session_titles.get_title("T3") == ""
        session_titles.delete_title("不存在")  # 幂等

    def test_generate_title_fallback_on_llm_error(self, monkeypatch):
        """LLM 抛异常时兜底为首条消息截断（同步路径，注入 _invoke_llm 失败）。"""
        import services.session_titles as st

        def _boom(text):
            raise RuntimeError("no llm")
        monkeypatch.setattr(st, "_invoke_llm", _boom)
        # 15 字消息 → 截断为 14 字 + 省略号
        assert st.generate_title("帮我订一张明天北京到上海的机票") == "帮我订一张明天北京到上海的机…"

    def test_generate_title_empty_message(self):
        import services.session_titles as st
        assert st.generate_title("   ") == ""


# ---------------------------------------------------------------- 工作台趋势（功能B）

class TestStatsTrend:
    """admin_repo.stats_trend 聚合逻辑（临时库种子数据）。"""

    def _seed_order(self, conn, order_no, member_id="M1001", created=None, refunded=None,
                    flight_no="CA1061", status="已出票"):
        conn.execute(
            "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, status, "
            "created_at, refunded_at, passengers) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (order_no, member_id, flight_no, FUTURE, "经济", 800, status,
             created, refunded))

    def test_trend_counts_by_day(self):
        from services import admin_repo
        from datetime import date, timedelta
        conn = db.get_connection()
        today = date.today()
        self._seed_order(conn, "A1", created=today.strftime("%Y-%m-%d 09:00:00"))
        self._seed_order(conn, "A2", created=today.strftime("%Y-%m-%d 21:30:00"))
        self._seed_order(conn, "A3", created=(today - timedelta(days=1)).strftime("%Y-%m-%d 08:00:00"))
        # 超出 7 天窗口的订单不计入
        self._seed_order(conn, "A4", created=(today - timedelta(days=9)).strftime("%Y-%m-%d 08:00:00"))
        # 退款：今日 1 笔、昨日 1 笔、无退款时间的存量订单不计入
        self._seed_order(conn, "A5", created=today.strftime("%Y-%m-%d 10:00:00"),
                         refunded=today.strftime("%Y-%m-%d 12:00:00"), status="已退款")
        self._seed_order(conn, "A6", created=(today - timedelta(days=2)).strftime("%Y-%m-%d 10:00:00"),
                         refunded=(today - timedelta(days=1)).strftime("%Y-%m-%d 15:00:00"), status="已退款")
        self._seed_order(conn, "A7", created=(today - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00"),
                         status="已退款")
        conn.commit()
        conn.close()

        d = admin_repo.stats_trend(7)
        assert len(d["days"]) == 7 and d["days"][-1] == today.isoformat()
        assert d["days"][0] == (today - timedelta(days=6)).isoformat()
        assert d["orders"][-1] == 3          # 今日 A1 A2 A5
        assert d["orders"][-2] == 2          # 昨日 A3 A7
        assert d["refunds"][-1] == 1         # 今日 A5
        assert d["refunds"][-2] == 1         # 昨日 A6
        assert sum(d["orders"]) == 6         # 今日3+昨日2+前日1；9 天前的不算
        assert sum(d["refunds"]) == 2        # 无 refunded_at 的 A7 不算

    def test_trend_top_routes(self):
        from services import admin_repo
        conn = db.get_connection()
        # 航线聚合走 INNER JOIN flights：先补两条航线航班
        conn.execute("INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, duration_min, aircraft, freq_days) "
                     "VALUES ('CA2000','CA','SHA','PEK','11:00','13:10',130,'B737','1234567'),"
                     "('CA3000','CA','PEK','SHA','14:00','16:10',130,'B737','1234567')")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            self._seed_order(conn, f"B1{i}", created=now)                 # 北京-上海 ×3
        for i in range(2):
            self._seed_order(conn, f"B2{i}", created=now, flight_no="CA2000")  # 上海-北京 ×2
        self._seed_order(conn, "B3", created=now, flight_no="CA3000")     # 北京-上海 ×1（第4班）
        conn.commit()
        conn.close()

        d = admin_repo.stats_trend(7)
        top = d["top_routes"]
        assert top[0] == {"route": "北京-上海", "count": 4}
        assert top[1] == {"route": "上海-北京", "count": 2}
        assert len(top) == 2

    def test_trend_empty_db_all_zero(self):
        from services import admin_repo
        d = admin_repo.stats_trend(7)
        assert len(d["days"]) == 7
        assert d["orders"] == [0] * 7 and d["refunds"] == [0] * 7
        assert d["top_routes"] == []

    def test_trend_days_param_clamped(self):
        from services import admin_repo
        assert len(admin_repo.stats_trend(0)["days"]) == 1
        assert len(admin_repo.stats_trend(999)["days"]) == 30

    def test_today_orders_uses_date_prefix(self):
        """get_stats 的今日订单按 created_at 日期前缀统计（回归：等值比较恒为0）。"""
        from services import admin_repo
        conn = db.get_connection()
        self._seed_order(conn, "C1", created=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        conn.commit()
        conn.close()
        assert admin_repo.get_stats()["today_orders"] == 1

    def test_refund_writes_refunded_at(self):
        """自愿退票即时退款应写入 refunded_at（图表数据源）。"""
        from services import flight_repo, db
        conn = db.get_connection()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._seed_order(conn, "D1", created=now)
        conn.commit()
        conn.close()
        security.set_current_member("M1001")
        r = flight_repo.refund_order_instant("D1", "M1001")
        assert r.get("success")
        conn = db.get_connection()
        row = conn.execute("SELECT refunded_at, status FROM orders WHERE order_no='D1'").fetchone()
        conn.close()
        assert row["status"] == "已退款" and row["refunded_at"]


# ---------------------------------------------------------------- 值机选座（功能C）

class TestCheckinWindow:
    """值机窗口纯函数（时间显式注入，确定性好）。"""

    def _dep(self, hours):
        return datetime.now() + timedelta(hours=hours)

    def test_not_open_yet(self):
        from services import checkin_repo
        ok, reason = checkin_repo.checkin_window_status(self._dep(30))
        assert not ok and "24小时" in reason

    def test_open_in_window(self):
        from services import checkin_repo
        ok, reason = checkin_repo.checkin_window_status(self._dep(3))
        assert ok and reason == ""

    def test_closed_near_departure(self):
        from services import checkin_repo
        ok, reason = checkin_repo.checkin_window_status(self._dep(0.3))
        assert not ok and "截止" in reason

    def test_departed(self):
        from services import checkin_repo
        ok, reason = checkin_repo.checkin_window_status(self._dep(-1))
        assert not ok and "已起飞" in reason

    def test_none_departure(self):
        from services import checkin_repo
        ok, reason = checkin_repo.checkin_window_status(None)
        assert not ok and reason


class TestCheckinFlow:
    """座位图生成 + 值机/改座/取消/登机牌（独立临时库）。"""

    def _add_flight_order(self, order_no, member_id="M1001", status="已出票",
                          hours_ahead=3.0, cabin="经济", flight_no="CA9001"):
        """插入一个落在值机窗口内的航班+订单，返回 (flight_no, flight_date)。"""
        dep = datetime.now() + timedelta(hours=hours_ahead)
        fdate = dep.date().isoformat()
        ftime = dep.strftime("%H:%M")
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, "
            "duration_min, aircraft, freq_days) VALUES (?,'CA','PEK','SHA',?,'23:59',120,'B737','1234567')",
            (flight_no, ftime))
        conn.execute(
            "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, "
            "status, created_at, passengers) VALUES (?,?,?,?,?,?,?,?,1)",
            (order_no, member_id, flight_no, fdate, cabin, 800, status,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return flight_no, fdate

    def _pick_seat(self, flight_no, fdate, cabin="经济", want="free"):
        """从确定性座位图里挑一个指定状态的座位。"""
        from services import checkin_repo
        m = checkin_repo.seat_map(flight_no, fdate)
        for row in m["cabins"][cabin]:
            for s in row["seats"]:
                if want == "free" and s["status"] == "free":
                    return s["seat_no"]
                if want == "occupied" and s["status"] == "occupied":
                    return s["seat_no"]
        raise AssertionError(f"座位图里没有符合要求的座位: {cabin}/{want}")

    def test_seat_map_deterministic_and_layout(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K1")
        m1 = checkin_repo.seat_map(flight_no, fdate)
        m2 = checkin_repo.seat_map(flight_no, fdate)
        assert "error" not in m1 and m1["total"] == 162        # 3排×4 + 25排×6
        assert m1["free"] == m2["free"]                        # 确定性预占
        biz_rows = m1["cabins"]["商务"]
        assert len(biz_rows) == 3 and len(biz_rows[0]["seats"]) == 4
        assert len(m1["cabins"]["经济"]) == 25

    def test_seat_map_rejects_unknown_flight(self):
        from services import checkin_repo
        assert "error" in checkin_repo.seat_map("ZZ9999", FUTURE)

    def test_checkin_success_and_boarding_pass(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K2")
        seat = self._pick_seat(flight_no, fdate)
        r = checkin_repo.do_checkin("K2", "M1001", seat)
        assert r.get("success") and r["seat_no"] == seat and r["passenger"] == "李磊"
        assert r["gate"] and r["boarding_time"]
        bp = checkin_repo.get_boarding_pass("K2", "M1001")
        assert bp["seat_no"] == seat and bp["route"] == "北京 → 上海"

    def test_checkin_rejects_occupied_seat(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K3")
        seat = self._pick_seat(flight_no, fdate, want="occupied")
        assert "已被占用" in checkin_repo.do_checkin("K3", "M1001", seat)["error"]

    def test_checkin_rejects_wrong_cabin(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K4", cabin="经济")
        biz_seat = self._pick_seat(flight_no, fdate, cabin="商务")
        assert "商务舱" in checkin_repo.do_checkin("K4", "M1001", biz_seat)["error"]

    def test_checkin_rejects_other_member(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K5", member_id="M1002")
        seat = self._pick_seat(flight_no, fdate)
        assert "无权限" in checkin_repo.do_checkin("K5", "M1001", seat)["error"]
        assert "无权限" in checkin_repo.get_boarding_pass("K5", "M1001")["error"]
        assert "无权限" in checkin_repo.cancel_checkin("K5", "M1001")["error"]

    def test_checkin_rejects_non_issued_status(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K6", status="待支付")
        seat = self._pick_seat(flight_no, fdate)
        assert "待支付" in checkin_repo.do_checkin("K6", "M1001", seat)["error"]

    def test_checkin_rejects_out_of_window(self):
        from services import checkin_repo
        self._add_flight_order("K7", hours_ahead=40)
        assert "尚未开放" in checkin_repo.do_checkin("K7", "M1001", "36E")["error"]

    def test_reseat_releases_old_seat(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K8")
        seat1 = self._pick_seat(flight_no, fdate)
        r1 = checkin_repo.do_checkin("K8", "M1001", seat1)
        assert r1.get("success"), r1
        # 占掉另一个空闲座位作为改座目标
        seat2 = self._pick_seat(flight_no, fdate)
        r2 = checkin_repo.do_checkin("K8", "M1001", seat2)
        assert r2.get("success") and r2["message"] == "改座成功", r2
        conn = db.get_connection()
        old = conn.execute("SELECT status FROM seats WHERE flight_no=? AND seat_no=?",
                           (flight_no, seat1)).fetchone()
        new = conn.execute("SELECT status, order_no FROM seats WHERE flight_no=? AND seat_no=?",
                           (flight_no, seat2)).fetchone()
        conn.close()
        assert old["status"] == "free" and new["status"] == "occupied" and new["order_no"] == "K8"

    def test_cancel_checkin_releases_seat(self):
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K9")
        seat = self._pick_seat(flight_no, fdate)
        checkin_repo.do_checkin("K9", "M1001", seat)
        r = checkin_repo.cancel_checkin("K9", "M1001")
        assert r["success"] and r["released"]
        conn = db.get_connection()
        s = conn.execute("SELECT status FROM seats WHERE flight_no=? AND seat_no=?",
                         (flight_no, seat)).fetchone()
        ck = conn.execute("SELECT 1 FROM checkins WHERE order_no='K9'").fetchone()
        conn.close()
        assert s["status"] == "free" and ck is None
        r2 = checkin_repo.cancel_checkin("K9", "M1001")   # 幂等
        assert r2["success"] and not r2["released"]

    def test_boarding_pass_requires_checkin(self):
        from services import checkin_repo
        self._add_flight_order("K10")
        assert "尚未值机" in checkin_repo.get_boarding_pass("K10", "M1001")["error"]

    def test_unknown_order_and_seat(self):
        from services import checkin_repo
        assert "不存在" in checkin_repo.do_checkin("NOPE", "M1001", "40C")["error"]
        self._add_flight_order("K11")
        assert "不存在" in checkin_repo.do_checkin("K11", "M1001", "99Z")["error"]

    def test_refund_auto_cancels_checkin(self):
        """自愿退票应自动取消值机并释放座位。"""
        from services import checkin_repo, flight_repo
        flight_no, fdate = self._add_flight_order("K12")
        seat = self._pick_seat(flight_no, fdate)
        checkin_repo.do_checkin("K12", "M1001", seat)
        security.set_current_member("M1001")
        r = flight_repo.refund_order_instant("K12", "M1001")
        assert r.get("success"), r
        conn = db.get_connection()
        ck = conn.execute("SELECT 1 FROM checkins WHERE order_no='K12'").fetchone()
        s = conn.execute("SELECT status FROM seats WHERE flight_no=? AND seat_no=?",
                         (flight_no, seat)).fetchone()
        conn.close()
        assert ck is None and s["status"] == "free"

    def test_change_order_auto_cancels_checkin(self):
        """改签应自动取消值机（新航班需重新值机）。"""
        from services import checkin_repo, flight_repo
        self._add_flight_order("K13")
        seat = self._pick_seat("CA9001", (datetime.now() + timedelta(hours=3)).date().isoformat())
        checkin_repo.do_checkin("K13", "M1001", seat)
        conn = db.get_connection()
        dep = datetime.now() + timedelta(days=2)
        conn.execute(
            "INSERT INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, "
            "duration_min, aircraft, freq_days) VALUES ('CA9002','CA','PEK','SHA',?,'23:00',120,'B737','1234567')",
            (dep.strftime("%H:%M"),))
        conn.execute(
            "INSERT INTO flight_prices (flight_no, flight_date, cabin, price) VALUES "
            "('CA9002',?, '经济', 700), ('CA9002', ?, '商务', 1600)",
            (dep.date().isoformat(), dep.date().isoformat()))
        conn.commit()
        conn.close()
        security.set_current_member("M1001")
        r = flight_repo.change_order("K13", "M1001", "CA9002", dep.date().isoformat(), "经济")
        assert r.get("success"), r
        conn = db.get_connection()
        ck = conn.execute("SELECT 1 FROM checkins WHERE order_no='K13'").fetchone()
        conn.close()
        assert ck is None


# ---------------------------------------------------------------- 直接运行入口

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--tb=short"]))
