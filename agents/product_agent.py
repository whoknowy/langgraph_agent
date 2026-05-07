"""
机票专家智能体
专门负责机票相关信息咨询和推荐
"""

from typing import Dict, List, Any
from langchain.messages import HumanMessage, SystemMessage
from .base_agent import BaseAgent

class ProductAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="机票专家",
            role="机票信息咨询和推荐",
            expertise=["航班查询", "机票预订", "价格比较", "行程规划", "退改签政策",
                     "价格构成分析", "目的地天气", "延误预测", "价格波动预测"]
        )

        # TODO: 机票信息应该从数据库获取，这里只是模拟数据
        # 实际应用中应该连接机票数据库或调用机票API服务
        self.flight_database = {
            "国内航班": {
                "航空公司": ["中国国航", "东方航空", "南方航空", "海南航空"],
                "热门航线": ["北京-上海", "上海-广州", "北京-广州", "深圳-北京"],
                "价格区间": "500-5000元",
                "主要特点": ["航班密度高", "服务完善", "准点率高", "行李额充足"],
                "适用人群": "商务出行、旅游度假、探亲访友",
                "推荐指数": "⭐⭐⭐⭐⭐"
            },
            "国际航班": {
                "航空公司": ["中国国航", "东方航空", "南方航空", "国泰航空", "新加坡航空"],
                "热门航线": ["北京-纽约", "上海-伦敦", "广州-悉尼", "深圳-东京"],
                "价格区间": "2000-20000元",
                "主要特点": ["服务优质", "舒适体验", "中转便捷", "会员福利"],
                "适用人群": "国际商务、出国留学、境外旅游",
                "推荐指数": "⭐⭐⭐⭐⭐"
            },
            "特价机票": {
                "航空公司": ["春秋航空", "吉祥航空", "西部航空", "九元航空"],
                "热门航线": ["上海-厦门", "广州-昆明", "北京-西安", "深圳-杭州"],
                "价格区间": "200-1500元",
                "主要特点": ["价格实惠", "促销活动多", "灵活选择", "性价比高"],
                "适用人群": "预算有限、灵活出行、背包客",
                "推荐指数": "⭐⭐⭐⭐"
            }
        }

        # 机票价格构成说明
        self.price_components = {
            "机票票价": {
                "基础票价": "基础票价，占总价的60-80%",
                "燃油附加费": "根据航线和油价调整，占总价的10-20%",
                "机场建设费": "国内航班50元，国际航班90元",
                "保险费": "可选，20-60元不等",
                "服务费": "订票服务费，占总价的3-5%",
                "行李费": "超额行李额外收取"
            },
            "影响价格因素": {
                "季节性": "节假日、寒暑假价格上浮20-50%",
                "提前预订": "越早预订价格越优惠",
                "航班时段": "早晚班机价格较低",
                "航线热度": "热门航线价格较高",
                "航空公司": "不同航空公司定价策略不同"
            }
        }

        # 目的地天气信息（模拟数据）
        self.destination_weather = {
            "北京": {"气候": "温带季风气候", "最佳旅游季": "春秋两季", "平均温度": "12-25°C", "降水": "夏季多雨"},
            "上海": {"气候": "亚热带季风气候", "最佳旅游季": "春秋两季", "平均温度": "15-28°C", "降水": "梅雨季节6-7月"},
            "广州": {"气候": "亚热带季风气候", "最佳旅游季": "10-12月", "平均温度": "20-30°C", "降水": "夏季多雨"},
            "深圳": {"气候": "亚热带季风气候", "最佳旅游季": "11-次年1月", "平均温度": "20-28°C", "降水": "夏季多雨"},
            "成都": {"气候": "亚热带季风气候", "最佳旅游季": "3-6月", "平均温度": "15-25°C", "降水": "秋季多雨"},
            "杭州": {"气候": "亚热带季风气候", "最佳旅游季": "4-5月", "平均温度": "15-28°C", "降水": "梅雨季节6月"},
            "西安": {"气候": "温带季风气候", "最佳旅游季": "4-5月,9-10月", "平均温度": "10-25°C", "降水": "秋季多雨"},
            "厦门": {"气候": "亚热带季风气候", "最佳旅游季": "10-11月", "平均温度": "18-28°C", "降水": "夏季多台风雨"}
        }

        # 航班延误预测参考
        self.delay_factors = {
            "天气原因": {"概率": "20-30%", "影响": "高", "持续时间": "视天气情况而定"},
            "航空管制": {"概率": "15-20%", "影响": "中", "持续时间": "通常2-4小时"},
            "机械故障": {"概率": "5-10%", "影响": "高", "持续时间": "可能需要换机"},
            "机组调度": {"概率": "3-5%", "影响": "中", "持续时间": "1-2小时"},
            "乘客原因": {"概率": "2-3%", "影响": "低", "持续时间": "30分钟以内"},
            "机场流量": {"概率": "10-15%", "影响": "中", "持续时间": "通常1-2小时"}
        }

        # 价格波动预测规则
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

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """处理机票相关查询"""
        customer_query = state["customer_query"]
        session_id = state.get("session_id", "default")

        # 添加用户消息到会话历史
        self._add_message_to_session(session_id, customer_query, is_user=True)

        # 从会话管理器获取对话历史上下文
        conversation_context = self._get_conversation_context(session_id)

        # 从机票数据库中匹配相关信息
        matched_flights = self._match_flights(customer_query)

        # 分析查询类型
        query_type = self._analyze_query_type(customer_query)

        # 构建系统提示并增强对话上下文说明
        base_system_prompt = f"""你是{self.name}，专门负责{self.role}。
        你的专业领域包括：{', '.join(self.expertise)}

        请根据客户查询提供专业、详细的机票信息，包括：
        - 航班信息和航线选择
        - 价格区间和性价比分析
        - 适用场景和乘客类型
        - 退改签政策和注意事项
        - 机票价格构成说明
        - 目的地天气信息
        - 航班延误预测
        - 价格波动预测

        回答要专业、准确、有说服力。如果客户询问的机票信息不在你的知识范围内，请说明并建议联系客服获取最新信息。"""

        system_prompt = self._enhance_system_prompt_with_context(base_system_prompt)

        # 构建消息列表
        messages = []

        # 添加对话历史上下文（如果有的话）
        if conversation_context:
            context_message = f"""对话历史上下文：
{conversation_context}

请基于以上对话历史和当前查询，提供连贯的回答。"""
            messages.append(SystemMessage(content=context_message))

        # 添加系统提示
        messages.append(SystemMessage(content=system_prompt))

        # 如果有匹配的机票信息，添加到上下文中
        if matched_flights:
            flight_context = f"""机票信息：
{matched_flights}

当前查询：{customer_query}"""
            messages.append(HumanMessage(content=flight_context))
        else:
            messages.append(HumanMessage(content=customer_query))

        # 调用LLM
        try:
            response = self.llm.invoke(messages)
            response_content = response.content
        except Exception as e:
            print(f"机票专家调用LLM时出错: {e}")
            response_content = "抱歉，处理您的机票查询时遇到技术问题，请稍后重试。"

        # 添加AI回复到会话历史
        self._add_message_to_session(session_id, response_content, is_user=False)

        # 更新状态
        state["response"] = response_content
        state["current_agent"] = self.name
        state["tools_used"].append(f"{self.name}_processing")

        return state

    def _analyze_query_type(self, query: str) -> str:
        """分析查询类型"""
        query_lower = query.lower()

        if any(keyword in query_lower for keyword in ["价格构成", "票价组成", "费用", "票价包含"]):
            return "price_composition"
        elif any(keyword in query_lower for keyword in ["天气", "气候", "温度", "下雨", "下雪"]):
            return "weather"
        elif any(keyword in query_lower for keyword in ["延误", "晚点", "延误概率", "准点率"]):
            return "delay_prediction"
        elif any(keyword in query_lower for keyword in ["价格波动", "价格趋势", "涨价", "降价", "打折"]):
            return "price_trend"
        elif any(keyword in query_lower for keyword in ["机票", "航班", "航线", "飞行", "预订"]):
            return "flight_info"

        return "general"

    def _match_flights(self, query: str) -> str:
        """匹配查询中的机票信息"""
        query_lower = query.lower()
        matched_info = []

        # 精确匹配航班类型
        for flight_type, flight_info in self.flight_database.items():
            if flight_type in query_lower:
                # 格式化机票信息
                info_text = f"""航班类型：{flight_type}
航空公司：{', '.join(flight_info['航空公司'])}
热门航线：{', '.join(flight_info['热门航线'])}
价格区间：{flight_info['价格区间']}
主要特点：{', '.join(flight_info['主要特点'])}
适用人群：{flight_info['适用人群']}
推荐指数：{flight_info['推荐指数']}"""
                matched_info.append(info_text)

        # 如果没有精确匹配，尝试模糊匹配
        if not matched_info:
            for flight_type, flight_info in self.flight_database.items():
                # 检查查询中是否包含机票相关的关键词
                if any(keyword in query_lower for keyword in ["机票", "航班", "航线", "飞行", "预订"]):
                    if flight_type not in [info.split('：')[1] for info in matched_info]:
                        info_text = f"""相关航班：{flight_type}
航空公司：{', '.join(flight_info['航空公司'])}
价格区间：{flight_info['价格区间']}
主要特点：{', '.join(flight_info['主要特点'][:2])}..."""
                        matched_info.append(info_text)

        return "\n\n".join(matched_info) if matched_info else ""

    def get_price_composition(self) -> str:
        """获取机票价格构成说明"""
        result = "【机票价格构成说明】\n\n"

        result += "一、机票票价组成：\n"
        for component, description in self.price_components["机票票价"].items():
            result += f"  • {component}：{description}\n"

        result += "\n二、影响价格因素：\n"
        for factor, description in self.price_components["影响价格因素"].items():
            result += f"  • {factor}：{description}\n"

        return result

    def get_destination_weather(self, destination: str) -> str:
        """获取目的地天气信息"""
        if destination in self.destination_weather:
            weather = self.destination_weather[destination]
            result = f"【{destination}天气信息】\n"
            result += f"  气候类型：{weather['气候']}\n"
            result += f"  最佳旅游季节：{weather['最佳旅游季']}\n"
            result += f"  平均温度：{weather['平均温度']}\n"
            result += f"  降水情况：{weather['降水']}\n"
            return result
        else:
            return f"抱歉，暂未收录{destination}的天气信息。建议您查询天气预报或联系客服获取详细信息。"

    def get_delay_prediction(self, route: str = None) -> str:
        """获取航班延误预测信息"""
        result = "【航班延误预测参考】\n\n"
        result += "以下因素可能影响航班准点率：\n\n"

        for factor, info in self.delay_factors.items():
            result += f"  • {factor}：\n"
            result += f"    - 延误概率：{info['概率']}\n"
            result += f"    - 影响程度：{info['影响']}\n"
            result += f"    - 预计持续：{info['持续时间']}\n\n"

        result += "温馨提示：建议您提前2小时到达机场，并关注航班动态更新。"

        return result

    def get_price_trend(self, query: str = None) -> str:
        """获取价格波动预测信息"""
        result = "【机票价格波动预测】\n\n"

        result += "一、提前预订规律：\n"
        for days, trend in self.price_trends["提前预订规律"].items():
            result += f"  • {days}预订：{trend}\n"

        result += "\n二、季节波动：\n"
        for season, trend in self.price_trends["季节波动"].items():
            result += f"  • {season}：{trend}\n"

        result += "\n三、周内波动：\n"
        for day, trend in self.price_trends["周内波动"].items():
            result += f"  • {day}：{trend}\n"

        result += "\n建议：如果您行程灵活，可以考虑在工作日出行，并提前30天以上预订以获得最佳价格。"

        return result