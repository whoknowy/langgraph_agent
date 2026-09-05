#!/usr/bin/env python3
"""
多智能体客服系统 - Web 入口
基于 LangGraph API 接口，路由与 Flask 会话；业务逻辑见 chat_web_service.py
"""

import os
import time
from typing import Dict, Any, List

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, render_template, request, jsonify, session, Response

from chat_web_service import (
    run_chat_sync,
    stream_chat_tokens,
    fetch_sessions_list,
    fetch_session_detail,
    delete_remote_thread,
    clear_thread_and_create_new,
    langgraph_connectivity_test,
    get_current_thread_id,
)

# 导入配置（与历史行为保持一致）
from config import *  # noqa: E402,F401,F403

app = Flask(__name__)

# Flask 配置
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key-here")
app.config['SESSION_TYPE'] = 'filesystem'

# 启动即建表/迁移（幂等）：新表（如 notifications）在已有库上也要生效
from services import db as _db  # noqa: E402
_c = _db.get_connection()
_db.init_schema(_c)
_c.close()

# 订单生命周期：起飞自动「已使用」+ 待支付超时自动「已取消」（后台线程，与客户端/管理端共享 DB）
from services.lifecycle import start_lifecycle_worker  # noqa: E402
start_lifecycle_worker()


# --- 工具层硬安全：请求期间绑定受信上下文（会员=归属校验 / 管理员=豁免跨会员） ---

@app.before_request
def _bind_security_context():
    from flask import g
    from services import security
    if request.path.startswith('/admin'):
        admin = session.get('admin')
        if admin:
            g._admin_security_token = security.set_current_admin(admin.get('username'))
    else:
        member = session.get('member')
        if member:
            g._member_security_token = security.set_current_member(member.get('member_id'))


@app.teardown_request
def _unbind_security_context(exc):
    from flask import g
    from services import security
    token = g.pop('_member_security_token', None)
    if token is not None:
        security.reset_current_member(token)
    token = g.pop('_admin_security_token', None)
    if token is not None:
        security.reset_current_admin(token)


# --- Flask session 内的本地对话占位（主页模板可能使用）---

def _current_member() -> Dict[str, Any]:
    """当前登录会员（未登录返回空 dict）。"""
    return session.get('member') or {}


def _require_member():
    """聊天等接口的登录门禁：未登录返回 (None, 401响应)。"""
    member = _current_member()
    if not member:
        return None, (jsonify({'error': '未登录，请先登录会员账号'}), 401)
    return member, None


def _local_chat_response(user_message: str, session_id: str):
    """LangGraph 服务未启动时，回退到本地多智能体流程。"""
    try:
        from multi_agent_customer_service import process_customer_query
        member = _current_member()
        result = process_customer_query(user_message, session_id,
                                        customer_info={"member_id": member.get("member_id")})
        return jsonify({
            'response': result.get('response', ''),
            'session_id': session_id,
            'thread_id': session_id,
            'local_fallback': True
        })
    except Exception as e:
        print(f"❌ 本地客服处理失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'本地客服处理失败: {str(e)}'}), 500


@app.route('/')
def index():
    """主页（对话历史由 LangGraph 线程状态经 /api/sessions 系列接口提供）"""
    return render_template('index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    """处理聊天请求"""
    try:
        member, denied = _require_member()
        if denied:
            return denied

        data = request.get_json()
        user_message = (data.get('message') or '').strip()
        client_session_id = data.get('session_id', 'default')

        ai_text, err_msg, http_code = run_chat_sync(user_message, client_session_id,
                                                    member_id=member.get('member_id'))
        if err_msg in ('无法创建或找到助手', '无法创建线程'):
            return _local_chat_response(user_message, client_session_id)
        if err_msg:
            return jsonify({'error': err_msg}), http_code or 500

        tid = get_current_thread_id()
        return jsonify({
            'response': ai_text,
            'session_id': tid,
            'thread_id': tid,
        })
    except Exception as e:
        print(f"❌ 聊天处理错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'内部错误: {str(e)}'}), 500


@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    """处理流式聊天请求"""
    try:
        member, denied = _require_member()
        if denied:
            return denied

        data = request.get_json()
        user_message = (data.get('message') or '').strip()
        client_session_id = data.get('session_id', 'default')

        return Response(
            stream_chat_tokens(user_message, client_session_id,
                               member_id=member.get('member_id')),
            mimetype='text/event-stream'
        )

    except Exception as e:
        print(f"❌ 流式聊天处理错误: {e}")
        return jsonify({'error': f'内部错误: {str(e)}'}), 500


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """获取会话列表"""
    sessions, err = fetch_sessions_list()
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'sessions': sessions or []})


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """获取特定会话详情"""
    session_data, err = fetch_session_detail(session_id)
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'session': session_data})


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """删除会话"""
    try:
        ok, status = delete_remote_thread(session_id)
        if ok:
            return jsonify({'message': '会话删除成功'})
        return jsonify({'error': f'删除会话失败: {status}'}), 500
    except Exception as e:
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500


