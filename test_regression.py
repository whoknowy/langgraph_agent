# -*- coding: utf-8 -*-
"""重构后回归验收脚本：走 Flask 流式接口，校验新图各路径。每个用例独立会话。"""
import io
import json
import sys
import urllib.request
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5000"


def stream_chat(message, session_id=None):
    """调用 /api/chat/stream，返回 (完整文本, 工具事件列表)。session_id 缺省时新建独立会话。"""
    sid = session_id or str(uuid.uuid4())
    payload = json.dumps({"message": message, "session_id": sid}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/chat/stream", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    text_parts, tools = [], []
    with urllib.request.urlopen(req, timeout=180) as resp:
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
            elif "content" in obj:
                text_parts.append(obj["content"])
    return "".join(text_parts), tools


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {detail}")
    return cond


results = []

# 1. 查航班
t, tools = stream_chat("帮我查一下明天北京到上海的经济舱机票", None)
results.append(check("1.查航班: 流式+search_flights工具", len(t) > 50 and any(x["name"] == "search_flights" for x in tools),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 2. 天气
t, tools = stream_chat("上海明天天气怎么样", None)
results.append(check("2.天气: get_weather工具", any(x["name"] == "get_weather" for x in tools),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 3. 延误预测
t, tools = stream_chat("明天东方航空北京到上海的航班容易延误吗", None)
results.append(check("3.延误预测: get_delay_prediction工具", any(x["name"] == "get_delay_prediction" for x in tools),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 4. 价格趋势
t, tools = stream_chat("未来两周北京到广州的票价走势如何", None)
results.append(check("4.价格趋势: get_price_trend工具", any(x["name"] == "get_price_trend" for x in tools),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 5. 账单
t, tools = stream_chat("帮我查一下会员号M1005的订单账单", None)
results.append(check("5.账单: get_order_bill工具+真实数据", any(x["name"] == "get_order_bill" for x in tools) and ("O0" in t or "订单" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 6. 投诉查询
t, tools = stream_chat("投诉单号T1000现在处理得怎么样了", None)
results.append(check("6.投诉查询: query_complaint工具", any(x["name"] == "query_complaint" for x in tools),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 7. 投诉提交（落库）
t, tools = stream_chat("我是会员M1007，上次航班延误害我误转机，这个问题必须正式处理。我现在要正式提交一个新投诉，请帮我登记", None)
has_create = any(x["name"] == "create_complaint" for x in tools)
results.append(check("7.投诉提交: create_complaint落库", has_create and ("T1" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:80]}"))

# 8. 混合多需求
t, tools = stream_chat("查一下后天成都到杭州的机票，顺便告诉我杭州那几天的天气", None)
names = {x["name"] for x in tools}
results.append(check("8.多需求一句话: ≥2个工具+连贯回答", len(names) >= 2 and len(t) > 80,
                     f"| 工具={sorted(names)} | 回答头: {t[:60]}"))

# 9. 闲聊
t, tools = stream_chat("你好呀，你是谁呀", None)
results.append(check("9.闲聊: 综合客服、无工具调用", len(tools) == 0 and len(t) > 5,
                     f"| 工具={[x['name'] for x in tools]} | 回答头: {t[:60]}"))

# 10. 高危敏感词拦截
t, tools = stream_chat("你们就是一群骗子，我要报警搞垮你们", None)
results.append(check("10.高危拦截: 固定话术、无工具", len(tools) == 0 and ("包含我们不便于处理的内容" in t),
                     f"| 工具={[x['name'] for x in tools]} | 回答: {t[:60]}"))

# 11. 多轮追问（同一会话连续两轮）
follow_sid = str(uuid.uuid4())
stream_chat("帮我查一下9月2日深圳到西安的机票", follow_sid)
t2, tools2 = stream_chat("那9月2日深圳到西安的商务舱价格呢", follow_sid)
results.append(check("11.多轮追问: 接得住上下文", any(x["name"] == "search_flights" for x in tools2) and ("西安" in t2 or "商务" in t2),
                     f"| 第二轮工具={[x['name'] for x in tools2]} | 回答头: {t2[:60]}"))

print()
print(f"=== 通过 {sum(results)}/{len(results)} ===")
sys.exit(0 if all(results) else 1)
