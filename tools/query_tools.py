"""
查询分类工具函数
"""

from typing import Dict, List, Any
from langchain.messages import HumanMessage, SystemMessage
from langchain.tools import tool

@tool
def classify_query(query: str, llm=None) -> str:
    """根据客户查询内容分类查询类型"""
    # 使用LLM进行分类
    system_prompt = """你是一个查询分类专家。请根据客户查询内容，将查询分类为以下类型之一：
    - product_info: 机票信息查询（询问航班、价格、预订、退改签等）
    - price_composition: 机票价格构成（询问票价组成、费用明细、票价包含什么等）
    - destination_weather: 目的地天气（询问目的地气候、温度、降水、旅游季节等）
    - delay_prediction: 航班延误预测（询问延误概率、准点率、晚点原因等）
    - price_trend: 价格波动预测（询问价格趋势、涨价降价、打折时间等）
    - billing: 账单问题（支付、退款、发票、费用等）
    - complaint: 投诉建议（不满、投诉、建议、反馈等）
    - general_inquiry: 一般咨询（其他类型的问题）

    只返回分类结果，不要其他内容。"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"请分类以下查询：{query}")
    ]

    try:
        response = llm.invoke(messages)
        result = response.content.strip()
        return result
    except Exception as e:
        print(f"Error in classify_query: {e}")
        return "general_inquiry"