@app.route('/api/sessions/<session_id>/clear', methods=['POST'])
def clear_session(session_id):
    """清空会话"""
    try:
        new_thread_id, err = clear_thread_and_create_new(session_id)
        if err:
            return jsonify({'error': err}), 500

        return jsonify({
            'message': '会话清空成功',
            'new_thread_id': new_thread_id
        })
    except Exception as e:
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500


@app.route('/api/new_session', methods=['POST'])
def create_new_session():
    """创建新会话（Flask session 侧）"""
    try:
        import uuid
        new_session_id = str(uuid.uuid4())
        session['current_session_id'] = new_session_id
        return jsonify({
            'session_id': new_session_id,
            'message': '新会话创建成功'
        })
    except Exception as e:
        return jsonify({'error': f'创建会话失败: {str(e)}'}), 500


# --- 会员登录与身份 ---

@app.route('/api/login', methods=['POST'])
def login():
    """会员登录：member_id + 手机号后4位（演示级身份校验）。"""
    try:
        data = request.get_json() or {}
        from services import flight_repo

        # 模式一：密码登录（注册会员，账号=会员号或手机号）
        password = (data.get('password') or '')
        if password:
            account = (data.get('account') or data.get('member_id') or data.get('phone') or '').strip()
            cust = flight_repo.verify_member_password(account, password)
            if cust.get('error'):
                return jsonify({'error': cust['error']}), 401
            session['member'] = {'member_id': cust['member_id'], 'name': cust['name'], 'level': cust['level']}
            return jsonify({'member': session['member'], 'message': f"欢迎回来，{cust['name']}"})

        # 模式二：演示账号尾号登录（会员号 + 手机号后4位）
        member_id = (data.get('member_id') or '').strip().upper()
        phone_suffix = (data.get('phone_suffix') or '').strip()
        if not member_id or not phone_suffix:
            return jsonify({'error': '请输入会员号和手机号后4位（注册会员可用密码登录）'}), 400

        cust = flight_repo.get_customer(member_id)
        if cust.get('error'):
            return jsonify({'error': '会员号不存在，请核对后重试'}), 401
        if str(cust.get('phone', ''))[-4:] != phone_suffix:
            return jsonify({'error': '手机号后4位不正确'}), 401

        session['member'] = {
            'member_id': cust['member_id'],
            'name': cust['name'],
            'level': cust['level'],
        }
        return jsonify({'member': session['member'], 'message': f"欢迎回来，{cust['name']}"})
    except Exception as e:
        return jsonify({'error': f'登录失败: {str(e)}'}), 500


@app.route('/api/register', methods=['POST'])
def register():
    """会员注册：手机号唯一，口令哈希存储，成功后自动登录。"""
    try:
        data = request.get_json() or {}
        from services import flight_repo
        result = flight_repo.register_member(
            data.get('name') or '', data.get('phone') or '',
            data.get('password') or '', data.get('email') or '')
        if result.get('error'):
            return jsonify({'error': result['error']}), 400
        session['member'] = {'member_id': result['member_id'], 'name': result['name'], 'level': result['level']}
        return jsonify({'member': session['member'],
                        'message': f"注册成功，{result['name']}！你的会员号是 {result['member_id']}"})
    except Exception as e:
        return jsonify({'error': f'注册失败: {str(e)}'}), 500


@app.route('/api/logout', methods=['POST'])
def logout():
    """退出登录"""
    session.pop('member', None)
    return jsonify({'message': '已退出登录'})


@app.route('/api/me')
def me():
    """当前登录会员"""
    member = _current_member()
    if not member:
        return jsonify({'member': None}), 401
    return jsonify({'member': member})


@app.route('/api/demo_accounts')
def demo_accounts():
    """演示账号（登录页一键填入）。"""
    try:
        from services import flight_repo
        return jsonify({'accounts': flight_repo.list_demo_accounts(3)})
    except Exception as e:
        return jsonify({'error': f'获取演示账号失败: {str(e)}'}), 500


