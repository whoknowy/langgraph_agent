/**
 * 执行链路面板的标签映射（工具/图节点 → 中文）。
 * 后端 SSE 事件里已带 label（services/trace_events.py），这里做兜底——
 * 老版本服务端或事件缺 label 时仍能显示可读名称。
 * 与后端 TOOL_LABELS / NODE_LABELS 保持一致，改一处记得同步另一处。
 */

export const TOOL_LABELS = {
  search_flights: '航班搜索', get_price_trend: '价格趋势', get_delay_prediction: '延误预测',
  get_weather: '天气查询', get_flight_price_detail: '票价构成', get_order_bill: '账单查询',
  query_complaint: '投诉查询', create_complaint: '投诉登记', submit_booking_request: '订票确认',
  refund_request: '退票确认', open_seat_map: '值机选座',
  baggage_allowance: '行李额度', web_search: '联网搜索', query_notifications: '站内通知'
}

export const NODE_LABELS = {
  sensitive_guard: '敏感词守卫',
  intent_classifier: '意图分类',
  product_agent: '机票专家',
  billing_agent: '账单专家',
  complaint_agent: '投诉处理专家',
  general_agent: '综合客服',
  trip_planner_agent: '旅行规划师',
  final_response: '最终响应',
  local_fallback: '本地兜底链路'
}

export const toolLabel = (name) => TOOL_LABELS[name] || name
export const nodeLabel = (name) => NODE_LABELS[name] || name
