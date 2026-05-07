"""
MCP工具模块
提供机票查询相关的MCP工具
"""

from typing import Dict, List, Any, Optional, Callable
from abc import ABC, abstractmethod
import random

class MCPTool:
    """
    MCP工具基类
    所有MCP工具都必须继承此类并实现标准接口
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: List[Dict] = None
    ):
        self.name = name
        self.description = description
        self.parameters = parameters or []

    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行工具逻辑

        Args:
            **kwargs: 工具参数

        Returns:
            {
                "success": bool,
                "data": Any,
                "error": str
            }
        """
        pass

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的JSON Schema"""
        properties = {}
        required = []

        for param in self.parameters:
            properties[param["name"]] = {
                "type": param.get("type", "string"),
                "description": param.get("description", "")
            }
            if param.get("required", False):
                required.append(param["name"])

        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }


class FlightSearchTool(MCPTool):
    """机票搜索工具"""

    def __init__(self):
        super().__init__(
            name="flight_search",
            description="搜索航班信息，支持按出发地、目的地、日期查询",
            parameters=[
                {"name": "departure", "type": "string", "description": "出发城市", "required": True},
                {"name": "destination", "type": "string", "description": "目的城市", "required": True},
                {"name": "date", "type": "string", "description": "出发日期 YYYY-MM-DD", "required": False},
                {"name": "passengers", "type": "integer", "description": "乘客数量", "required": False}
            ]
        )

        # 模拟航班数据
        self.flight_database = {
            "北京": {
                "上海": [
                    {"airline": "中国国航", "flight_no": "CA1234", "departure_time": "08:00", "arrival_time": "10:30", "price": 680, "type": "直达"},
                    {"airline": "东方航空", "flight_no": "MU5678", "departure_time": "10:00", "arrival_time": "12:30", "price": 720, "type": "直达"},
                    {"airline": "南方航空", "flight_no": "CZ9012", "departure_time": "14:00", "arrival_time": "16:30", "price": 650, "type": "直达"}
                ],
                "广州": [
                    {"airline": "中国国航", "flight_no": "CA3456", "departure_time": "09:00", "arrival_time": "12:00", "price": 1280, "type": "直达"},
                    {"airline": "南方航空", "flight_no": "CZ7890", "departure_time": "15:00", "arrival_time": "18:00", "price": 1350, "type": "直达"}
                ]
            },
            "上海": {
                "北京": [
                    {"airline": "东方航空", "flight_no": "MU1234", "departure_time": "07:00", "arrival_time": "09:30", "price": 700, "type": "直达"},
                    {"airline": "中国国航", "flight_no": "CA5678", "departure_time": "11:00", "arrival_time": "13:30", "price": 680, "type": "直达"}
                ],
                "深圳": [
                    {"airline": "春秋航空", "flight_no": "9C8901", "departure_time": "08:30", "arrival_time": "11:00", "price": 450, "type": "直达"},
                    {"airline": "吉祥航空", "flight_no": "HO2345", "departure_time": "13:00", "arrival_time": "15:30", "price": 520, "type": "直达"}
                ]
            },
            "广州": {
                "北京": [
                    {"airline": "南方航空", "flight_no": "CZ3123", "departure_time": "08:00", "arrival_time": "11:30", "price": 1380, "type": "直达"},
                    {"airline": "中国国航", "flight_no": "CA4567", "departure_time": "14:00", "arrival_time": "17:30", "price": 1320, "type": "直达"}
                ],
                "成都": [
                    {"airline": "南方航空", "flight_no": "CZ8901", "departure_time": "10:00", "arrival_time": "12:30", "price": 880, "type": "直达"}
                ]
            }
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行航班搜索"""
        try:
            departure = kwargs.get("departure", "")
            destination = kwargs.get("destination", "")
            date = kwargs.get("date", "")
            passengers = kwargs.get("passengers", 1)

            if not departure or not destination:
                return {
                    "success": False,
                    "data": None,
                    "error": "出发地和目的地不能为空"
                }

            # 查找航班
            routes = self.flight_database.get(departure, {}).get(destination, [])

            if not routes:
                # 尝试反向查找
                routes = self.flight_database.get(destination, {}).get(departure, [])
                if routes:
                    routes = [{"reverse": True, **route} for route in routes]

            if not routes:
                return {
                    "success": True,
                    "data": {
                        "departure": departure,
                        "destination": destination,
                        "date": date,
                        "flights": [],
                        "message": f"暂未找到从{departure}到{destination}的航班"
                    },
                    "error": None
                }

            # 计算总价
            for flight in routes:
                flight["total_price"] = flight["price"] * passengers

            return {
                "success": True,
                "data": {
                    "departure": departure,
                    "destination": destination,
                    "date": date,
                    "passengers": passengers,
                    "flights": routes,
                    "count": len(routes)
                },
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"航班搜索失败: {str(e)}"
            }