# --- 订票 / 支付 / 退票（REST，真正的写库动作） ---

@app.route('/api/booking_quote')
def booking_quote():
    """订票报价（确认卡片展示用）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        result = flight_repo.booking_quote(
            request.args.get('flight_no', ''),
            request.args.get('flight_date', ''),
            request.args.get('cabin', ''),
            request.args.get('passengers', 1),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'报价失败: {str(e)}'}), 500


@app.route('/api/book', methods=['POST'])
def book():
    """创建订单（待支付）。member_id 以登录身份为准。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import flight_repo
        result = flight_repo.book_flight(
            member_id=member['member_id'],
            flight_no=data.get('flight_no', ''),
            flight_date=data.get('flight_date', ''),
            cabin=data.get('cabin', ''),
            passengers=data.get('passengers', 1),
        )
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'下单失败: {str(e)}'}), 500


@app.route('/api/pay', methods=['POST'])
def pay():
    """支付订单：待支付 → 已出票。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import flight_repo
        result = flight_repo.pay_order(data.get('order_no', ''), member_id=member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'支付失败: {str(e)}'}), 500


@app.route('/api/change_quote')
def change_quote():
    """改签报价（确认卡片展示：新航班信息/差价）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        result = flight_repo.change_quote(
            request.args.get('order_no', ''), member['member_id'],
            request.args.get('new_flight_no', ''), request.args.get('new_date', ''),
            request.args.get('new_cabin', ''))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'改签报价失败: {str(e)}'}), 500


@app.route('/api/change', methods=['POST'])
def change():
    """执行改签（免改签费，差价多退少补；代码层校验，LLM 只能发起卡片）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import flight_repo
        result = flight_repo.change_order(
            data.get('order_no', ''), member['member_id'], data.get('new_flight_no', ''),
            data.get('new_date', ''), data.get('new_cabin', ''))
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'改签失败: {str(e)}'}), 500


@app.route('/api/refund_quote')
def refund_quote():
    """自愿退票报价（确认卡片展示：手续费/预计到账）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        result = flight_repo.refund_quote(request.args.get('order_no', ''),
                                          member_id=member['member_id'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'报价失败: {str(e)}'}), 500


@app.route('/api/refund', methods=['POST'])
def refund():
    """退票：voluntary=自愿（规则费率即时退款）；special=非自愿特殊通道（退票中，管理端审批）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import flight_repo
        refund_type = (data.get('refund_type') or 'voluntary').strip()
        if refund_type == 'special':
            result = flight_repo.refund_order(data.get('order_no', ''), member_id=member['member_id'])
            if result.get('error'):
                return jsonify(result), 400
            result['message'] = result.get('message', '') + '（特殊退票已受理，人工审核中）'
            return jsonify(result)
        result = flight_repo.refund_order_instant(data.get('order_no', ''), member_id=member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'退票失败: {str(e)}'}), 500


# --- 我的数据面板（直查库，不过 LLM） ---

@app.route('/api/my/orders')
def my_orders():
    """当前会员的订单列表。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        result = flight_repo.get_order_bill(member_id=member['member_id'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'查询订单失败: {str(e)}'}), 500


@app.route('/api/my/complaints')
def my_complaints():
    """当前会员的投诉列表。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        result = flight_repo.query_complaints(member_id=member['member_id'])
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'查询投诉失败: {str(e)}'}), 500


@app.route('/api/my/notifications')
def my_notifications():
    """当前会员的站内通知列表。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import notification_repo
        unread_only = request.args.get('unread') in ('1', 'true')
        items = notification_repo.list_notifications(member['member_id'], unread_only=unread_only)
        return jsonify({'notifications': items,
                        'unread_count': notification_repo.unread_count(member['member_id'])})
    except Exception as e:
        return jsonify({'error': f'查询通知失败: {str(e)}'}), 500


