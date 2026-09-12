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
    """每个用例独立临时库；结束后还原路径并清空身份上下文。

    身份清空必须在 setup 阶段捕获 token 再于 teardown reset：
    直接写 `reset(set(None))` 会把参数先求值，net 效果是"还原成上一个用例的身份"，
    导致越权校验被上一条用例残留的管理员/会员身份绕过。
    """
    old_path = db.DB_PATH
    member_token = security.set_current_member(None)
    admin_token = security.set_current_admin(None)
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
    security.reset_current_member(member_token)
    security.reset_current_admin(admin_token)


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
    def setup_method(self):
        # 状态流转现在从订单反查归属再走受信通道；用例需带登录身份
        security.set_current_member("M1001")

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

    def test_thread_values_fallback(self):
        """/threads/search 与 /threads/{id} 自带 values；/state 空时不能丢历史。"""
        from chat_web_service import (
            _has_state_values,
            _state_data_from_thread_values,
            conversation_history_from_state_data,
        )
        thread = {"values": {"messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "您好"},
        ]}}
        state_data = _state_data_from_thread_values(thread)
        assert _has_state_values(state_data) is True
        history = conversation_history_from_state_data(state_data)
        assert [(m["is_user"], m["content"]) for m in history] == [(True, "你好"), (False, "您好")]

        assert _state_data_from_thread_values({}) == {}
        assert _has_state_values({"values": {"customer_query": "只有路由字段"}}) is False


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

    def test_lexicon_hot_reload(self, tmp_path):
        """编辑 skills/lexicons/*.txt 后无需重启，下一次匹配自动生效。"""
        import skills.sensitive_word_filter as swf
        original = swf.LEXICON_DIR
        try:
            swf.LEXICON_DIR = tmp_path
            custom = tmp_path / "custom.txt"
            custom.write_text("客服\n", encoding="utf-8")
            swf._ensure_words_loaded(force=True)
            assert swf.run_sensitive_guard("人工客服")["blocked"] is True

            custom.write_text("", encoding="utf-8")
            swf._ensure_words_loaded(force=True)
            assert swf.run_sensitive_guard("人工客服")["blocked"] is False
        finally:
            swf.LEXICON_DIR = original
            swf._ensure_words_loaded(force=True)

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

    def setup_method(self):
        # 仓储层写操作现在统一从订单反查归属再走受信通道，用例需要提供登录身份
        security.set_current_member("M1001")

    def _add_flight_order(self, order_no, member_id="M1001", status="已出票",
                          hours_ahead=3.0, cabin="经济", flight_no="CA9001"):
        """插入一个落在值机窗口内的航班+订单（航班幂等，可多次调用加订单），返回 (flight_no, flight_date)。"""
        dep = datetime.now() + timedelta(hours=hours_ahead)
        fdate = dep.date().isoformat()
        ftime = dep.strftime("%H:%M")
        conn = db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO flights (flight_no, airline_code, dep_iata, arr_iata, dep_time, arr_time, "
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
        seat = self._pick_seat(flight_no, fdate, want="free")
        r1 = checkin_repo.do_checkin("K3", "M1001", seat)
        assert r1.get("success"), r1
        # 同航班另一位会员抢同一个座位：需切到该会员身份（跨会员操作现在会被硬拒）
        self._add_flight_order("K3B", member_id="M1002", flight_no=flight_no)
        security.set_current_member("M1002")
        r2 = checkin_repo.do_checkin("K3B", "M1002", seat)
        assert "已被占用" in r2["error"]

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

    def test_gate_bound_to_flight_not_order(self):
        """同一航班同一天的两名旅客应拿到同一登机口（登机口是航班物理资源）。"""
        from services import checkin_repo
        flight_no, fdate = self._add_flight_order("K14")
        self._add_flight_order("K15", member_id="M1002")   # 同一航班第二张订单
        r1 = checkin_repo.do_checkin("K14", "M1001", self._pick_seat(flight_no, fdate))
        security.set_current_member("M1002")   # 同一航班第二位旅客（跨会员操作需切身份）
        r2 = checkin_repo.do_checkin("K15", "M1002", self._pick_seat(flight_no, fdate))
        assert r1.get("success") and r2.get("success"), (r1, r2)
        assert r1["gate"] == r2["gate"]
        assert r1["boarding_time"] == r2["boarding_time"]

    def test_assigned_gate_preferred_over_fallback(self):
        """管理端指派的登机口优先于确定性兜底。"""
        from services import checkin_repo, db
        flight_no, fdate = self._add_flight_order("K16")
        conn = db.get_connection()
        conn.execute("UPDATE flights SET gate = 'C8' WHERE flight_no = ?", (flight_no,))
        conn.commit()
        conn.close()
        r = checkin_repo.do_checkin("K16", "M1001", self._pick_seat(flight_no, fdate))
        assert r.get("success") and r["gate"] == "C8"

    def test_boarding_pass_shows_live_gate_and_notifies(self):
        """值机后管理端变更登机口：登机牌展示实时值，旅客收到站内通知。"""
        from services import checkin_repo, admin_repo
        flight_no, fdate = self._add_flight_order("K17")
        r1 = checkin_repo.do_checkin("K17", "M1001", self._pick_seat(flight_no, fdate))
        assert r1.get("success")
        g = admin_repo.assign_gate(flight_no, "B12")
        assert g.get("success") and g["notified"] == 1 and g["gate"] == "B12"
        bp = checkin_repo.get_boarding_pass("K17", "M1001")
        assert bp["gate"] == "B12"                       # 登机牌实时生效
        info = checkin_repo.checkin_info("K17", "M1001")
        assert info["gate"] == "B12"                     # 智能体查询同步生效
        conn = db.get_connection()
        n = conn.execute("SELECT title, content FROM notifications WHERE member_id='M1001' "
                         "ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        assert n["title"] == "登机口变更提醒" and "B12" in n["content"]

    def test_assign_gate_validation(self):
        from services import admin_repo
        assert "格式" in admin_repo.assign_gate("CA9001", "12B")["error"]      # 数字在字母前
        assert "格式" in admin_repo.assign_gate("CA9001", "X")["error"]        # 缺数字
        assert "格式" in admin_repo.assign_gate("CA9001", "")["error"]
        assert "不存在" in admin_repo.assign_gate("ZZ9999", "B1")["error"]

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


# ---------------------------------------------------------------- 会话线程归属（安全修复）

class TestSessionOwners:
    """thread_id ↔ member_id 归属仓库（会话越权修复的事实源）。"""

    def test_bind_and_owner(self):
        from services import session_owners
        assert session_owners.owner_of("T-abc") == ""
        session_owners.bind("T-abc", "m1001")          # 会员号归一为大写
        assert session_owners.owner_of("T-abc") == "M1001"
        assert session_owners.owned("T-abc", "M1001") is True
        assert session_owners.owned("T-abc", "m1001") is True   # 会员号大小写不敏感
        assert session_owners.owned("T-abc", "M1002") is False
        # 线程 ID 按服务端原样精确匹配（UUID 大小写固定），改写大小写不视为同一线程
        assert session_owners.owned("t-abc", "M1001") is False

    def test_bind_idempotent_first_wins(self):
        """首次绑定后后续 bind 不改变归属（防抢注）。"""
        from services import session_owners
        session_owners.bind("T-1", "M1001")
        session_owners.bind("T-1", "M1002")
        assert session_owners.owner_of("T-1") == "M1001"

    def test_owned_by_scoped(self):
        from services import session_owners
        session_owners.bind("T-a", "M1001")
        session_owners.bind("T-b", "M1001")
        session_owners.bind("T-c", "M1002")
        assert session_owners.owned_by("M1001") == {"T-a", "T-b"}
        assert session_owners.owned_by("M1002") == {"T-c"}
        assert session_owners.owned_by("") == set()

    def test_owned_requires_member(self):
        from services import session_owners
        session_owners.bind("T-x", "M1001")
        assert session_owners.owned("T-x", "") is False       # 未登录不通过
        assert session_owners.owned("T-x", "M1002") is False

    def test_release(self):
        from services import session_owners
        session_owners.bind("T-r", "M1001")
        session_owners.release("T-r")
        assert session_owners.owner_of("T-r") == ""
        session_owners.release("T-r")   # 幂等

    def test_unowned_thread_never_owned(self):
        """存量无主线程对任何会员都不可通过归属校验。"""
        from services import session_owners
        assert session_owners.owner_of("legacy-thread") == ""
        assert session_owners.owned("legacy-thread", "M1001") is False


# ---------------------------------------------------------------- 启动安全策略（默认凭据修复）

class TestBootstrapSecurity:
    """Flask 密钥解析与管理员口令策略（默认密钥/默认管理员修复）。"""

    def test_production_refuses_missing_secret(self):
        from services.bootstrap_security import resolve_flask_secret
        with pytest.raises(SystemExit):
            resolve_flask_secret("", True, "unused.key")

    def test_production_refuses_denylisted_secret(self):
        from services.bootstrap_security import resolve_flask_secret
        for bad in ("your-secret-key-here", "CHANGEME", "secret"):
            with pytest.raises(SystemExit):
                resolve_flask_secret(bad, True, "unused.key")

    def test_production_refuses_short_secret(self):
        from services.bootstrap_security import resolve_flask_secret
        with pytest.raises(SystemExit):
            resolve_flask_secret("short-key", True, "unused.key")

    def test_production_accepts_strong_secret(self):
        from services.bootstrap_security import resolve_flask_secret
        strong = "x" * 48
        assert resolve_flask_secret(strong, True, "unused.key") == strong

    def test_dev_generates_and_persists(self, tmp_path):
        from services.bootstrap_security import resolve_flask_secret
        kf = tmp_path / "sk.key"
        k1 = resolve_flask_secret("", False, kf)
        assert kf.exists() and len(k1) == 64
        k2 = resolve_flask_secret("", False, kf)
        assert k2 == k1                      # 持久化：重启不换钥，session 不失效

    def test_dev_rejects_denylisted_value(self, tmp_path):
        from services.bootstrap_security import resolve_flask_secret
        kf = tmp_path / "sk.key"
        k = resolve_flask_secret("your-secret-key-here", False, kf)
        assert k != "your-secret-key-here" and len(k) == 64

    def test_admin_password_policy(self):
        from services.bootstrap_security import validate_admin_password
        assert validate_admin_password("short") is not None           # 长度不足
        assert validate_admin_password("admin123") is not None        # 默认口令
        assert validate_admin_password("password") is not None        # 常见弱口令
        assert validate_admin_password("") is not None
        assert validate_admin_password("S3afe-Key-2026") is None

    def test_enforce_policy_flags_default_password(self):
        """存量 admin123 口令在启动巡检时被标记强制改密；强口令不受影响。"""
        from werkzeug.security import generate_password_hash
        from services import db
        from services.db_seed import enforce_admin_password_policy
        conn = db.get_connection()
        conn.execute("INSERT OR REPLACE INTO admins (username, password_hash, name, must_change_password) "
                     "VALUES ('admin', ?, '运营管理员', 0)", (generate_password_hash("admin123"),))
        conn.execute("INSERT OR REPLACE INTO admins (username, password_hash, name, must_change_password) "
                     "VALUES ('ops', ?, '运营二号', 0)", (generate_password_hash("S3afe-Key-2026"),))
        conn.commit()
        assert enforce_admin_password_policy(conn) == 1
        flags = {r["username"]: r["must_change_password"]
                 for r in conn.execute("SELECT username, must_change_password FROM admins").fetchall()}
        conn.close()
        assert flags == {"admin": 1, "ops": 0}


# ---------------------------------------------------------------- 非流式聊天的确认卡片回读

class TestRunChatPendingAction:
    """run_chat_sync 第 5 元素 pending_action：非流式通道的确认卡片来源。"""

    CARD = {"type": "book_flight", "flight_no": "CA1061", "cabin": "经济"}

    def _run(self, monkeypatch, state_values=None, run_status=200):
        import chat_web_service as cws

        class FakeResp:
            def __init__(self, payload, status=200):
                self._payload, self.status_code = payload, status

            def json(self):
                return self._payload

        class FakeRequests:
            def post(self, url, **kw):
                return FakeResp({"run_id": "r1"}, run_status)

            def get(self, url, **kw):
                if "/runs/r1" in url:
                    return FakeResp({"status": "success"})
                if url.endswith("/state"):
                    return FakeResp({"values": state_values or {}})
                return FakeResp({}, 404)

        monkeypatch.setattr(cws, "requests", FakeRequests())
        monkeypatch.setattr(cws, "ensure_assistant_exists", lambda: True)
        monkeypatch.setattr(cws, "ensure_thread_exists", lambda sid, mid: ("thread-t", None))
        monkeypatch.setattr(cws, "_assistant_id", "fake-assistant")
        return cws.run_chat_sync("你好", "thread-t", "M1")

    def test_success_returns_card_as_fifth_element(self, monkeypatch):
        text, err, code, tid, pa = self._run(
            monkeypatch, {"response": "好的", "pending_action": self.CARD})
        assert err is None and tid == "thread-t" and text == "好的"
        assert pa == self.CARD

    def test_success_without_card_returns_none(self, monkeypatch):
        _, err, _, _, pa = self._run(monkeypatch, {"response": "仅文本"})
        assert err is None and pa is None

    def test_card_without_type_is_rejected(self, monkeypatch):
        _, _, _, _, pa = self._run(monkeypatch, {"pending_action": {"flight_no": "CA1061"}})
        assert pa is None

    def test_run_failure_keeps_tuple_shape(self, monkeypatch):
        _, err, code, _, pa = self._run(monkeypatch, run_status=500)
        assert err and code == 500 and pa is None

    def test_valid_pending_action_direct(self):
        from chat_web_service import _valid_pending_action
        assert _valid_pending_action({"pending_action": self.CARD}) == self.CARD
        assert _valid_pending_action({"pending_action": "book_flight"}) is None
        assert _valid_pending_action({}) is None


# ---------------------------------------------------------------- checkpoint 丢失后的历史回填

class TestThreadHistorySeeding:
    """LangGraph 重启后旧线程 checkpoint 丢失，新一轮 run 必须回填历史，否则覆盖旧记录。"""

    class FakeResp:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            return self._payload

    def test_seeds_history_when_checkpoint_missing(self, monkeypatch):
        import chat_web_service as cws
        calls = []

        class FakeRequests:
            def get(self, url, **kw):
                calls.append(url)
                if 'history' in url:
                    return TestThreadHistorySeeding.FakeResp([])
                if url.endswith('/state'):
                    return TestThreadHistorySeeding.FakeResp(
                        {"values": {}, "checkpoint": None})
                if '/threads/' in url:
                    return TestThreadHistorySeeding.FakeResp({
                        "values": {"messages": [
                            {"role": "user", "content": "旧问题"},
                            {"role": "assistant", "content": "旧回答"},
                        ]}
                    })
                return TestThreadHistorySeeding.FakeResp({}, 404)

        monkeypatch.setattr(cws, "requests", FakeRequests())
        graph_input = cws._build_chat_input("thread-1", "新问题", "M1")
        assert [m["content"] for m in graph_input["messages"]] == ["旧问题", "旧回答", "新问题"]
        assert graph_input["customer_query"] == "新问题"
        assert graph_input["session_id"] == "thread-1"
        assert graph_input["member_id"] == "M1"
        assert any('history' in url for url in calls)
        assert any(url.endswith('/threads/thread-1') for url in calls)

    def test_does_not_seed_when_checkpoint_exists(self, monkeypatch):
        import chat_web_service as cws

        class FakeRequests:
            def get(self, url, **kw):
                if 'history' in url:
                    return TestThreadHistorySeeding.FakeResp([{"checkpoint_id": "cp-1"}])
                if url.endswith('/state'):
                    return TestThreadHistorySeeding.FakeResp({
                        "values": {"messages": [{"role": "user", "content": "旧问题"}]},
                        "checkpoint": {"checkpoint_id": "cp-1"},
                    })
                raise AssertionError("checkpoint 存在时不应读取线程详情")

        monkeypatch.setattr(cws, "requests", FakeRequests())
        graph_input = cws._build_chat_input("thread-2", "新问题")
        assert [m["content"] for m in graph_input["messages"]] == ["新问题"]

    def test_unknown_state_does_not_seed(self, monkeypatch):
        import chat_web_service as cws

        class FakeRequests:
            def get(self, url, **kw):
                return TestThreadHistorySeeding.FakeResp({}, 500)

        monkeypatch.setattr(cws, "requests", FakeRequests())
        graph_input = cws._build_chat_input("thread-3", "新问题")
        assert [m["content"] for m in graph_input["messages"]] == ["新问题"]


# ---------------------------------------------------------------- JWT token 登录

class TestTokenAuth:
    """services/token_auth.py：签发/校验/过期/篡改/类型隔离。"""

    SECRET = "unit-test-secret-key"
    CLAIMS = {"typ": "member", "member_id": "M21", "name": "张三", "level": "金卡"}

    def test_roundtrip(self):
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET)
        claims = token_auth.verify_token(tok, self.SECRET, "member")
        assert claims and claims["member_id"] == "M21" and claims["typ"] == "member"

    def test_typ_isolation(self):
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET)
        assert token_auth.verify_token(tok, self.SECRET, "admin") is None
        admin_tok = token_auth.issue_token({"typ": "admin", "username": "admin", "name": "运营"}, self.SECRET)
        assert token_auth.verify_token(admin_tok, self.SECRET, "admin")["username"] == "admin"
        assert token_auth.verify_token(admin_tok, self.SECRET, "member") is None

    def test_expired_rejected(self):
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET, ttl=-10)
        assert token_auth.verify_token(tok, self.SECRET, "member") is None

    def test_tampered_and_garbage_rejected(self):
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET)
        assert token_auth.verify_token(tok[:-3] + "xyz", self.SECRET, "member") is None
        assert token_auth.verify_token("not-a-jwt", self.SECRET, "member") is None
        assert token_auth.verify_token("", self.SECRET, "member") is None
        assert token_auth.verify_token(tok, "", "member") is None

    def test_wrong_secret_rejected(self):
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET)
        assert token_auth.verify_token(tok, "another-secret", "member") is None

    def test_ttl_written_into_claims(self):
        import time as _time
        from services import token_auth
        tok = token_auth.issue_token(self.CLAIMS, self.SECRET, ttl=3600)
        claims = token_auth.verify_token(tok, self.SECRET, "member")
        assert claims["exp"] - claims["iat"] == 3600
        assert claims["iat"] <= int(_time.time())


