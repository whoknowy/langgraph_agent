#!/usr/bin/env python3
"""
多智能体客服系统 - Web 入口
基于 LangGraph API 接口，路由与 Flask 会话；业务逻辑见 chat_web_service.py
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Any, List

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify, session, Response, send_from_directory, redirect

from chat_web_service import (
    run_chat_sync,
    stream_chat_tokens,
    fetch_sessions_list,
    fetch_session_detail,
    delete_remote_thread,
    clear_thread_and_create_new,
    langgraph_connectivity_test,
)
from services import session_titles

# 导入配置（与历史行为保持一致）
from config import *  # noqa: E402,F401,F403
from services.bootstrap_security import resolve_flask_secret

app = Flask(__name__)
FRONTEND_DIST = Path(__file__).resolve().parent / 'frontend' / 'dist'


def _serve_frontend(html_name: str):
    """提供 Vue 构建产物；未构建时给出构建提示。"""
    target = FRONTEND_DIST / html_name
    if target.exists():
        return send_from_directory(FRONTEND_DIST, html_name)
    return Response(
        "<!DOCTYPE html><html lang=\"zh-CN\"><meta charset=\"UTF-8\">"
        "<title>前端未构建</title><body style=\"font-family:sans-serif;padding:40px\">"
        "<h2>Vue 前端尚未构建</h2>"
        "<p>请先执行：<code>cd frontend && npm install && npm run build</code></p>"
        "</body></html>",
        mimetype='text/html'
    )

# Flask 会话签名密钥：生产环境强制显式强密钥（弱值/缺省拒绝启动）；
# 开发环境缺省或弱值时自动生成并持久化到 data/.flask_secret_key（重启不失效）
app.secret_key = resolve_flask_secret(
    os.getenv("FLASK_SECRET_KEY"),
    IS_PRODUCTION,
    Path(__file__).resolve().parent / "data" / ".flask_secret_key",
)
app.config['SESSION_TYPE'] = 'filesystem'

# 启动即建表/迁移 + 首次种子 + 口令策略巡检（全部幂等）
from services import db as _db  # noqa: E402
_c = _db.get_connection()
_db.init_schema(_c)
from services.db_seed import ensure_seeded, enforce_admin_password_policy  # noqa: E402
ensure_seeded(_c)
enforce_admin_password_policy(_c)
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
        admin = _resolve_admin()
        if admin:
            g._admin_security_token = security.set_current_admin(admin.get('username'))
    else:
        member = _resolve_member()
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

def _bearer_payload(expected_typ: str):
    """从 Authorization: Bearer 头解析 JWT claims（多端通道）；缺失/无效返回 None。"""
    auth = request.headers.get('Authorization') or ''
    if not auth.startswith('Bearer '):
        return None
    from services import token_auth
    return token_auth.verify_token(auth[7:].strip(), app.secret_key, expected_typ)


def _current_member() -> Dict[str, Any]:
    """当前登录会员（未登录返回空 dict）。"""
    return session.get('member') or {}


def _resolve_member() -> Dict[str, Any]:
    """请求身份解析：优先 Bearer token（安卓/小程序），回退 Cookie 会话（Web）。"""
    payload = _bearer_payload('member')
    if payload and payload.get('member_id'):
        return {'member_id': payload['member_id'],
                'name': payload.get('name') or '',
                'level': payload.get('level') or ''}
    return _current_member()


def _require_member():
    """聊天等接口的登录门禁：未登录返回 (None, 401响应)。"""
    member = _resolve_member()
    if not member:
        return None, (jsonify({'error': '未登录，请先登录会员账号'}), 401)
    return member, None


def _local_chat_response(user_message: str, session_id: str):
    """LangGraph 服务未启动时，回退到本地多智能体流程。"""
    try:
        from multi_agent_customer_service import process_customer_query
        member = _resolve_member()
        result = process_customer_query(user_message, session_id,
                                        customer_info={"member_id": member.get("member_id")})
        return jsonify({
            'response': result.get('response', ''),
            'session_id': session_id,
            'thread_id': session_id,
            'pending_action': result.get('pending_action'),
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
    return _serve_frontend('index.html')


@app.route('/assets/<path:filename>')
def frontend_assets(filename):
    """Vue 构建产物静态资源。"""
    return send_from_directory(FRONTEND_DIST / 'assets', filename)

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

        ai_text, err_msg, http_code, tid, pending_action = run_chat_sync(
            user_message, client_session_id, member_id=member.get('member_id'))
        if err_msg in ('无法创建或找到助手', '无法创建线程'):
            return _local_chat_response(user_message, client_session_id)
        if err_msg:
            return jsonify({'error': err_msg}), http_code or 500

        # 非流式通道也生成会话标题，避免仅走 /api/chat 的会话在侧栏显示“新对话”
        if tid:
            session_titles.generate_title_async(tid, user_message)

        return jsonify({
            'response': ai_text,
            'session_id': tid,
            'thread_id': tid,
            'pending_action': pending_action,
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
    """获取当前登录会员的会话列表（按线程归属过滤）。"""
    member, denied = _require_member()
    if denied:
        return denied
    sessions, err = fetch_sessions_list(member['member_id'])
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'sessions': sessions or []})


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """获取特定会话详情（仅限本人线程）。"""
    member, denied = _require_member()
    if denied:
        return denied
    session_data, err = fetch_session_detail(session_id, member['member_id'])
    if err:
        return jsonify({'error': err}), 403 if err == '无权限访问该会话' else 500
    return jsonify({'session': session_data})


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """删除会话（仅限本人线程）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        ok, status = delete_remote_thread(session_id, member['member_id'])
        if ok:
            return jsonify({'message': '会话删除成功'})
        if status == 403:
            return jsonify({'error': '无权限删除该会话'}), 403
        return jsonify({'error': f'删除会话失败: {status}'}), 500
    except Exception as e:
        return jsonify({'error': f'服务器错误: {str(e)}'}), 500


