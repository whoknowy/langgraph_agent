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


# ---------------------------------------------------------------- 直接运行入口

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--tb=short"]))
