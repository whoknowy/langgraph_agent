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
    stream_chat_events,
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


# --- 工具层硬安全：请求期间把登录身份绑定到受信上下文（供仓库层校验归属） ---

@app.before_request
def _bind_member_security_context():
    from flask import g
    from services import security
    member = session.get('member')
    if member:
        g._member_security_token = security.set_current_member(member.get('member_id'))


@app.teardown_request
def _unbind_member_security_context(exc):
    from flask import g
    from services import security
    token = g.pop('_member_security_token', None)
    if token is not None:
        security.reset_current_member(token)


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


def get_conversation_history(session_id: str) -> List[Dict[str, Any]]:
    if 'conversations' not in session:
        session['conversations'] = {}
    return session['conversations'].get(session_id, [])


def add_conversation_message(session_id: str, role: str, content: str) -> None:
    history = get_conversation_history(session_id)
    history.append({
        'role': role,
        'content': content,
    })
    session['conversations'][session_id] = history


@app.route('/')
def index():
    """主页"""
    current_session_id = session.get('current_session_id', 'default')
    conversation_history = get_conversation_history(current_session_id)
    return render_template('index.html', conversation_history=conversation_history)


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
            if 'conversations' in session and session_id in session['conversations']:
                del session['conversations'][session_id]
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

        if 'conversations' in session and session_id in session['conversations']:
            session['conversations'][session_id] = []

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
        if 'conversations' not in session:
            session['conversations'] = {}
        session['conversations'][new_session_id] = []
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
        member_id = (data.get('member_id') or '').strip().upper()
        phone_suffix = (data.get('phone_suffix') or '').strip()
        if not member_id or not phone_suffix:
            return jsonify({'error': '请输入会员号和手机号后4位'}), 400

        from services import flight_repo
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


@app.route('/api/refund', methods=['POST'])
def refund():
    """申请退票：已出票 → 退票中。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import flight_repo
        result = flight_repo.refund_order(data.get('order_no', ''), member_id=member['member_id'])
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
    print("🚀 多智能体客服系统 Web 应用")
    print("=" * 60)
    print("🌐 启动 Web 服务...")
    print("📱 访问地址: http://localhost:5000")
    print("💡 按 Ctrl+C 停止服务")
    print()
    app.run(host='0.0.0.0', port=5000, debug=True)


if __name__ == "__main__":
    main()