@app.route('/api/sessions/<session_id>/clear', methods=['POST'])
def clear_session(session_id):
    """清空会话（仅限本人线程，重建的新线程仍归属本人）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        new_thread_id, err = clear_thread_and_create_new(session_id, member['member_id'])
        if err:
            return jsonify({'error': err}), 403 if err == '无权限操作该会话' else 500

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

def _issue_member_token(member: Dict[str, Any]) -> str:
    """为登录会员签发 JWT（多端通道；Web 端 Cookie 照旧下发）。"""
    from services import token_auth
    return token_auth.issue_token({
        'typ': 'member',
        'member_id': member.get('member_id'),
        'name': member.get('name') or '',
        'level': member.get('level') or '',
    }, app.secret_key)


def _issue_admin_token(admin: Dict[str, Any]) -> str:
    """为登录管理员签发 JWT。"""
    from services import token_auth
    return token_auth.issue_token({
        'typ': 'admin',
        'username': admin.get('username'),
        'name': admin.get('name') or '',
    }, app.secret_key)


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
            return jsonify({'member': session['member'], 'token': _issue_member_token(session['member']),
                            'message': f"欢迎回来，{cust['name']}"})

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
        return jsonify({'member': session['member'], 'token': _issue_member_token(session['member']),
                        'message': f"欢迎回来，{cust['name']}"})
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
        return jsonify({'member': session['member'], 'token': _issue_member_token(session['member']),
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
    """当前登录会员（Bearer / Cookie 双通道）"""
    member = _resolve_member()
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
    """一步付讫（模拟支付，兼容既有单测与多端调用）。

    真实渠道请走 /api/pay/create → 收银台 → /api/pay/confirm 或异步通知。
    """
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


# --- 支付渠道抽象（mock / 支付宝沙箱）：幂等落账见 services/payment_service.py ---

def _callback_base() -> str:
    """回调基址：显式配置优先，否则按当前请求 host 自动拼（本地与内网穿透都适用）。"""
    return (os.getenv("PUBLIC_BASE_URL") or request.host_url).rstrip('/')


def _alipay_provider():
    """按 ALIPAY_DEBUG 取沙箱或生产渠道实例。"""
    from services.payment import get_provider
    return get_provider('alipay_sandbox' if ALIPAY_DEBUG else 'alipay')


@app.route('/api/pay/create', methods=['POST'])
def pay_create():
    """发起支付。

    返回 mode=redirect 时前端跳转 pay_url 并轮询 /api/pay/status；
    mode=direct 表示站内可直接确认，调 /api/pay/confirm。
    """
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import payment_service
        base = _callback_base()
        result = payment_service.start_payment(
            order_no=data.get('order_no', ''),
            member_id=member['member_id'],
            return_url=ALIPAY_RETURN_URL or f"{base}/#/pay/result",
            notify_url=ALIPAY_NOTIFY_URL or f"{base}/api/pay/notify/alipay",
        )
        if result.get('error'):
            return jsonify(result), 400
        # redirect 模式改走本站中转页：由它表单 POST 到网关。
        # 直接 GET 跳转在沙箱环境常因缺少 Referer 被拦（RefererCheckFailed），
        # 经本站中转可保证 Referer 恒为本站点。
        if result.get('mode') == 'redirect':
            result['pay_url'] = f"{base}/api/pay/gateway/{result['pay_no']}"
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'发起支付失败: {str(e)}'}), 500


@app.route('/api/pay/gateway/<pay_no>')
def pay_gateway(pay_no):
    """收银台中转页：自动提交表单跳转到支付宝网关。

    电脑网站支付的官方接入方式就是表单 POST；本站中转后再提交，
    Referer 恒为本站点，可规避沙箱对直接 GET 跳转的 RefererCheckFailed 拦截。
    """
    import html
    try:
        from services import payment_repo
        payment = payment_repo.get_payment((pay_no or '').strip().upper())
        if not payment:
            return Response("支付流水不存在或已失效", mimetype='text/html; charset=utf-8'), 404
        if payment["status"] != "待支付":
            return Response("该笔支付已处理，请勿重复支付",
                            mimetype='text/html; charset=utf-8'), 400

        base = _callback_base()
        provider = _alipay_provider()
        form = provider.build_checkout_form(
            pay_no=payment["pay_no"],
            subject=f"机票订单 {payment['order_no']}",
            amount=float(payment["amount"]),
            return_url=ALIPAY_RETURN_URL or f"{base}/#/pay/result",
            notify_url=ALIPAY_NOTIFY_URL or f"{base}/api/pay/notify/alipay",
        )
        if not form:
            return Response("当前支付渠道不支持表单方式，请返回重试",
                            mimetype='text/html; charset=utf-8'), 500

        inputs = "".join(
            f'<input type="hidden" name="{html.escape(str(k))}" value="{html.escape(str(v))}">'
            for k, v in form["fields"].items())
        page = (
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>正在跳转支付宝…</title>'
            '<style>body{font-family:-apple-system,"Microsoft YaHei",sans-serif;'
            'text-align:center;padding:80px 20px;color:#555}'
            'button{margin-top:18px;padding:9px 22px;border:0;border-radius:6px;'
            'background:#1677ff;color:#fff;font-size:14px;cursor:pointer}</style></head>'
            '<body onload="document.getElementById(\'alipayForm\').submit()">'
            f'<p>正在跳转到支付宝收银台…</p>'
            f'<form id="alipayForm" method="POST" action="{html.escape(form["gateway"])}">'
            f'{inputs}</form>'
            '<noscript><p>若浏览器未自动跳转，请手动点击：</p></noscript>'
            '<button type="submit" form="alipayForm">前往支付宝付款</button>'
            '</body></html>'
        )
        return Response(page, mimetype='text/html; charset=utf-8')
    except Exception as e:
        print(f"❌ [支付] 收银台中转页生成失败: {e}")
        return Response("收银台加载失败，请返回重试", mimetype='text/html; charset=utf-8'), 500


@app.route('/api/pay/confirm', methods=['POST'])
def pay_confirm():
    """站内确认支付（direct 模式，目前仅模拟渠道使用）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import payment_service
        result = payment_service.confirm_payment(data.get('pay_no', ''),
                                                 member_id=member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'确认支付失败: {str(e)}'}), 500