@app.route('/api/my/notifications/read', methods=['POST'])
def my_notifications_read():
    """当前会员的全部通知置为已读。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import notification_repo
        n = notification_repo.mark_all_read(member['member_id'])
        return jsonify({'success': True, 'marked': n})
    except Exception as e:
        return jsonify({'error': f'标记已读失败: {str(e)}'}), 500


# ============================================================
# 管理员运营平台（/admin）
# ============================================================

def _current_admin() -> Dict[str, Any]:
    return session.get('admin') or {}


def admin_required():
    """管理端接口门禁。返回 (admin, denied)。"""
    admin = _current_admin()
    if not admin:
        return None, (jsonify({'error': '未登录管理员账号'}), 401)
    return admin, None


@app.route('/admin')
def admin_index():
    """管理平台页面（前端自行检查登录态并显示登录视图）。"""
    return render_template('admin.html')


@app.route('/admin/api/login', methods=['POST'])
def admin_login():
    try:
        data = request.get_json() or {}
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '')
        if not username or not password:
            return jsonify({'error': '请输入用户名和密码'}), 400

        from werkzeug.security import check_password_hash
        from services import db
        conn = db.get_connection()
        db.init_schema(conn)
        row = conn.execute("SELECT username, password_hash, name FROM admins WHERE username = ?",
                           (username,)).fetchone()
        conn.close()
        if not row or not check_password_hash(row['password_hash'], password):
            return jsonify({'error': '用户名或密码错误'}), 401

        session['admin'] = {'username': row['username'], 'name': row['name']}
        return jsonify({'admin': session['admin']})
    except Exception as e:
        return jsonify({'error': f'登录失败: {str(e)}'}), 500


@app.route('/admin/api/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({'message': '已退出'})


@app.route('/admin/api/me')
def admin_me():
    admin = _current_admin()
    if not admin:
        return jsonify({'admin': None}), 401
    return jsonify({'admin': admin})


@app.route('/admin/api/stats')
def admin_stats():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.get_stats())


# ---- 退款处理 ----

@app.route('/admin/api/refunds')
def admin_refunds():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_refund_queue())


@app.route('/admin/api/refunds/approve', methods=['POST'])
def admin_refund_approve():
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.approve_refund(data.get('order_no', ''),
                                       data.get('refund_amount'), data.get('admin_note', ''))
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/admin/api/refunds/reject', methods=['POST'])
def admin_refund_reject():
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.reject_refund(data.get('order_no', ''), data.get('admin_note', ''))
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


# ---- 投诉处理 ----

@app.route('/admin/api/complaints')
def admin_complaints():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_complaints(request.args.get('status') or None,
                                              request.args.get('q') or None))


@app.route('/admin/api/complaints/resolve', methods=['POST'])
def admin_complaint_resolve():
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.resolve_complaint(data.get('ticket_no', ''), data.get('reply', ''))
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/admin/api/complaints/escalate', methods=['POST'])
def admin_complaint_escalate():
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.escalate_complaint(data.get('ticket_no', ''), data.get('note', ''))
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/admin/api/complaints/reopen', methods=['POST'])
def admin_complaint_reopen():
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.reopen_complaint(data.get('ticket_no', ''))
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


# ---- 航班 / 机场 / 航司 ----

@app.route('/admin/api/flights')
def admin_flights():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_flights(request.args.get('q') or None))


@app.route('/admin/api/flights', methods=['POST'])
def admin_flight_create():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    result = admin_repo.create_flight(request.get_json() or {})
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/admin/api/airports')
def admin_airports():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_airports())


@app.route('/admin/api/airports', methods=['POST'])
def admin_airport_create():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    result = admin_repo.create_airport(request.get_json() or {})
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/admin/api/airlines')
def admin_airlines():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_airlines())


@app.route('/admin/api/airlines', methods=['POST'])
def admin_airline_create():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    result = admin_repo.create_airline(request.get_json() or {})
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)


# ---- 订单全局查询 / 会员（只读） ----

@app.route('/admin/api/orders')
def admin_orders():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_orders(request.args.get('status') or None,
                                          request.args.get('q') or None))


@app.route('/admin/api/customers')
def admin_customers():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.list_customers(request.args.get('q') or None))


@app.route('/api/health')
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'healthy',
        'timestamp': time.time()
    })


@app.route('/api/test')
def test_langgraph():
    """测试 LangGraph API 调用"""
    result, err = langgraph_connectivity_test()
    if err:
        return jsonify({'error': err}), 500
    return jsonify(result)


def main():
    """主函数"""
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true" and os.getenv("LANGSMITH_API_KEY"):
        print("🔭 LangSmith 轨迹观测已启用（项目:", os.getenv("LANGSMITH_PROJECT", "default"), "）")
    else:
        print("🔭 LangSmith 轨迹观测未启用（.env 配置 LANGSMITH_* 后自动上报）")
    print("🚀 多智能体客服系统 Web 应用")
    print("=" * 60)
    print("🌐 启动 Web 服务...")
    print("📱 访问地址: http://localhost:5000")
    print("💡 按 Ctrl+C 停止服务")
    print()
    app.run(host='0.0.0.0', port=5000, debug=True)


if __name__ == "__main__":
    main()
