"""
意图路由Skill
支持多意图识别（按用户提问顺序）
"""

from typing import Dict, List, Any, Tuple
from skills.skill_base import Skill, SkillType, SkillResult
from tools.mcp_tools import mcp_tool_registry
from memory import get_enhanced_context

class IntentRoutingSkill(Skill):
    """
    意图路由Skill
    识别用户意图并路由到相应的处理Agent或工具
    支持多意图识别，按用户提问顺序返回
    """

    def __init__(self):
        super().__init__(
            name="intent_router",
            description="意图路由器：识别多意图并按用户提问顺序返回",
            skill_type=SkillType.WORKFLOW,
            keywords=["意图", "路由", "识别", "分发"],
            priority=90
        )

        self.intent_mapping = {
            "product_info": {"agent": "product_agent", "tools": ["flight_search"], "description": "机票预订咨询", "need_tool": True},
            "price_composition": {"agent": "product_agent", "tools": ["price_composition"], "description": "机票价格构成", "need_tool": True},
            "destination_weather": {"agent": "product_agent", "tools": ["weather_query"], "description": "目的地天气查询", "need_tool": True},
            "delay_prediction": {"agent": "product_agent", "tools": ["delay_prediction"], "description": "航班延误预测", "need_tool": True},
            "price_trend": {"agent": "product_agent", "tools": ["price_trend"], "description": "价格波动预测", "need_tool": True},
            "billing": {"agent": "billing_agent", "tools": [], "description": "账单问题", "need_tool": False},
            "complaint": {"agent": "complaint_agent", "tools": [], "description": "投诉建议", "need_tool": False},
            "general_inquiry": {"agent": "general_agent", "tools": [], "description": "一般咨询", "need_tool": False}
        }

    def can_handle(self, context: Dict[str, Any]) -> bool:
        return "query" in context

    def execute(self, context: Dict[str, Any]) -> SkillResult:
        try:
            query = context.get("query", "")

            if not query:
                return SkillResult.error_result("Query is empty")

            # 执行多意图识别，返回(意图ID列表, 是否规则命中, 置信度)
            intent_ids, is_rule_matched, confidence = self._classify_intent(query)
            
            # 判断是否多意图
            is_multi_intent = len(intent_ids) > 1

            # 获取第一个意图的路由信息作为主路由
            main_intent = intent_ids[0]
            routing_info = self.intent_mapping.get(main_intent, self.intent_mapping["general_inquiry"])

            # 多意图时并行执行工具，单意图时按原有逻辑执行
            tool_results = {}
            if is_multi_intent:
                tool_results = self._execute_tools_parallel(query, intent_ids, context.get("session_id"))
            elif routing_info.get("need_tool", False):
                tool_results = self._execute_tools(query, main_intent, routing_info, context.get("session_id"))

            return SkillResult.success_result(
                data={
                    "intent": main_intent,
                    "intents": intent_ids,
                    "is_multi_intent": is_multi_intent,
                    "agent": routing_info["agent"],
                    "tools": routing_info.get("tools", []),
                    "tool_results": tool_results,
                    "description": routing_info["description"],
                    "is_rule_matched": is_rule_matched,
                    "confidence": confidence
                },
                metadata={"intent": main_intent, "intents": intent_ids, "is_multi_intent": is_multi_intent},
                next_action="route_to",
                route_to=routing_info["agent"]
            )

        except Exception as e:
            return SkillResult.error_result(f"Intent routing error: {str(e)}")

    def _classify_intent(self, query: str) -> Tuple[List[str], bool, float]:
        """
        多意图识别 - 按用户提问顺序返回
        
        Returns:
            (意图ID列表, 是否规则命中, 最高置信度)
        """
        query_lower = query.lower()
        matched_intents = []

        rule_intents = [
            ("price_composition", ["价格构成", "票价组成", "费用明细"]),
            ("destination_weather", ["天气", "气候", "温度", "降水"]),
            ("delay_prediction", ["延误", "晚点", "准点率"]),
            ("price_trend", ["价格波动", "价格趋势", "涨价", "降价"]),
            ("product_info", ["机票", "航班", "航线", "预订", "退票", "改签", "查航班", "买机票", "订机票"]),
            ("billing", ["支付", "退款", "发票", "账单", "费用"]),
            ("complaint", ["投诉", "不满", "反馈", "建议"])
        ]

        for intent_id, keywords in rule_intents:
            for keyword in keywords:
                pos = query_lower.find(keyword)
                if pos != -1:
                    matched_intents.append((intent_id, 0.95, pos))
                    break

        matched_intents.sort(key=lambda x: x[2])
        
        seen = set()
        unique_intents = []
        for item in matched_intents:
            if item[0] not in seen:
                seen.add(item[0])
                unique_intents.append(item)

        if unique_intents:
            intent_ids = [item[0] for item in unique_intents]
            return (intent_ids, True, 0.95)

        from skills.embedding_intent_classifier import embedding_intent_classifier
        result = embedding_intent_classifier.classify(query)

        if result["confidence"] > 0.5:
            return ([result["intent"]], False, result["confidence"])

        return (["general_inquiry"], False, 0.3)

    def _execute_tools_parallel(self, query: str, intents: List[str], session_id: str = None) -> Dict[str, Any]:
        """
        并行执行多个意图对应的工具
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        
        tool_results = {}
        
        all_tool_tasks = []
        for intent_id in intents:
            routing_info = self.intent_mapping.get(intent_id)
            if routing_info and routing_info.get("need_tool", False):
                for tool_name in routing_info.get("tools", []):
                    params = self._parse_tool_params(query, intent_id).get(tool_name, {})
                    all_tool_tasks.append((intent_id, tool_name, params))
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            def run_tool(intent_id, tool_name, params):
                try:
                    result = mcp_tool_registry.execute_tool(tool_name, **params)
                    return (intent_id, tool_name, {"success": True, "data": result})
                except Exception as e:
                    return (intent_id, tool_name, {"success": False, "error": str(e)})
            
            futures = [loop.run_in_executor(executor, run_tool, intent_id, tool_name, params) 
                      for intent_id, tool_name, params in all_tool_tasks]
            
            results = loop.run_until_complete(asyncio.gather(*futures))
            
            for intent_id, tool_name, result in results:
                if intent_id not in tool_results:
                    tool_results[intent_id] = {}
                tool_results[intent_id][tool_name] = result
        
        return tool_results

    def _execute_tools(self, query: str, intent: str, routing_info: Dict, session_id: str = None) -> Dict[str, Any]:
        """
        执行工具调度

        Args:
            query: 用户输入
            intent: 意图ID
            routing_info: 路由信息
            session_id: 会话ID（用于获取上下文）

        Returns:
            工具执行结果字典
        """
        tool_results = {}
        tools = routing_info.get("tools", [])

        if not tools:
            return tool_results

        # 解析工具参数
        tool_params = self._parse_tool_params(query, intent)

        # 执行工具
        for tool_name in tools:
            try:
                result = mcp_tool_registry.execute_tool(tool_name, **tool_params.get(tool_name, {}))
                tool_results[tool_name] = result
            except Exception as e:
                tool_results[tool_name] = {
                    "success": False,
                    "data": None,
                    "error": str(e)
                }

        return tool_results

    def _parse_tool_params(self, query: str, intent: str) -> Dict[str, Dict]:
        """根据意图解析工具参数"""
        tool_params = {}

        if intent == "product_info":
            tool_params["flight_search"] = self._parse_flight_params(query)
        elif intent == "destination_weather":
            tool_params["weather_query"] = self._parse_weather_params(query)
        elif intent == "delay_prediction":
            tool_params["delay_prediction"] = self._parse_delay_params(query)
        elif intent == "price_trend":
            tool_params["price_trend"] = self._parse_price_trend_params(query)
        elif intent == "price_composition":
            tool_params["price_composition"] = {}

        return tool_params

    def _parse_flight_params(self, query: str) -> Dict[str, Any]:
        """解析航班搜索参数"""
        import re
        params = {}

        cities = ["北京", "上海", "广州", "深圳", "成都", "杭州", "西安", "厦门", "南京", "武汉"]
        for city in cities:
            if city in query:
                if "departure" not in params:
                    params["departure"] = city
                elif "destination" not in params:
                    params["destination"] = city
                    break

        date_pattern = r'\d{4}[-/]\d{2}[-/]\d{2}'
        dates = re.findall(date_pattern, query)
        if dates:
            params["date"] = dates[0].replace('/', '-')

        passenger_patterns = [r'(\d+)个?[人位]', r'(\d+)张?', r'([一两二三四五]+)个?[人位]']
        for pattern in passenger_patterns:
            match = re.search(pattern, query)
            if match:
                num = match.group(1)
                chinese_to_num = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
                params["passengers"] = chinese_to_num.get(num, int(num))
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
        if "-" in query:
            parts = query.split("-")
            if len(parts) >= 2:
                params["route"] = f"{parts[0].split()[-1]}-{parts[1].split()[0]}"
        return params

    def _parse_price_trend_params(self, query: str) -> Dict[str, str]:
        """解析价格趋势参数"""
        params = {}
        if "-" in query:
            parts = query.split("-")
            if len(parts) >= 2:
                params["route"] = f"{parts[0].split()[-1]}-{parts[1].split()[0]}"
        return params


class AgentDispatchSkill(Skill):
    """
    Agent调度Skill
    负责调用具体的Agent进行处理
    """

    def __init__(self):
        super().__init__(
            name="agent_dispatcher",
            description="Agent调度器：调用具体的Agent处理用户请求，整合工具结果",
            skill_type=SkillType.AGENT,
            keywords=["Agent", "调度", "处理", "执行"],
            priority=80
        )

    def can_handle(self, context: Dict[str, Any]) -> bool:
        """检查是否能处理该请求"""
        return "agent" in context or "agent_name" in context

    def execute(self, context: Dict[str, Any]) -> SkillResult:
        """
        执行Agent调度

        Args:
            context: {
                "agent": Agent名称或Agent对象,
                "query": 用户输入,
                "session_id": 会话ID,
                "mood_tag": 情绪标签(可选),
                "filter_result": 过滤结果(可选),
                "tool_results": 工具执行结果(可选)
            }

        Returns:
            SkillResult: {
                "success": 是否成功,
                "data": {
                    "response": Agent响应内容,
                    "agent": 处理的Agent名称
                }
            }
        """
        try:
            agent_name = context.get("agent", context.get("agent_name", "general_agent"))
            query = context.get("query", "")
            mood_tag = context.get("mood_tag", "")
            filter_result = context.get("filter_result", {})
            tool_results = context.get("tool_results", {})

            # 获取Agent实例
            agent = self._get_agent(agent_name)
            if not agent:
                return SkillResult.error_result(f"Agent not found: {agent_name}")

            # 获取增强上下文
            session_id = context.get("session_id", "")
            enhanced_context = ""
            if session_id:
                try:
                    enhanced_context = get_enhanced_context(session_id, query)
                except Exception as e:
                    print(f"获取增强上下文失败: {e}")

            # 准备Agent输入
            agent_input = {
                "customer_query": query,
                "session_id": session_id,
                "mood_tag": mood_tag,
                "filter_action": filter_result.get("action", ""),
                "filter_response": filter_result.get("response", ""),
                "enhanced_context": enhanced_context
            }

            # 添加工具执行结果
            if tool_results:
                agent_input["tool_results"] = tool_results

            # 调用Agent
            from agents import initialize_agents
            agents = initialize_agents()
            target_agent = agents.get(agent_name)

            if not target_agent:
                return SkillResult.error_result(f"Agent not found: {agent_name}")

            # 处理查询
            result = target_agent.process(agent_input)

            # 处理结果
            response = result.get("response", "")
            current_agent = result.get("current_agent", agent_name)

            # 如果有情绪标签，添加安抚话术
            if mood_tag == "dissatisfied" and filter_result.get("response"):
                response = f"{filter_result['response']}\n\n{response}"

            return SkillResult.success_result(
                data={
                    "response": response,
                    "agent": current_agent,
                    "result": result
                },
                metadata={
                    "agent_name": current_agent,
                    "mood_tag": mood_tag
                },
                next_action="continue"
            )

        except Exception as e:
            return SkillResult.error_result(f"Agent dispatch error: {str(e)}")

    def _get_agent(self, agent_name: str):
        """获取Agent实例"""
        from agents import initialize_agents
        agents = initialize_agents()
        return agents.get(agent_name)


# 创建全局意图路由Skill和Agent调度Skill实例
intent_routing_skill = IntentRoutingSkill()
agent_dispatch_skill = AgentDispatchSkill()