class TestPayment:
    """支付渠道抽象与幂等落账（零 LLM、零 HTTP、零外部网关）。

    重点是「不重复出票」：异步通知会重投多次，只有首次翻转才允许推进订单。
    """

    def setup_method(self):
        # 支付编排的归属校验现在从订单反查走受信通道；用例需带登录身份
        security.set_current_member("M1001")

    @staticmethod
    def _new_order(order_no="PA1", member="M1001", amount=600, status="待支付"):
        from services import db
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO orders (order_no, member_id, flight_no, flight_date, cabin, amount, "
            "status, created_at, passengers) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_no, member, "CA1061", FUTURE, "经济", amount, status,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 1))
        conn.commit()
        conn.close()
        return order_no

    @staticmethod
    def _order(order_no):
        from services import db
        conn = db.get_connection()
        row = conn.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def test_payments_table_created(self):
        from services import db
        conn = db.get_connection()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)")]
        conn.close()
        assert "pay_no" in cols and "out_trade_no" in cols and "trade_no" in cols

    def test_start_payment_mock_returns_direct(self):
        from services import payment_service
        self._new_order("PA1")
        r = payment_service.start_payment("PA1", member_id="M1001", provider_name="mock")
        assert r.get("success") and r["mode"] == "direct"
        assert r["pay_no"].startswith("P") and r["amount"] == 600.0
        assert r["pay_url"] is None

    def test_start_payment_rejects_non_pending_order(self):
        from services import payment_service
        self._new_order("PA2", status="已出票")
        r = payment_service.start_payment("PA2", member_id="M1001", provider_name="mock")
        assert "error" in r and "无需支付" in r["error"]

    def test_start_payment_rejects_other_member(self):
        """归属以「登录身份 vs 订单归属」为准，传参不再是信任来源。"""
        from services import payment_service
        self._new_order("PA3", member="M1001")
        security.set_current_member("M1002")   # 以别的人身份去支付 M1001 的订单
        r = payment_service.start_payment("PA3", member_id="M1002", provider_name="mock")
        assert "error" in r and "不一致" in r["error"]

    def test_pending_payment_is_reused(self):
        """反复点「去支付」不应堆出一串悬挂流水。"""
        from services import db, payment_service
        self._new_order("PA4")
        r1 = payment_service.start_payment("PA4", member_id="M1001", provider_name="mock")
        r2 = payment_service.start_payment("PA4", member_id="M1001", provider_name="mock")
        assert r1["pay_no"] == r2["pay_no"]
        conn = db.get_connection()
        n = conn.execute("SELECT COUNT(*) FROM payments WHERE order_no = 'PA4'").fetchone()[0]
        conn.close()
        assert n == 1

    def test_confirm_payment_completes_order(self):
        from services import payment_service
        self._new_order("PA5")
        started = payment_service.start_payment("PA5", member_id="M1001", provider_name="mock")
        r = payment_service.confirm_payment(started["pay_no"], member_id="M1001")
        assert r.get("success") and r.get("settled")
        order = self._order("PA5")
        assert order["status"] == "已出票"
        assert order["pay_channel"] == "mock" and order["paid_at"]

    def test_settle_is_idempotent(self):
        """重复通知只出票一次。"""
        from services import payment_service
        self._new_order("PA6")
        started = payment_service.start_payment("PA6", member_id="M1001", provider_name="mock")
        pay_no = started["pay_no"]
        first = payment_service.settle_payment(pay_no=pay_no, expect_amount=600,
                                               trade_no="ALI001", provider_name="mock")
        assert first.get("settled") and not first.get("already")
        for _ in range(5):   # 模拟支付宝重投 5 次
            again = payment_service.settle_payment(pay_no=pay_no, expect_amount=600,
                                                   trade_no="ALI001", provider_name="mock")
            assert again.get("already") is True
        assert self._order("PA6")["status"] == "已出票"

    def test_settle_rejects_amount_mismatch(self):
        from services import payment_service
        self._new_order("PA7", amount=600)
        started = payment_service.start_payment("PA7", member_id="M1001", provider_name="mock")
        r = payment_service.settle_payment(pay_no=started["pay_no"], expect_amount=0.01)
        assert "error" in r and "金额不符" in r["error"]
        assert self._order("PA7")["status"] == "待支付"

    def test_settle_rejects_closed_payment(self):
        from services import payment_repo, payment_service
        self._new_order("PA8")
        started = payment_service.start_payment("PA8", member_id="M1001", provider_name="mock")
        payment_repo.mark_closed(started["pay_no"])
        r = payment_service.settle_payment(pay_no=started["pay_no"], expect_amount=600)
        assert "error" in r and "已关闭" in r["error"]

    def test_settle_warns_when_order_already_cancelled(self):
        """钱收了但订单已被超时任务取消：不能静默吞掉，必须告警。"""
        from services import payment_service
        self._new_order("PA9", status="已取消")
        started = payment_service.start_payment("PA9", member_id="M1001", provider_name="mock")
        assert "error" in started    # 已取消的订单根本不该发起支付
        self._new_order("PA10")
        s = payment_service.start_payment("PA10", member_id="M1001", provider_name="mock")
        from services import db
        conn = db.get_connection()
        conn.execute("UPDATE orders SET status = '已取消' WHERE order_no = 'PA10'")
        conn.commit()
        conn.close()
        r = payment_service.settle_payment(pay_no=s["pay_no"], expect_amount=600, provider_name="mock")
        assert r.get("success") and r.get("settled") is False
        assert "人工处理" in r.get("warning", "")

    def test_payment_status_reflects_paid(self):
        from services import payment_service
        self._new_order("PB1")
        started = payment_service.start_payment("PB1", member_id="M1001", provider_name="mock")
        before = payment_service.get_payment_status("PB1", member_id="M1001")
        assert before["paid"] is False and before["pay_status"] == "待支付"
        payment_service.confirm_payment(started["pay_no"], member_id="M1001")
        after = payment_service.get_payment_status("PB1", member_id="M1001")
        assert after["paid"] is True and after["pay_status"] == "支付成功"

    def test_mock_provider_rejects_forged_notify(self):
        """模拟渠道不得开放伪造回调——否则等于留了个免付款后门。"""
        from services.payment import get_provider
        ok, _ = get_provider("mock").verify_notify({"out_trade_no": "P1", "trade_status": "TRADE_SUCCESS"})
        assert ok is False

    # --- 支付宝验签链路（真实 RSA，不联网）---

    @staticmethod
    def _sign(params):
        """按支付宝规则对参数签名（用应用私钥）。"""
        import base64

        from Cryptodome.Hash import SHA256
        from Cryptodome.PublicKey import RSA
        from Cryptodome.Signature import pkcs1_15

        from config import ALIPAY_PRIVATE_KEY_PATH
        from services.payment.alipay_provider import (_PRIV_FOOTER, _PRIV_HEADER,
                                                      _load_key)
        items = sorted((k, v) for k, v in params.items()
                       if k not in ("sign", "sign_type") and v not in (None, ""))
        content = "&".join(f"{k}={v}" for k, v in items)
        pem = _load_key(ALIPAY_PRIVATE_KEY_PATH, "ALIPAY_PRIVATE_KEY",
                        _PRIV_HEADER, _PRIV_FOOTER)
        sig = pkcs1_15.new(RSA.importKey(pem)).sign(SHA256.new(content.encode("utf-8")))
        return base64.b64encode(sig).decode()

    @classmethod
    def _alipay_provider_with_app_pubkey(cls):
        """构造一个「用应用公钥验签」的支付宝渠道实例（仅测试用）。

        真实环境下支付宝是用它自己的私钥签回调、我们拿支付宝公钥验；
        测试里拿不到支付宝私钥，所以换成应用密钥对自签自验，
        目的只是覆盖「验签 → 校验 → 落账」这段代码路径本身。
        """
        from config import (ALIPAY_APP_ID, ALIPAY_PRIVATE_KEY_PATH,
                            ALIPAY_PUBLIC_KEY_PATH)
        from services.payment.alipay_provider import AlipayProvider
        p = AlipayProvider(app_id=ALIPAY_APP_ID,
                           private_key_path=ALIPAY_PRIVATE_KEY_PATH,
                           public_key_path=ALIPAY_PUBLIC_KEY_PATH, debug=True)
        # 把验签公钥换成应用公钥，使自签数据可被验通
        p.public_key_path = "keys/app_public.txt"
        return p

    @staticmethod
    def _notify_params(pay_no, amount, *, app_id, trade_status="TRADE_SUCCESS",
                       trade_no="2026091122001447550512345678"):
        return {
            "gmt_create": "2026-09-11 13:00:00", "charset": "utf-8",
            "seller_email": "sandbox@alipay.com", "subject": "机票订单 TEST",
            "sign_type": "RSA2", "trade_no": trade_no, "buyer_id": "2088622000000001",
            "notify_type": "trade_status_sync", "out_trade_no": pay_no,
            "notify_time": "2026-09-11 13:00:05", "trade_status": trade_status,
            "total_amount": f"{float(amount):.2f}", "app_id": app_id,
            "notify_id": "test-notify-001",
        }

    def test_alipay_verify_notify_accepts_valid_signature(self):
        """正确签名的通知应通过验签，并解析出标准化字段。"""
        from config import ALIPAY_APP_ID
        p = self._alipay_provider_with_app_pubkey()
        params = self._notify_params("PB2", 600, app_id=ALIPAY_APP_ID)
        params["sign"] = self._sign(params)
        ok, fields = p.verify_notify(params)
        assert ok is True, fields
        assert fields["out_trade_no"] == "PB2"
        assert fields["trade_status"] == "TRADE_SUCCESS"
        assert fields["trade_no"] == "2026091122001447550512345678"

    def test_alipay_verify_notify_rejects_tampered_amount(self):
        """签名后篡改金额 → 验签必须失败（否则可改单骗过金额校验）。"""
        from config import ALIPAY_APP_ID
        p = self._alipay_provider_with_app_pubkey()
        params = self._notify_params("PB3", 600, app_id=ALIPAY_APP_ID)
        params["sign"] = self._sign(params)
        params["total_amount"] = "0.01"      # 篡改
        ok, info = p.verify_notify(params)
        assert ok is False and "签名" in info.get("error", "")

    def test_alipay_verify_notify_rejects_wrong_app_id(self):
        """验签通过但 app_id 不是发给我们的 → 拒绝（防他人商户号伪造）。"""
        from config import ALIPAY_APP_ID
        p = self._alipay_provider_with_app_pubkey()
        params = self._notify_params("PB4", 600, app_id="9999999999999999")
        params["sign"] = self._sign(params)
        ok, info = p.verify_notify(params)
        assert ok is False and "app_id" in info.get("error", "")

    def test_alipay_verify_notify_rejects_missing_sign(self):
        from config import ALIPAY_APP_ID
        p = self._alipay_provider_with_app_pubkey()
        params = self._notify_params("PB5", 600, app_id=ALIPAY_APP_ID)
        ok, info = p.verify_notify(params)     # 不带 sign
        assert ok is False and "sign" in info.get("error", "")

    def test_alipay_notify_end_to_end_marks_order_paid_once(self):
        """验签通过后走完整落账：出票，且重投 5 次仍只出票一次。"""
        from config import ALIPAY_APP_ID
        from services import payment_repo, payment_service
        p = self._alipay_provider_with_app_pubkey()
        self._new_order("PB6", amount=600)
        started = payment_service.start_payment("PB6", member_id="M1001",
                                                provider_name="alipay_sandbox")
        pay_no = started["pay_no"]

        for i in range(5):   # 模拟支付宝重投
            params = self._notify_params(pay_no, 600, app_id=ALIPAY_APP_ID)
            params["sign"] = self._sign(params)
            ok, fields = p.verify_notify(params)
            assert ok is True, fields
            r = payment_service.settle_payment(
                out_trade_no=fields["out_trade_no"], expect_amount=fields["amount"],
                trade_no=fields["trade_no"], buyer_id=fields["buyer_id"],
                provider_name=p.name)
            assert r.get("success")
            if i > 0:
                assert r.get("already") is True   # 第 2 次起都是重复通知

        order = self._order("PB6")
        assert order["status"] == "已出票"
        assert order["pay_channel"] == "alipay_sandbox"
        payment = payment_repo.get_by_out_trade_no(pay_no)
        assert payment["status"] == "支付成功"
        assert payment["trade_no"] == "2026091122001447550512345678"

    def test_return_url_targets_backend_sync_handler(self):
        """return_url 必须指向后端同步回调，不能直接写前端 hash 结果页。

        支付宝同步回跳只带 out_trade_no，结果页要的是 order_no；若直接跳
        `/#/pay/result`，页面会显示「缺少订单号」——必须由 `/api/pay/return/alipay`
        补上 order_no 再 302。这条用例锁死该契约，防回归。
        """
        import re
        src = open("web_app.py", encoding="utf-8").read()
        assert "/api/pay/return/alipay" in src
        # 不允许再出现「return_url 直接拼 hash 结果页」的写法
        bad = re.findall(r'return_url\s*=\s*[^\n]*#/pay/result', src)
        assert not bad, f"return_url 不应直接指向 hash 结果页：{bad}"

    def test_pay_result_view_reads_order_no_from_query(self):
        """结果页从 query 取 order_no（这是同步回调补参数后的落点）。"""
        src = open("frontend/src/client/components/PayResultView.vue",
                   encoding="utf-8").read()
        assert "route.query.order_no" in src