@app.route('/api/pay/status')
def pay_status():
    """轮询支付结果（前端支付中弹窗用，零副作用）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import payment_service
        result = payment_service.get_payment_status(request.args.get('order_no', ''),
                                                    member_id=member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'查询支付状态失败: {str(e)}'}), 500


@app.route('/api/pay/notify/alipay', methods=['POST'])
def pay_notify_alipay():
    """支付宝异步通知（无登录态，安全性完全依赖验签）。

    验签 → 校验金额 → 幂等改单 → 返回 success。
    支付宝会间隔重投 8 次，重复通知由 mark_paid 去重，必须原样返回 success 才会停止重试。
    """
    try:
        from services import payment_service
        data = request.form.to_dict()
        provider = _alipay_provider()
        ok, fields = provider.verify_notify(data)
        if not ok:
            print(f"⚠️ [支付] 异步通知验签失败：{fields.get('error') or data.get('out_trade_no')}")
            return 'failure', 400
        if fields.get('trade_status') not in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
            # 非终态（如 WAIT_BUYER_PAY）不处理，但必须回 success，否则会被判定为通知失败
            return 'success'
        result = payment_service.settle_payment(
            out_trade_no=fields.get('out_trade_no'),
            expect_amount=fields.get('amount'),
            trade_no=fields.get('trade_no'),
            buyer_id=fields.get('buyer_id'),
            notify_raw=json.dumps(data, ensure_ascii=False),
            provider_name=provider.name,
        )
        if result.get('error'):
            print(f"⚠️ [支付] 异步通知落账失败：{result['error']}")
            return 'failure', 400
        if result.get('warning'):
            print(f"⚠️ [支付] {result['warning']}")
        return 'success'
    except Exception as e:
        print(f"❌ [支付] 异步通知处理异常: {e}")
        return 'failure', 500


@app.route('/api/pay/return/alipay')
def pay_return_alipay():
    """同步回调：仅用于跳回结果页，不据此改单。

    本地收不到异步通知时，这里主动查单兜底完成闭环；改单仍然走幂等的
    settle_payment，重复执行不会重复出票。
    """
    try:
        from services import payment_service
        data = request.args.to_dict()
        provider = _alipay_provider()
        pay_no = data.get('out_trade_no', '')
        ok, fields = provider.verify_notify(data)
        if ok:
            # 同步参数可被篡改，只信任查询接口返回的真实状态
            q = provider.query_payment(pay_no)
            if q.get('paid'):
                payment_service.settle_payment(
                    out_trade_no=pay_no, expect_amount=q.get('amount'),
                    trade_no=q.get('trade_no'), buyer_id=q.get('buyer_id'),
                    notify_raw='sync-return', provider_name=provider.name)
        # 带上 order_no，结果页据此查状态（回调只回传 out_trade_no）
        order_no = ''
        try:
            from services import payment_repo
            _pay = payment_repo.get_by_out_trade_no(pay_no)
            if _pay:
                order_no = _pay["order_no"]
        except Exception:
            pass
        return redirect(f"/#/pay/result?pay_no={pay_no}&order_no={order_no}")
    except Exception as e:
        print(f"❌ [支付] 同步回调处理异常: {e}")
        return redirect("/#/orders")


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


# --- 航班搜索（客户端直查，支持机票预订页） ---

@app.route('/api/flights/search')
def api_flights_search():
    """按出发/到达城市与日期搜索航班（机票预订页使用）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import flight_repo
        dep = request.args.get('departure', '').strip()
        arr = request.args.get('destination', '').strip()
        date = request.args.get('date', '').strip()
        result = flight_repo.search_flights(dep, arr, date or None)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'航班搜索失败: {str(e)}'}), 500