class PriceCompositionTool(MCPTool):
    """机票价格构成工具"""

    def __init__(self):
        super().__init__(
            name="price_composition",
            description="查询机票价格构成，了解机票价格的组成部分",
            parameters=[
                {"name": "price", "type": "number", "description": "机票总价", "required": False}
            ]
        )

        self.price_components = {
            "基础票价": {"description": "占总价的60-80%", "formula": "总价 × (60%~80%)"},
            "燃油附加费": {"description": "根据航线和油价调整", "formula": "通常100-200元"},
            "机场建设费": {"description": "国内50元，国际90元", "formula": "固定金额"},
            "保险费": {"description": "可选，20-60元不等", "formula": "自选"},
            "服务费": {"description": "订票服务费", "formula": "总价 × 3-5%"},
            "行李费": {"description": "超额行李额外收取", "formula": "按件计费"}
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行价格构成查询"""
        try:
            total_price = kwargs.get("price", 0)

            if total_price > 0:
                # 计算各项金额
                breakdown = {}
                breakdown["基础票价"] = f"约 {total_price * 0.7:.0f} 元 (70%)"
                breakdown["燃油附加费"] = "约 100-200 元 (按航线)"
                breakdown["机场建设费"] = "50 元"
                breakdown["保险费"] = "20-60 元 (可选)"
                breakdown["服务费"] = f"约 {total_price * 0.04:.0f} 元 (4%)"
                breakdown["行李费"] = "按需另计"

                return {
                    "success": True,
                    "data": {
                        "total_price": total_price,
                        "breakdown": breakdown,
                        "tip": "实际价格以出票为准，此为参考值"
                    },
                    "error": None
                }
            else:
                return {
                    "success": True,
                    "data": {
                        "components": self.price_components,
                        "example": {
                            "总价1000元的航班": {
                                "基础票价": "约700元",
                                "燃油附加费": "约150元",
                                "机场建设费": "50元",
                                "服务费": "约40元",
                                "合计参考": "约940元"
                            }
                        }
                    },
                    "error": None
                }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"价格构成查询失败: {str(e)}"
            }


class WeatherQueryTool(MCPTool):
    """目的地天气查询工具"""

    def __init__(self):
        super().__init__(
            name="weather_query",
            description="查询目的地天气信息，帮助旅客了解当地气候",
            parameters=[
                {"name": "city", "type": "string", "description": "城市名称", "required": True},
                {"name": "date", "type": "string", "description": "查询日期 YYYY-MM-DD", "required": False}
            ]
        )

        self.weather_database = {
            "北京": {"气候": "温带季风气候", "最佳旅游季": "春秋两季", "平均温度": "12-25°C", "降水": "夏季多雨", "建议": "携带薄外套和雨具"},
            "上海": {"气候": "亚热带季风气候", "最佳旅游季": "春秋两季", "平均温度": "15-28°C", "降水": "梅雨季节6-7月", "建议": "注意防晒，随身带伞"},
            "广州": {"气候": "亚热带季风气候", "最佳旅游季": "10-12月", "平均温度": "20-30°C", "降水": "夏季多雨", "建议": "注意防暑防雨"},
            "深圳": {"气候": "亚热带季风气候", "最佳旅游季": "11-次年1月", "平均温度": "20-28°C", "降水": "夏季多雨", "建议": "气候温暖，宜出行"},
            "成都": {"气候": "亚热带季风气候", "最佳旅游季": "3-6月", "平均温度": "15-25°C", "降水": "秋季多雨", "建议": "美食之都，四季皆宜"},
            "杭州": {"气候": "亚热带季风气候", "最佳旅游季": "4-5月", "平均温度": "15-28°C", "降水": "梅雨季节6月", "建议": "西湖美景，春秋最佳"},
            "西安": {"气候": "温带季风气候", "最佳旅游季": "4-5月,9-10月", "平均温度": "10-25°C", "降水": "秋季多雨", "建议": "历史古都，春秋宜游"},
            "厦门": {"气候": "亚热带季风气候", "最佳旅游季": "10-11月", "平均温度": "18-28°C", "降水": "夏季多台风雨", "建议": "海滨城市，注意台风季节"}
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行天气查询"""
        try:
            city = kwargs.get("city", "")

            if not city:
                return {
                    "success": False,
                    "data": None,
                    "error": "城市名称不能为空"
                }

            weather = self.weather_database.get(city)

            if not weather:
                # 返回所有可用城市
                return {
                    "success": True,
                    "data": {
                        "available_cities": list(self.weather_database.keys()),
                        "message": f"暂未收录{city}的天气信息"
                    },
                    "error": None
                }

            return {
                "success": True,
                "data": {
                    "city": city,
                    "weather": weather
                },
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"天气查询失败: {str(e)}"
            }


class DelayPredictionTool(MCPTool):
    """航班延误预测工具"""

    def __init__(self):
        super().__init__(
            name="delay_prediction",
            description="预测航班延误概率，参考历史数据和常见因素",
            parameters=[
                {"name": "route", "type": "string", "description": "航线，如 北京-上海", "required": False},
                {"name": "airline", "type": "string", "description": "航空公司", "required": False},
                {"name": "date", "type": "string", "description": "航班日期", "required": False}
            ]
        )

        self.delay_factors = {
            "天气原因": {"概率": "20-30%", "影响": "高", "持续时间": "视天气情况而定"},
            "航空管制": {"概率": "15-20%", "影响": "中", "持续时间": "通常2-4小时"},
            "机械故障": {"概率": "5-10%", "影响": "高", "持续时间": "可能需要换机"},
            "机组调度": {"概率": "3-5%", "影响": "中", "持续时间": "1-2小时"},
            "乘客原因": {"概率": "2-3%", "影响": "低", "持续时间": "30分钟以内"},
            "机场流量": {"概率": "10-15%", "影响": "中", "持续时间": "通常1-2小时"}
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行延误预测"""
        try:
            route = kwargs.get("route", "")
            airline = kwargs.get("airline", "")
            date = kwargs.get("date", "")

            # 计算综合延误概率
            base_delay_rate = 15  # 基础延误率15%

            # 根据航空公司调整
            airline_adjustment = {
                "中国国航": -2,
                "东方航空": 0,
                "南方航空": -1,
                "海南航空": -3,
                "春秋航空": 5,
                "吉祥航空": 2
            }

            if airline and airline in airline_adjustment:
                base_delay_rate += airline_adjustment[airline]

            # 根据日期调整（周末稍微容易延误）
            if date:
                # 这里简化处理，实际应该根据具体日期计算
                pass

            return {
                "success": True,
                "data": {
                    "route": route,
                    "airline": airline,
                    "date": date,
                    "prediction": {
                        "overall_delay_rate": f"{base_delay_rate}%",
                        "on_time_rate": f"{100 - base_delay_rate}%",
                        "factors": self.delay_factors,
                        "tips": [
                            "建议提前2小时到达机场",
                            "关注航班动态更新",
                            "购买航班延误险可降低损失",
                            "避免在恶劣天气时段出行"
                        ]
                    }
                },
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"延误预测失败: {str(e)}"
            }


class PriceTrendTool(MCPTool):
    """价格波动预测工具"""

    def __init__(self):
        super().__init__(
            name="price_trend",
            description="预测机票价格波动，帮助旅客选择最佳预订时机",
            parameters=[
                {"name": "route", "type": "string", "description": "航线，如 北京-上海", "required": False},
                {"name": "target_date", "type": "string", "description": "目标出行日期", "required": False}
            ]
        )

        self.price_trends = {
            "提前预订规律": {
                "7天内": "价格最高，较基准价上浮30-100%",
                "7-14天": "价格较高，较基准价上浮10-30%",
                "14-30天": "价格适中，较基准价持平或略低",
                "30-60天": "价格优惠，较基准价低10-20%",
                "60天以上": "价格最优，较基准价低20-40%"
            },
            "季节波动": {
                "春运期间": "价格上浮40-100%",
                "暑寒假": "价格上浮30-60%",
                "五一/十一": "价格上浮40-80%",
                "平日": "价格正常，可享受优惠"
            },
            "周内波动": {
                "周一/周二": "价格较低",
                "周三/周四": "价格适中",
                "周五/周六": "价格较高",
                "周日": "价格适中"
            }
        }

    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行价格趋势预测"""
        try:
            route = kwargs.get("route", "")
            target_date = kwargs.get("target_date", "")

            return {
                "success": True,
                "data": {
                    "route": route,
                    "target_date": target_date,
                    "trends": self.price_trends,
                    "recommendations": [
                        "建议提前30-60天预订",
                        "避开节假日出行",
                        "选择周中航班价格更低",
                        "灵活出行日期可节省20-40%",
                        "关注航司促销活动"
                    ]
                },
                "error": None
            }

        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"价格趋势预测失败: {str(e)}"
            }


class MCPToolRegistry:
    """MCP工具注册表"""

    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """注册默认工具"""
        self.register(FlightSearchTool())
        self.register(PriceCompositionTool())
        self.register(WeatherQueryTool())
        self.register(DelayPredictionTool())
        self.register(PriceTrendTool())

    def register(self, tool: MCPTool):
        """注册工具"""
        self.tools[tool.name] = tool

    def unregister(self, tool_name: str) -> bool:
        """注销工具"""
        if tool_name in self.tools:
            del self.tools[tool_name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[MCPTool]:
        """获取工具"""
        return self.tools.get(name)

    def get_all_tools(self) -> List[MCPTool]:
        """获取所有工具"""
        return list(self.tools.values())

    def get_tools_schema(self) -> List[Dict]:
        """获取所有工具的Schema"""
        return [tool.get_schema() for tool in self.tools.values()]

    def execute_tool(self, name: str, **kwargs) -> Dict[str, Any]:
        """执行工具"""
        tool = self.get_tool(name)
        if not tool:
            return {
                "success": False,
                "data": None,
                "error": f"Tool not found: {name}"
            }
        return tool.execute(**kwargs)


# 创建全局MCP工具注册表
mcp_tool_registry = MCPToolRegistry()