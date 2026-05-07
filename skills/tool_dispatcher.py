"""
工具调度Skill
管理和执行MCP工具
"""

from typing import Dict, List, Any, Optional
from skills.skill_base import Skill, SkillType, SkillResult
from tools.mcp_tools import mcp_tool_registry

class ToolDispatchSkill(Skill):
    """
    工具调度Skill
    根据上下文选择合适的工具并执行
    """

    def __init__(self):
        super().__init__(
            name="tool_dispatcher",
            description="工具调度器：根据意图类型选择合适的工具并执行",
            skill_type=SkillType.TOOL,
            keywords=["工具", "执行", "查询", "搜索"],
            priority=85
        )

        # 意图到工具的映射
        self.intent_tool_mapping = {
            "product_info": ["flight_search"],
            "price_composition": ["price_composition"],
            "destination_weather": ["weather_query"],
            "delay_prediction": ["delay_prediction"],
            "price_trend": ["price_trend"]
        }

    def can_handle(self, context: Dict[str, Any]) -> bool:
        """检查是否能处理该请求"""
        return "tools" in context or "intent" in context

    def execute(self, context: Dict[str, Any]) -> SkillResult:
        """
        执行工具调度

        Args:
            context: {
                "intent": 意图类型,
                "query": 用户输入,
                "tools": 工具名称列表(可选),
                "params": 工具参数字典(可选)
            }

        Returns:
            SkillResult: {
                "success": 是否成功,
                "data": {
                    "tool_results": 工具执行结果字典
                }
            }
        """
        try:
            intent = context.get("intent", "")
            query = context.get("query", "")
            tools = context.get("tools", [])
            params = context.get("params", {})

            # 如果没有指定工具，根据意图获取
            if not tools and intent in self.intent_tool_mapping:
                tools = self.intent_tool_mapping[intent]

            if not tools:
                return SkillResult.success_result(
                    data={"tool_results": {}},
                    metadata={"message": "No tools to execute"},
                    next_action="continue"
                )

            # 解析工具参数
            tool_params = self._parse_tool_params(query, intent, params)

            # 执行工具
            tool_results = {}
            for tool_name in tools:
                result = mcp_tool_registry.execute_tool(tool_name, **tool_params.get(tool_name, {}))
                tool_results[tool_name] = result

            return SkillResult.success_result(
                data={
                    "tool_results": tool_results,
                    "executed_tools": tools
                },
                metadata={
                    "tool_count": len(tools),
                    "tools": list(tool_results.keys())
                },
                next_action="continue"
            )

        except Exception as e:
            return SkillResult.error_result(f"Tool dispatch error: {str(e)}")

    def _parse_tool_params(self, query: str, intent: str, params: Dict) -> Dict[str, Dict]:
        """解析工具参数"""
        tool_params = {}

        # 根据意图解析参数
        if intent == "product_info" or "flight_search" in self.intent_tool_mapping.get(intent, []):
            # 解析航班搜索参数
            tool_params["flight_search"] = self._parse_flight_params(query)

        if intent == "destination_weather" or "weather_query" in self.intent_tool_mapping.get(intent, []):
            # 解析天气查询参数
            tool_params["weather_query"] = self._parse_weather_params(query)

        if intent == "delay_prediction":
            tool_params["delay_prediction"] = self._parse_delay_params(query)

        if intent == "price_trend":
            tool_params["price_trend"] = self._parse_price_trend_params(query)

        # 合并params
        for tool_name, tool_param in tool_params.items():
            if tool_name in params:
                tool_param.update(params[tool_name])

        return tool_params

    def _parse_flight_params(self, query: str) -> Dict[str, Any]:
        """解析航班搜索参数"""
        params = {}

        # 简单的关键词提取
        # 实际应该用NLU来解析
        cities = ["北京", "上海", "广州", "深圳", "成都", "杭州", "西安", "厦门", "南京", "武汉"]
        for city in cities:
            if city in query:
                if "departure" not in params:
                    params["departure"] = city
                elif "destination" not in params:
                    params["destination"] = city

        # 解析日期
        import re
        date_pattern = r'\d{4}[-/]\d{2}[-/]\d{2}'
        dates = re.findall(date_pattern, query)
        if dates:
            params["date"] = dates[0].replace('/', '-')

        # 解析乘客数量
        passenger_patterns = [r'(\d+)个?[人位]', r'(\d+)张?', r'([一两二三四五]+)个?[人位]']
        for pattern in passenger_patterns:
            match = re.search(pattern, query)
            if match:
                num = match.group(1)
                if num in ["一", "二", "三", "四", "五"]:
                    chinese_to_num = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
                    params["passengers"] = chinese_to_num.get(num, 1)
                else:
                    params["passengers"] = int(num)
                break

        return params

    def _parse_weather_params(self, query: str) -> Dict[str, str]:
        """解析天气查询参数"""
        params = {}

        cities = ["北京", "上海", "广州", "深圳", "成都", "杭州", "西安", "厦门", "南京", "武汉"]
        for city in cities:
            if city in query:
                params["city"] = city
                break

        return params

    def _parse_delay_params(self, query: str) -> Dict[str, str]:
        """解析延误预测参数"""
        params = {}

        # 提取航线
        if "-" in query:
            parts = query.split("-")
            if len(parts) >= 2:
                params["route"] = f"{parts[0].split()[-1]}-{parts[1].split()[0]}"

        return params

    def _parse_price_trend_params(self, query: str) -> Dict[str, str]:
        """解析价格趋势参数"""
        params = {}

        # 提取航线
        if "-" in query:
            parts = query.split("-")
            if len(parts) >= 2:
                params["route"] = f"{parts[0].split()[-1]}-{parts[1].split()[0]}"

        return params


# 创建全局工具调度Skill实例
tool_dispatch_skill = ToolDispatchSkill()