# --- 值机选座 / 登机牌（REST 写库，不经 LLM） ---

@app.route('/api/checkin/seats')
def checkin_seats():
    """座位图（可带 order_no 标注本订单已选座位）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import checkin_repo
        result = checkin_repo.seat_map(request.args.get('flight_no', ''),
                                       request.args.get('flight_date', ''),
                                       order_no=request.args.get('order_no', ''))
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'座位图查询失败: {str(e)}'}), 500


@app.route('/api/checkin', methods=['POST'])
def do_checkin():
    """值机/改座：校验窗口/归属/舱位，原子占座，返回登机牌数据。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import checkin_repo
        result = checkin_repo.do_checkin(data.get('order_no', ''),
                                         member['member_id'], data.get('seat_no', ''))
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'值机失败: {str(e)}'}), 500


@app.route('/api/checkin/cancel', methods=['POST'])
def cancel_checkin():
    """取消值机：释放座位、删除值机记录。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        data = request.get_json() or {}
        from services import checkin_repo
        result = checkin_repo.cancel_checkin(data.get('order_no', ''), member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'取消值机失败: {str(e)}'}), 500


@app.route('/api/checkin/boardpass')
def checkin_boardpass():
    """登机牌查询（已值机订单）。"""
    try:
        member, denied = _require_member()
        if denied:
            return denied
        from services import checkin_repo
        result = checkin_repo.get_boarding_pass(request.args.get('order_no', ''),
                                                member['member_id'])
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'登机牌查询失败: {str(e)}'}), 500


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


def _resolve_admin() -> Dict[str, Any]:
    """管理端请求身份解析：优先 Bearer token，回退 Cookie 会话。"""
    payload = _bearer_payload('admin')
    if payload and payload.get('username'):
        return {'username': payload['username'], 'name': payload.get('name') or ''}
    return _current_admin()


def admin_required():
    """管理端接口门禁。返回 (admin, denied)。

    除登录/改密/自身信息外，must_change_password=1（首次登录或仍在使用默认
    口令）时拦截一切管理操作——先改密再干活。
    """
    admin = _resolve_admin()
    if not admin:
        return None, (jsonify({'error': '未登录管理员账号'}), 401)
    if _admin_must_change_password(admin.get('username')):
        return None, (jsonify({'error': '首次登录必须先修改密码', 'must_change_password': True}), 403)
    return admin, None


@app.route('/admin')
def admin_index():
    """管理平台页面（前端自行检查登录态并显示登录视图）。"""
    return _serve_frontend('admin.html')


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
        row = conn.execute(
            "SELECT username, password_hash, name, must_change_password FROM admins WHERE username = ?",
            (username,)).fetchone()
        conn.close()
        if not row or not check_password_hash(row['password_hash'], password):
            return jsonify({'error': '用户名或密码错误'}), 401

        session['admin'] = {'username': row['username'], 'name': row['name'],
                            'must_change_password': bool(row['must_change_password'])}
        return jsonify({'admin': session['admin'], 'token': _issue_admin_token(session['admin'])})
    except Exception as e:
        return jsonify({'error': f'登录失败: {str(e)}'}), 500


@app.route('/admin/api/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return jsonify({'message': '已退出'})


@app.route('/admin/api/me')
def admin_me():
    admin = _resolve_admin()
    if not admin:
        return jsonify({'admin': None}), 401
    # must_change_password 以数据库实时值为准（其他端登录改密/启动巡检置位后同步感知）
    from services import db
    conn = db.get_connection()
    row = conn.execute("SELECT must_change_password FROM admins WHERE username = ?",
                       (admin.get('username'),)).fetchone()
    conn.close()
    info = dict(admin)
    info['must_change_password'] = bool(row['must_change_password']) if row else False
    return jsonify({'admin': info})


def _admin_must_change_password(username: str) -> bool:
    from services import db
    conn = db.get_connection()
    row = conn.execute("SELECT must_change_password FROM admins WHERE username = ?",
                       (username,)).fetchone()
    conn.close()
    return bool(row and row['must_change_password'])


@app.route('/admin/api/change_password', methods=['POST'])
def admin_change_password():
    """管理员修改自己的密码（首次登录强制改密走这里）。"""
    admin = _resolve_admin()
    if not admin:
        return jsonify({'error': '未登录管理员账号'}), 401
    data = request.get_json() or {}
    old_pw = data.get('old_password') or ''
    new_pw = data.get('new_password') or ''

    from werkzeug.security import check_password_hash, generate_password_hash
    from services import bootstrap_security, db
    conn = db.get_connection()
    row = conn.execute("SELECT password_hash FROM admins WHERE username = ?",
                       (admin['username'],)).fetchone()
    if not row or not check_password_hash(row['password_hash'], old_pw):
        conn.close()
        return jsonify({'error': '原密码不正确'}), 400
    if new_pw == old_pw:
        conn.close()
        return jsonify({'error': '新密码不能与原密码相同'}), 400
    err = bootstrap_security.validate_admin_password(new_pw)
    if err:
        conn.close()
        return jsonify({'error': err}), 400
    conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = ?",
                 (generate_password_hash(new_pw), admin['username']))
    conn.commit()
    conn.close()
    if 'admin' in session:
        session['admin']['must_change_password'] = False
    return jsonify({'success': True, 'message': '密码已更新，请妥善保管'})


@app.route('/admin/api/stats')
def admin_stats():
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    return jsonify(admin_repo.get_stats())


@app.route('/admin/api/stats/trend')
def admin_stats_trend():
    """工作台图表：近 N 天订单/退款趋势 + 热门航线 Top5。"""
    admin, denied = admin_required()
    if denied:
        return denied
    from services import admin_repo
    try:
        days = int(request.args.get('days', 7))
    except (TypeError, ValueError):
        days = 7
    return jsonify(admin_repo.stats_trend(days))


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


@app.route('/admin/api/flights/gate', methods=['POST'])
def admin_flight_gate():
    """指派/变更航班登机口（变更自动通知已值机旅客）。"""
    admin, denied = admin_required()
    if denied:
        return denied
    data = request.get_json() or {}
    from services import admin_repo
    result = admin_repo.assign_gate(data.get('flight_no', ''), data.get('gate', ''))
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
    if IS_PRODUCTION:
        raise SystemExit(
            "⛔ 生产环境禁止以 python web_app.py 运行（开发服务器带调试重载器）。"
            "请使用 WSGI 服务器，如：waitress-serve --listen=0.0.0.0:5000 web_app:app")
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