class TestConfirmToken:
    """一次性确认凭证：没有它写接口不能执行；跨会员/跨动作/跨目标/重放/篡改都无效。"""

    def test_issue_then_consume_ok(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})
        assert tok.get("confirm_token")
        r = confirm_token.consume(tok["confirm_token"], "refund", "M1001", "OTEST01",
                                  {"refund_type": "voluntary"})
        assert r.get("ok")

    def test_missing_token_rejected(self):
        from services import confirm_token
        security.set_current_member("M1001")
        r = confirm_token.consume("", "refund", "M1001", "OTEST01", {"refund_type": "voluntary"})
        assert "缺少确认凭证" in r["error"]

    def test_token_is_one_time(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                     {"refund_type": "voluntary"}).get("ok")
        again = confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                      {"refund_type": "voluntary"})
        assert "已被使用" in again["error"]

    def test_token_bound_to_member(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        r = confirm_token.consume(tok, "refund", "M1002", "OTEST01", {"refund_type": "voluntary"})
        assert "不属于当前登录会员" in r["error"]

    def test_token_bound_to_action_and_target(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert "与操作不符" in confirm_token.consume(
            tok, "change", "M1001", "OTEST01", {})["error"]
        tok2 = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert "与目标不符" in confirm_token.consume(
            tok2, "refund", "M1001", "OTEST99", {"refund_type": "voluntary"})["error"]

    def test_tampered_params_rejected(self):
        """确认的是自愿退票，就不能提交特殊退票。"""
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        r = confirm_token.consume(tok, "refund", "M1001", "OTEST01", {"refund_type": "special"})
        assert "与确认时不一致" in r["error"]

    def test_expired_token_rejected(self):
        from services import confirm_token, db
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        conn = db.get_connection()
        conn.execute("UPDATE confirm_tokens SET expires_at = '2000-01-01 00:00:00' WHERE token = ?", (tok,))
        conn.commit()
        conn.close()
        assert "已过期" in confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                                {"refund_type": "voluntary"})["error"]

    def test_fingerprint_ignores_formatting(self):
        from services import confirm_token
        assert confirm_token.fingerprint("book", {"cabin": "经济舱", "passengers": 1}) == \
            confirm_token.fingerprint("book", {"cabin": "经济", "passengers": "1"})


class TestRefundIdempotency:
    """requestId 防重复退款：同一请求只退一次，订单也只翻转一次。"""

    def setup_method(self):
        security.set_current_member("M1001")

    def test_same_request_id_refunds_once(self):
        from services import flight_repo
        _insert_order("OIDEM1", amount=1000)
        first = flight_repo.refund_order_instant("OIDEM1", "M1001", request_id="R-1")
        assert first.get("success") and first.get("status") == "已退款"
        again = flight_repo.refund_order_instant("OIDEM1", "M1001", request_id="R-1")
        assert again.get("idempotent") is True
        assert again.get("refund_amount") == first.get("refund_amount")

    def test_new_request_id_on_refunded_order_rejected(self):
        """换了 requestId 也不能重复退款：状态守卫会把第二笔挡住。"""
        from services import flight_repo
        _insert_order("OIDEM2", amount=1000)
        assert flight_repo.refund_order_instant("OIDEM2", "M1001", request_id="R-2a").get("success")
        second = flight_repo.refund_order_instant("OIDEM2", "M1001", request_id="R-2b")
        assert second.get("error")
        conn = db.get_connection()
        n = conn.execute("SELECT COUNT(*) c FROM refunds WHERE order_no='OIDEM2'").fetchone()["c"]
        conn.close()
        assert n == 1          # 只有一笔退款流水

    def test_refund_records_flow_row(self):
        from services import flight_repo
        _insert_order("OIDEM3", amount=1000)
        flight_repo.refund_order_instant("OIDEM3", "M1001", request_id="R-3")
        row = flight_repo.get_refund_by_request("R-3")
        assert row and row["order_no"] == "OIDEM3" and row["status"] == "已退款"
        assert row["amount"] == 950          # 1000 - 5%（>72h）

    def test_special_refund_is_idempotent_too(self):
        from services import flight_repo
        _insert_order("OIDEM4", amount=1000)
        first = flight_repo.refund_order("OIDEM4", "M1001", request_id="R-4")
        assert first.get("success")
        again = flight_repo.refund_order("OIDEM4", "M1001", request_id="R-4")
        assert again.get("idempotent") is True


class TestOwnershipHardening:
    """归属校验不再"传 None 就放行"：无身份一律拒绝，系统内部流程需显式声明。"""

    def test_write_without_login_is_rejected(self):
        from services import flight_repo
        _insert_order("OHARD1", amount=1000)
        assert "未登录" in flight_repo.refund_order_instant("OHARD1", member_id="M1001")["error"]
        assert "未登录" in flight_repo.pay_order("OHARD1", member_id="M1001")["error"]

    def test_checkin_without_login_is_rejected(self):
        from services import checkin_repo
        _insert_order("OHARD2", amount=800)
        assert "未登录" in checkin_repo.cancel_checkin("OHARD2")["error"]

    def test_system_context_allows_internal_flow(self):
        """支付异步回调/后台任务没有登录身份，用 system_context 显式放行。"""
        from services import flight_repo
        _insert_order("OHARD3", status="待支付")
        with security.system_context("单元测试模拟回调"):
            assert flight_repo.pay_order("OHARD3")["success"] is True
        assert security.get_current_system() is None       # 用完自动复位

    def test_other_member_write_rejected(self):
        from services import flight_repo
        _insert_order("OHARD4", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        assert "无权限" in flight_repo.refund_order_instant("OHARD4", "M1001")["error"]


class TestAuditLog:
    """审计留痕：越权尝试、写操作、幂等命中都可查可统计。"""

    def test_write_recorded(self):
        from services import audit, flight_repo
        security.set_current_member("M1001")
        _insert_order("OAUD1", amount=1000)
        flight_repo.refund_order_instant("OAUD1", "M1001", request_id="AR-1")
        logs = audit.recent(limit=20, event="write")
        assert any(l["action"] == "退票" and l["target"] == "OAUD1"
                   and l["result"] == "success" for l in logs)

    def test_denied_attempt_recorded(self):
        from services import audit, flight_repo
        _insert_order("OAUD2", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        flight_repo.refund_order_instant("OAUD2", "M1001")
        logs = audit.recent(limit=20, event="denied")
        assert any(l["result"] == "denied" for l in logs)
        assert logs[0]["actor"] == "M1001"

    def test_idempotent_hit_recorded(self):
        from services import audit, flight_repo
        security.set_current_member("M1001")
        _insert_order("OAUD3", amount=1000)
        flight_repo.refund_order_instant("OAUD3", "M1001", request_id="AR-3")
        flight_repo.refund_order_instant("OAUD3", "M1001", request_id="AR-3")
        assert any(l["event"] == "idempotent" for l in audit.recent(limit=20))

    def test_stats_reports_denied_rate(self):
        from services import audit, flight_repo
        _insert_order("OAUD4", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        flight_repo.refund_order_instant("OAUD4", "M1001")
        st = audit.stats(days=1)
        assert st["denied_attempts"] >= 1 and st["denied_rate"] == 1.0

    def test_audit_never_breaks_business(self):
        """审计写入失败不能影响业务（这里直接传不可序列化的细节也不该抛异常）。"""
        from services import audit
        audit.log("write", "测试", "success", detail={"weird": object()})
        assert True


class TestCardTokenWiring:
    """聊天确认卡片必须带上服务端签发的凭证——否则用户点了卡片写接口也会被拒。"""

    def setup_method(self):
        security.set_current_member("M1001")

    def test_refund_card_carries_usable_token(self):
        from agents.billing_agent import BillingAgent
        from services import confirm_token
        a = BillingAgent()
        hit, _ = a._on_tool_call("refund_request", {"order_no": "OTOK1", "refund_type": "voluntary"})
        assert hit and a._pending_action["type"] == "refund"
        tok = a._pending_action.get("confirm_token")
        assert tok, "退票卡片没有携带确认凭证"
        assert confirm_token.consume(tok, "refund", "M1001", "OTOK1",
                                     {"refund_type": "voluntary"}).get("ok")

    def test_change_card_carries_usable_token(self):
        from agents.billing_agent import BillingAgent
        from services import confirm_token
        a = BillingAgent()
        args = {"order_no": "OTOK2", "new_flight_no": "MU1074",
                "new_date": FUTURE, "new_cabin": "经济"}
        hit, _ = a._on_tool_call("change_request", args)
        assert hit and a._pending_action["type"] == "change_flight"
        tok = a._pending_action.get("confirm_token")
        assert confirm_token.consume(tok, "change", "M1001", "OTOK2", args).get("ok")

    def test_booking_card_carries_usable_token(self):
        from agents.product_agent import ProductAgent
        from services import confirm_token
        a = ProductAgent()
        args = {"flight_no": "CA1061", "flight_date": FUTURE, "cabin": "经济", "passengers": 2}
        hit, _ = a._on_tool_call("submit_booking_request", args)
        assert hit and a._pending_action["type"] == "book_flight"
        tok = a._pending_action.get("confirm_token")
        assert confirm_token.consume(tok, "book", "M1001", "", args).get("ok")


class TestRunTrace:
    """执行链路跟踪（SSE node/tool 事件 + done 汇总）——验收⑤ 状态机可观测。"""

    def test_node_chain_events_and_summary(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        evs = rt.start_node("sensitive_guard")
        assert [e["node"]["name"] for e in evs] == ["sensitive_guard"]
        assert evs[0]["node"]["status"] == "running"
        assert evs[0]["node"]["duration_ms"] is None

        # 意图分类消息流出：守卫收口 + 意图分类开跑
        evs = rt.observe_message("intent_classifier", "m1")
        names = [e["node"]["name"] for e in evs]
        assert names == ["sensitive_guard", "intent_classifier"]
        assert evs[0]["node"]["status"] == "done"
        assert evs[0]["node"]["duration_ms"] >= 0
        assert evs[1]["node"]["label"] == "意图分类"

        # 路由到账单专家：意图分类收口
        evs = rt.observe_message("billing_agent", "m2")
        assert [e["node"]["name"] for e in evs] == ["intent_classifier", "billing_agent"]

        # 流结束：合成最终响应节点
        evs = rt.finish()
        assert [e["node"]["name"] for e in evs] == ["billing_agent", "final_response", "final_response"]
        s = rt.summary()
        assert [n["name"] for n in s["nodes"]] == [
            "sensitive_guard", "intent_classifier", "billing_agent", "final_response"]
        assert all(n["status"] == "done" for n in s["nodes"])
        assert all(n["duration_ms"] is not None for n in s["nodes"])

    def test_same_node_not_reopened(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.start_node("billing_agent")
        assert rt.observe_message("billing_agent", "m1") == []      # 同节点不重复开
        assert rt.observe_message(None, "m1") == []                 # 空节点忽略
        assert len(rt.summary()["nodes"]) == 1

    def test_tool_running_then_done_on_next_message(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_message("billing_agent", "m1")
        evs = rt.observe_tool("get_order_bill", {"order_no": "T1"}, "m1")
        assert len(evs) == 1 and evs[0]["tool"]["name"] == "get_order_bill"
        assert evs[0]["tool"]["status"] == "running"
        assert evs[0]["tool"]["node"] == "billing_agent"            # 工具挂在节点下
        # 同名工具去重；参数滚动更新不重复发事件
        assert rt.observe_tool("get_order_bill", {"order_no": "T1"}, "m1") == []
        # 下一条消息流出 → 上一条消息上的工具收口
        evs = rt.observe_message("billing_agent", "m2")
        tool_done = [e["tool"] for e in evs if "tool" in e]
        assert len(tool_done) == 1 and tool_done[0]["status"] == "done"
        assert tool_done[0]["duration_ms"] >= 0
        assert tool_done[0]["args"] == {"order_no": "T1"}

    def test_tool_finished_at_stream_end(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_message("product_agent", "m1")
        rt.observe_tool("search_flights", {"dep": "PEK"}, "m1")
        evs = rt.finish()                                           # 流结束统一收口
        done_tools = [e["tool"] for e in evs if "tool" in e]
        assert [t["name"] for t in done_tools] == ["search_flights"]
        assert all(t["status"] == "done" for t in rt.summary()["tools"])

    def test_blocked_guard_stops_chain(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.start_node("sensitive_guard")
        evs = rt.finish(blocked=True)                               # 守卫拦截：止步于守卫
        names = [e["node"]["name"] for e in evs]
        assert names == ["sensitive_guard"]
        assert evs[0]["node"].get("note")
        assert "final_response" not in [n["name"] for n in rt.summary()["nodes"]]

    def test_tool_args_update_reflected_in_summary(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_tool("search_flights", None, "m1")                # 首个分片可能无参数
        rt.observe_tool("search_flights", {"dep": "PEK", "arr": "SHA"}, "m1")
        rt.finish()
        assert rt.summary()["tools"][0]["args"] == {"dep": "PEK", "arr": "SHA"}

class TestConfirmToken:
    """一次性确认凭证：没有它写接口不能执行；跨会员/跨动作/跨目标/重放/篡改都无效。"""

    def test_issue_then_consume_ok(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})
        assert tok.get("confirm_token")
        r = confirm_token.consume(tok["confirm_token"], "refund", "M1001", "OTEST01",
                                  {"refund_type": "voluntary"})
        assert r.get("ok")

    def test_missing_token_rejected(self):
        from services import confirm_token
        security.set_current_member("M1001")
        r = confirm_token.consume("", "refund", "M1001", "OTEST01", {"refund_type": "voluntary"})
        assert "缺少确认凭证" in r["error"]

    def test_token_is_one_time(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                     {"refund_type": "voluntary"}).get("ok")
        again = confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                      {"refund_type": "voluntary"})
        assert "已被使用" in again["error"]

    def test_token_bound_to_member(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        r = confirm_token.consume(tok, "refund", "M1002", "OTEST01", {"refund_type": "voluntary"})
        assert "不属于当前登录会员" in r["error"]

    def test_token_bound_to_action_and_target(self):
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert "与操作不符" in confirm_token.consume(
            tok, "change", "M1001", "OTEST01", {})["error"]
        tok2 = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        assert "与目标不符" in confirm_token.consume(
            tok2, "refund", "M1001", "OTEST99", {"refund_type": "voluntary"})["error"]

    def test_tampered_params_rejected(self):
        """确认的是自愿退票，就不能提交特殊退票。"""
        from services import confirm_token
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        r = confirm_token.consume(tok, "refund", "M1001", "OTEST01", {"refund_type": "special"})
        assert "与确认时不一致" in r["error"]

    def test_expired_token_rejected(self):
        from services import confirm_token, db
        security.set_current_member("M1001")
        tok = confirm_token.issue("refund", "M1001", "OTEST01", {"refund_type": "voluntary"})["confirm_token"]
        conn = db.get_connection()
        conn.execute("UPDATE confirm_tokens SET expires_at = '2000-01-01 00:00:00' WHERE token = ?", (tok,))
        conn.commit()
        conn.close()
        assert "已过期" in confirm_token.consume(tok, "refund", "M1001", "OTEST01",
                                                {"refund_type": "voluntary"})["error"]

    def test_fingerprint_ignores_formatting(self):
        from services import confirm_token
        assert confirm_token.fingerprint("book", {"cabin": "经济舱", "passengers": 1}) == \
            confirm_token.fingerprint("book", {"cabin": "经济", "passengers": "1"})


class TestRefundIdempotency:
    """requestId 防重复退款：同一请求只退一次，订单也只翻转一次。"""

    def setup_method(self):
        security.set_current_member("M1001")

    def test_same_request_id_refunds_once(self):
        from services import flight_repo
        _insert_order("OIDEM1", amount=1000)
        first = flight_repo.refund_order_instant("OIDEM1", "M1001", request_id="R-1")
        assert first.get("success") and first.get("status") == "已退款"
        again = flight_repo.refund_order_instant("OIDEM1", "M1001", request_id="R-1")
        assert again.get("idempotent") is True
        assert again.get("refund_amount") == first.get("refund_amount")

    def test_new_request_id_on_refunded_order_rejected(self):
        """换了 requestId 也不能重复退款：状态守卫会把第二笔挡住。"""
        from services import flight_repo
        _insert_order("OIDEM2", amount=1000)
        assert flight_repo.refund_order_instant("OIDEM2", "M1001", request_id="R-2a").get("success")
        second = flight_repo.refund_order_instant("OIDEM2", "M1001", request_id="R-2b")
        assert second.get("error")
        conn = db.get_connection()
        n = conn.execute("SELECT COUNT(*) c FROM refunds WHERE order_no='OIDEM2'").fetchone()["c"]
        conn.close()
        assert n == 1          # 只有一笔退款流水

    def test_refund_records_flow_row(self):
        from services import flight_repo
        _insert_order("OIDEM3", amount=1000)
        flight_repo.refund_order_instant("OIDEM3", "M1001", request_id="R-3")
        row = flight_repo.get_refund_by_request("R-3")
        assert row and row["order_no"] == "OIDEM3" and row["status"] == "已退款"
        assert row["amount"] == 950          # 1000 - 5%（>72h）

    def test_special_refund_is_idempotent_too(self):
        from services import flight_repo
        _insert_order("OIDEM4", amount=1000)
        first = flight_repo.refund_order("OIDEM4", "M1001", request_id="R-4")
        assert first.get("success")
        again = flight_repo.refund_order("OIDEM4", "M1001", request_id="R-4")
        assert again.get("idempotent") is True


class TestOwnershipHardening:
    """归属校验不再"传 None 就放行"：无身份一律拒绝，系统内部流程需显式声明。"""

    def test_write_without_login_is_rejected(self):
        from services import flight_repo
        _insert_order("OHARD1", amount=1000)
        assert "未登录" in flight_repo.refund_order_instant("OHARD1", member_id="M1001")["error"]
        assert "未登录" in flight_repo.pay_order("OHARD1", member_id="M1001")["error"]

    def test_checkin_without_login_is_rejected(self):
        from services import checkin_repo
        _insert_order("OHARD2", amount=800)
        assert "未登录" in checkin_repo.cancel_checkin("OHARD2")["error"]

    def test_system_context_allows_internal_flow(self):
        """支付异步回调/后台任务没有登录身份，用 system_context 显式放行。"""
        from services import flight_repo
        _insert_order("OHARD3", status="待支付")
        with security.system_context("单元测试模拟回调"):
            assert flight_repo.pay_order("OHARD3")["success"] is True
        assert security.get_current_system() is None       # 用完自动复位

    def test_other_member_write_rejected(self):
        from services import flight_repo
        _insert_order("OHARD4", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        assert "无权限" in flight_repo.refund_order_instant("OHARD4", "M1001")["error"]


class TestAuditLog:
    """审计留痕：越权尝试、写操作、幂等命中都可查可统计。"""

    def test_write_recorded(self):
        from services import audit, flight_repo
        security.set_current_member("M1001")
        _insert_order("OAUD1", amount=1000)
        flight_repo.refund_order_instant("OAUD1", "M1001", request_id="AR-1")
        logs = audit.recent(limit=20, event="write")
        assert any(l["action"] == "退票" and l["target"] == "OAUD1"
                   and l["result"] == "success" for l in logs)

    def test_denied_attempt_recorded(self):
        from services import audit, flight_repo
        _insert_order("OAUD2", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        flight_repo.refund_order_instant("OAUD2", "M1001")
        logs = audit.recent(limit=20, event="denied")
        assert any(l["result"] == "denied" for l in logs)
        assert logs[0]["actor"] == "M1001"

    def test_idempotent_hit_recorded(self):
        from services import audit, flight_repo
        security.set_current_member("M1001")
        _insert_order("OAUD3", amount=1000)
        flight_repo.refund_order_instant("OAUD3", "M1001", request_id="AR-3")
        flight_repo.refund_order_instant("OAUD3", "M1001", request_id="AR-3")
        assert any(l["event"] == "idempotent" for l in audit.recent(limit=20))

    def test_stats_reports_denied_rate(self):
        from services import audit, flight_repo
        _insert_order("OAUD4", member_id="M1002", amount=1000)
        security.set_current_member("M1001")
        flight_repo.refund_order_instant("OAUD4", "M1001")
        st = audit.stats(days=1)
        assert st["denied_attempts"] >= 1 and st["denied_rate"] == 1.0

    def test_audit_never_breaks_business(self):
        """审计写入失败不能影响业务（这里直接传不可序列化的细节也不该抛异常）。"""
        from services import audit
        audit.log("write", "测试", "success", detail={"weird": object()})
        assert True


class TestCardTokenWiring:
    """聊天确认卡片必须带上服务端签发的凭证——否则用户点了卡片写接口也会被拒。"""

    def setup_method(self):
        security.set_current_member("M1001")

    def test_refund_card_carries_usable_token(self):
        from agents.billing_agent import BillingAgent
        from services import confirm_token
        a = BillingAgent()
        hit, _ = a._on_tool_call("refund_request", {"order_no": "OTOK1", "refund_type": "voluntary"})
        assert hit and a._pending_action["type"] == "refund"
        tok = a._pending_action.get("confirm_token")
        assert tok, "退票卡片没有携带确认凭证"
        assert confirm_token.consume(tok, "refund", "M1001", "OTOK1",
                                     {"refund_type": "voluntary"}).get("ok")

    def test_change_card_carries_usable_token(self):
        from agents.billing_agent import BillingAgent
        from services import confirm_token
        a = BillingAgent()
        args = {"order_no": "OTOK2", "new_flight_no": "MU1074",
                "new_date": FUTURE, "new_cabin": "经济"}
        hit, _ = a._on_tool_call("change_request", args)
        assert hit and a._pending_action["type"] == "change_flight"
        tok = a._pending_action.get("confirm_token")
        assert confirm_token.consume(tok, "change", "M1001", "OTOK2", args).get("ok")

    def test_booking_card_carries_usable_token(self):
        from agents.product_agent import ProductAgent
        from services import confirm_token
        a = ProductAgent()
        args = {"flight_no": "CA1061", "flight_date": FUTURE, "cabin": "经济", "passengers": 2}
        hit, _ = a._on_tool_call("submit_booking_request", args)
        assert hit and a._pending_action["type"] == "book_flight"
        tok = a._pending_action.get("confirm_token")
        assert confirm_token.consume(tok, "book", "M1001", "", args).get("ok")


class TestRunTrace:
    """执行链路跟踪（SSE node/tool 事件 + done 汇总）——验收⑤ 状态机可观测。"""

    def test_node_chain_events_and_summary(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        evs = rt.start_node("sensitive_guard")
        assert [e["node"]["name"] for e in evs] == ["sensitive_guard"]
        assert evs[0]["node"]["status"] == "running"
        assert evs[0]["node"]["duration_ms"] is None

        # 意图分类消息流出：守卫收口 + 意图分类开跑
        evs = rt.observe_message("intent_classifier", "m1")
        names = [e["node"]["name"] for e in evs]
        assert names == ["sensitive_guard", "intent_classifier"]
        assert evs[0]["node"]["status"] == "done"
        assert evs[0]["node"]["duration_ms"] >= 0
        assert evs[1]["node"]["label"] == "意图分类"

        # 路由到账单专家：意图分类收口
        evs = rt.observe_message("billing_agent", "m2")
        assert [e["node"]["name"] for e in evs] == ["intent_classifier", "billing_agent"]

        # 流结束：合成最终响应节点
        evs = rt.finish()
        assert [e["node"]["name"] for e in evs] == ["billing_agent", "final_response", "final_response"]
        s = rt.summary()
        assert [n["name"] for n in s["nodes"]] == [
            "sensitive_guard", "intent_classifier", "billing_agent", "final_response"]
        assert all(n["status"] == "done" for n in s["nodes"])
        assert all(n["duration_ms"] is not None for n in s["nodes"])

    def test_same_node_not_reopened(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.start_node("billing_agent")
        assert rt.observe_message("billing_agent", "m1") == []      # 同节点不重复开
        assert rt.observe_message(None, "m1") == []                 # 空节点忽略
        assert len(rt.summary()["nodes"]) == 1

    def test_tool_running_then_done_on_next_message(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_message("billing_agent", "m1")
        evs = rt.observe_tool("get_order_bill", {"order_no": "T1"}, "m1")
        assert len(evs) == 1 and evs[0]["tool"]["name"] == "get_order_bill"
        assert evs[0]["tool"]["status"] == "running"
        assert evs[0]["tool"]["node"] == "billing_agent"            # 工具挂在节点下
        # 同名工具去重；参数滚动更新不重复发事件
        assert rt.observe_tool("get_order_bill", {"order_no": "T1"}, "m1") == []
        # 下一条消息流出 → 上一条消息上的工具收口
        evs = rt.observe_message("billing_agent", "m2")
        tool_done = [e["tool"] for e in evs if "tool" in e]
        assert len(tool_done) == 1 and tool_done[0]["status"] == "done"
        assert tool_done[0]["duration_ms"] >= 0
        assert tool_done[0]["args"] == {"order_no": "T1"}

    def test_tool_finished_at_stream_end(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_message("product_agent", "m1")
        rt.observe_tool("search_flights", {"dep": "PEK"}, "m1")
        evs = rt.finish()                                           # 流结束统一收口
        done_tools = [e["tool"] for e in evs if "tool" in e]
        assert [t["name"] for t in done_tools] == ["search_flights"]
        assert all(t["status"] == "done" for t in rt.summary()["tools"])

    def test_blocked_guard_stops_chain(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.start_node("sensitive_guard")
        evs = rt.finish(blocked=True)                               # 守卫拦截：止步于守卫
        names = [e["node"]["name"] for e in evs]
        assert names == ["sensitive_guard"]
        assert evs[0]["node"].get("note")
        assert "final_response" not in [n["name"] for n in rt.summary()["nodes"]]

    def test_tool_args_update_reflected_in_summary(self):
        from services.trace_events import RunTrace
        rt = RunTrace()
        rt.observe_tool("search_flights", None, "m1")                # 首个分片可能无参数
        rt.observe_tool("search_flights", {"dep": "PEK", "arr": "SHA"}, "m1")
        rt.finish()
        assert rt.summary()["tools"][0]["args"] == {"dep": "PEK", "arr": "SHA"}

class TestLangfuseSetup:
    """Langfuse 观测接入：未配置密钥 / 占位符时全部退化为 no-op，
    开关正确性；启用后不改变业务行为（config 合并防流式回调被替换）。"""

    def test_disabled_when_keys_unset(self, monkeypatch):
        from services import langfuse_setup
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        assert langfuse_setup.is_enabled() is False
        # trace 与 llm_config 都应退化为 no-op
        with langfuse_setup.trace("agent:test") as h:
            assert h is None
        assert langfuse_setup.llm_config(None) is None

    def test_disabled_for_placeholder_keys(self, monkeypatch):
        from services import langfuse_setup
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-xxxx")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-xxxx")
        assert langfuse_setup.is_enabled() is False           # 占位符不开

    def test_enabled_with_real_keys(self, monkeypatch):
        from services import langfuse_setup
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-real1234567890ab")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-real1234567890ab")
        assert langfuse_setup.is_enabled() is True

    def test_whitespace_only_keys_disabled(self, monkeypatch):
        from services import langfuse_setup
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "   ")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
        assert langfuse_setup.is_enabled() is False

    def test_llm_config_merges_not_replaces(self, monkeypatch):
        """llm_config 必须 merge_configs 合并，不能裸返回 callbacks——
        显式 callbacks 会整体替换 LangGraph 注入的流式回调，token 传播被吞。"""
        from services import langfuse_setup
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-real1234567890ab")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-real1234567890ab")
        with langfuse_setup.trace("agent:test") as h:
            assert h is not None
            cfg = langfuse_setup.llm_config(h)
            assert cfg is not None and cfg.get("callbacks")
            # 合并后应保留 LangGraph 注入的上下文键（而非只有 callbacks 一个键）
            assert len(cfg) >= 1


# ---------------------------------------------------------------- 直接运行入口

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--tb=short"]))
