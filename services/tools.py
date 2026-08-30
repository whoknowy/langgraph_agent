"""
功能调用工具层（Function Calling）。

- 全部工具基于 services.flight_repo（SQLite 模拟真实数据）与 Open-Meteo（真实天气 API）；
- 使用 LangChain `@tool` 装饰器：docstring 自动成为 AI 可见的描述，签名生成参数 schema；
- 返回统一为紧凑 JSON 字符串（模型消费）；
- 每个工具包含参数校验与错误兜底，不抛异常。
"""

import json
import datetime as _dt

import requests
from langchain_core.tools import tool

from services import flight_repo

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def _dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _weather_text(code: int) -> str:
    return WEATHER_CODES.get(int(code), "未知")


# ---------------------------------------------------------------- 航班

@tool
def search_flights(departure: str, destination: str, date: str = "") -> str:
    """搜索航班信息：返回航线在指定日期的航班时刻、航司、机型与经济舱/商务舱票价。

    Args:
        departure: 出发城市（如"北京"）或机场三字码（如"PEK"）
        destination: 目的城市（如"上海"）或机场三字码
        date: 出发日期 YYYY-MM-DD；留空时返回未来价格区间
    """
    try:
        data = flight_repo.search_flights(departure, destination, date or None)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"航班搜索失败: {e}"})


@tool
def get_price_trend(from_city: str, to_city: str, days_ahead: int = 30) -> str:
    """查询航线未来 N 天的每日最低/最高票价与趋势（用于分析涨价/降价）。

    Args:
        from_city: 出发城市
        to_city: 目的城市
        days_ahead: 查看未来天数，默认 30（1-60）
    """
    try:
        data = flight_repo.get_price_trend(from_city, to_city, days_ahead)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"价格趋势查询失败: {e}"})


@tool
def get_delay_prediction(route: str = "", airline: str = "") -> str:
    """预测航班延误情况：返回航线按航司统计的延误概率、平均延误时长与时段分布。

    Args:
        route: 航线，格式"北京-上海"
        airline: 航司代码（如 CA/MU/CZ/9C）或中文名；留空为全部航司
    """
    try:
        airline_code = _map_airline_code(airline)
        data = flight_repo.get_delay_prediction(route_str=route, airline=airline_code)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"延误预测查询失败: {e}"})


@tool
def get_flight_price_detail(flight_no: str, date: str) -> str:
    """查询指定航班某天的票价构成（基础票价/燃油附加费/机场建设费/服务费等）。

    Args:
        flight_no: 航班号（如 CA1061）
        date: 航班日期 YYYY-MM-DD
    """
    try:
        data = flight_repo.get_flight_price_detail(flight_no, date)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"价格构成查询失败: {e}"})


# ---------------------------------------------------------------- 天气（真实 API）

@tool
def get_weather(city: str, date: str = "") -> str:
    """查询城市天气（真实天气服务）：返回当前温度/天气状况/风力与未来7天预报。

    Args:
        city: 城市名（如"上海"）
        date: 目标日期 YYYY-MM-DD；留空返回实时天气
    """
    try:
        coords = flight_repo.get_city_coords(city)
        if coords.get("error"):
            return _dump(coords)
        return _dump(_fetch_open_meteo(coords["city"], coords["lat"], coords["lon"], date))
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"天气查询失败: {e}"})


def _fetch_open_meteo(city: str, lat: float, lon: float, date_str: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "Asia/Shanghai",
        "forecast_days": 7,
    }
    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        # 断网/服务不可用时降级：明确标注为不可用（不编造数值）
        return {"city": city, "error": f"天气服务暂不可用（{e}）",
                "hint": "请稍后重试，或咨询人工客服"}

    current = data.get("current", {})
    daily = data.get("daily", {})
    days = daily.get("time", [])
    forecast = []
    for i, d in enumerate(days):
        if i >= len(daily.get("weather_code", [])):
            break
        forecast.append({
            "date": d,
            "weather": _weather_text(daily["weather_code"][i]),
            "tmax": daily["temperature_2m_max"][i] if i < len(daily.get("temperature_2m_max", [])) else None,
            "tmin": daily["temperature_2m_min"][i] if i < len(daily.get("temperature_2m_min", [])) else None,
            "precip_prob": daily["precipitation_probability_max"][i]
            if i < len(daily.get("precipitation_probability_max", [])) else None,
        })

    result = {
        "city": city,
        "current": {
            "temperature": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "weather": _weather_text(current.get("weather_code", 0)),
            "wind_speed_kmh": current.get("wind_speed_10m"),
        },
        "forecast": forecast,
    }
    if date_str:
        d = date_str.strip().replace("/", "-")
        hit = next((f for f in forecast if f["date"] == d), None)
        if hit:
            result["target"] = {**hit, "date": d}
        else:
            result["target"] = {"date": d, "note": "超出可预报范围(仅未来7天)，给出7天内趋势"},
    return result


# ---------------------------------------------------------------- 订单/账单/投诉

@tool
def get_order_bill(member_id: str = "", order_no: str = "") -> str:
    """查询账单/订单详情：返回订单号、航班、航线、日期、舱位、金额与状态。

    Args:
        member_id: 会员号（如 M1005）
        order_no: 订单号（如 O0017887）；两者至少提供一个
    """
    try:
        data = flight_repo.get_order_bill(member_id or None, order_no or None)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"订单查询失败: {e}"})


@tool
def query_complaint(member_id: str = "", ticket_no: str = "") -> str:
    """查询投诉处理记录：返回投诉单号、关联订单、内容与处理状态。

    Args:
        member_id: 会员号（如 M1005）
        ticket_no: 投诉单号（如 T1000）；两者至少提供一个
    """
    try:
        data = flight_repo.query_complaints(member_id or None, ticket_no or None)
        return _dump(data)
    except Exception as e:  # pragma: no cover - 兜底
        return _dump({"error": f"投诉记录查询失败: {e}"})


# ---------------------------------------------------------------- 注册表

def _map_airline_code(name: str) -> str:
    """把航司中文名/代码归一为三字码，供 delay_stats 匹配。"""
    if not name:
        return None
    code_map = {"中国国航": "CA", "国航": "CA", "东方航空": "MU", "东航": "MU",
                "南方航空": "CZ", "南航": "CZ", "海南航空": "HU", "海航": "HU",
                "四川航空": "3U", "川航": "3U", "厦门航空": "MF", "厦航": "MF",
                "深圳航空": "ZH", "深航": "ZH", "春秋航空": "9C", "天津航空": "GS", "吉祥航空": "HO"}
    s = str(name).strip().upper()
    if s in code_map:
        return code_map[s]
    if s in code_map.values():
        return s
    for cn, code in code_map.items():
        if cn in str(name):
            return code
    return None


def all_tools():
    """返回全部工具实例（供 create_react_agent / 直接调用）。"""
    return [
        search_flights,
        get_price_trend,
        get_delay_prediction,
        get_flight_price_detail,
        get_weather,
        get_order_bill,
        query_complaint,
    ]


def tools_by_name() -> dict:
    return {t.name: t for t in all_tools()}
