"""
Embedding意图分类器
使用向量嵌入和相似度匹配进行意图识别
"""

import requests
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from config import OPENAI_API_KEY, OPENAI_BASE_URL

class IntentKnowledgeBase:
    """
    意图知识库
    存储每种意图的标准表述及其嵌入向量
    """

    def __init__(self):
        self.intents = {
            "product_info": {
                "name": "机票预订咨询",
                "description": "用户询问机票预订相关问题",
                "examples": [
                    "我想查一下北京到上海的机票",
                    "帮我看看明天有去广州的航班吗",
                    "我要买一张去深圳的机票",
                    "请问怎么预订机票",
                    "机票怎么买",
                    "帮我查下航班",
                    "有去杭州的飞机吗",
                    "我想订一张机票",
                    "帮我看看机票价格",
                    "查下从上海到成都的航班"
                ]
            },
            "price_composition": {
                "name": "机票价格构成",
                "description": "用户询问机票价格组成明细",
                "examples": [
                    "机票价格是怎么构成的",
                    "票价里都包含什么",
                    "机票价格组成有哪些",
                    "一张机票的钱都花在哪些地方了",
                    "帮我分析下机票价格",
                    "票价组成是什么",
                    "机票费用明细",
                    "为什么机票这么贵",
                    "机票价格都包含什么费用"
                ]
            },
            "destination_weather": {
                "name": "目的地天气查询",
                "description": "用户询问目的地天气情况",
                "examples": [
                    "北京天气怎么样",
                    "上海最近天气好吗",
                    "广州热不热",
                    "成都会不会下雨",
                    "杭州天气如何",
                    "深圳气候怎么样",
                    "西安温度多少",
                    "厦门适合什么时候去",
                    "目的地天气怎么样",
                    "帮我查下目的地天气"
                ]
            },
            "delay_prediction": {
                "name": "航班延误预测",
                "description": "用户询问航班是否会延误",
                "examples": [
                    "这个航班会延误吗",
                    "CA1234会晚点吗",
                    "今天的飞机会不会延误",
                    "帮我预测下航班准点率",
                    "这趟航班准点吗",
                    "延误的可能性大吗",
                    "会不会晚点啊",
                    "航班准点率怎么样",
                    "会不会delay啊"
                ]
            },
            "price_trend": {
                "name": "价格波动预测",
                "description": "用户询问机票价格趋势和最佳购买时机",
                "examples": [
                    "机票价格会涨吗",
                    "什么时候买机票最便宜",
                    "机票价格会降吗",
                    "现在是买机票的好时机吗",
                    "价格趋势怎么样",
                    "机票会涨价吗",
                    "什么时候降价",
                    "帮我分析下机票价格走势",
                    "五一机票价格会涨吗",
                    "近期机票价格会波动吗"
                ]
            },
            "billing": {
                "name": "账单支付问题",
                "description": "用户询问支付、退款、发票等账单问题",
                "examples": [
                    "怎么支付",
                    "支持哪些付款方式",
                    "可以退款吗",
                    "机票退款规则是什么",
                    "怎么开发票",
                    "行程单怎么获取",
                    "退款多久到账",
                    "怎么报销",
                    "支付遇到问题怎么办"
                ]
            },
            "complaint": {
                "name": "投诉建议",
                "description": "用户提出投诉或建议",
                "examples": [
                    "我要投诉",
                    "服务太差了",
                    "给你们提个建议",
                    "这个处理我不满意",
                    "我要反馈一个问题",
                    "非常不满意",
                    "要投诉你们",
                    "希望改进服务"
                ]
            },
            "general_inquiry": {
                "name": "一般咨询",
                "description": "用户询问其他一般性问题",
                "examples": [
                    "你们公司电话多少",
                    "客服工作时间",
                    "怎么联系你们",
                    "你们在哪里",
                    "有什么其他服务",
                    "随便问问",
                    "你好啊",
                    "在吗"
                ]
            }
        }
        self.intent_embeddings: Dict[str, np.ndarray] = {}

    def get_all_intents(self) -> List[str]:
        """获取所有意图ID列表"""
        return list(self.intents.keys())

    def get_intent_examples(self, intent_id: str) -> List[str]:
        """获取意图的标准表述"""
        if intent_id in self.intents:
            return self.intents[intent_id]["examples"]
        return []

    def get_intent_info(self, intent_id: str) -> Dict[str, str]:
        """获取意图信息"""
        return self.intents.get(intent_id, {})

    def set_embedding(self, intent_id: str, embedding: np.ndarray):
        """设置意图的嵌入向量"""
        self.intent_embeddings[intent_id] = embedding

    def get_embedding(self, intent_id: str) -> Optional[np.ndarray]:
        """获取意图的嵌入向量"""
        return self.intent_embeddings.get(intent_id)


class EmbeddingService:
    """
    嵌入服务
    使用OpenAI兼容API进行文本嵌入
    """

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or OPENAI_API_KEY
        self.base_url = base_url or f"{OPENAI_BASE_URL}/embeddings"
        self.model = "BAAI/bge-large-zh-v1.5"

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        """
        将文本转化为嵌入向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量或None（失败时）
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": self.model,
                "input": text
            }

            response = requests.post(
                self.base_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                embedding = data["data"][0]["embedding"]
                return np.array(embedding)
            else:
                print(f"Embedding API error: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"Embedding error: {str(e)}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """
        批量将文本转化为嵌入向量

        Args:
            texts: 输入文本列表

        Returns:
            嵌入向量列表
        """
        results = []
        for text in texts:
            embedding = self.embed_text(text)
            results.append(embedding)
        return results


class EmbeddingIntentClassifier:
    """
    基于Embedding的意图分类器
    结合规则匹配和向量相似度进行意图识别
    """

    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.knowledge_base = IntentKnowledgeBase()
        self.rule_keywords = {
            "price_composition": ["价格构成", "票价组成", "费用明细", "票价里", "票价结构", "价格组成", "费用"],
            "destination_weather": ["天气", "气候", "温度", "降水", "下雨", "晴天"],
            "delay_prediction": ["延误", "晚点", "准点率", "会晚吗", "会延误吗", "会delay吗"],
            "price_trend": ["价格波动", "价格趋势", "涨价", "降价", "会涨吗", "会降吗", "便宜", "划算"],
            "product_info": ["机票", "航班", "航线", "预订", "退票", "改签", "查航班", "买机票", "订机票"],
            "billing": ["支付", "退款", "发票", "账单", "费用", "付款", "退钱"],
            "complaint": ["投诉", "不满", "反馈", "建议", "很差", "不满意", "要投诉"]
        }
        self._initialized = False

    def initialize(self):
        """初始化：预计算所有意图的嵌入向量"""
        if self._initialized:
            return

        print("正在初始化意图知识库嵌入向量...")

        for intent_id in self.knowledge_base.get_all_intents():
            examples = self.knowledge_base.get_intent_examples(intent_id)
            embeddings = []

            for example in examples:
                embedding = self.embedding_service.embed_text(example)
                if embedding is not None:
                    embeddings.append(embedding)

            if embeddings:
                mean_embedding = np.mean(embeddings, axis=0)
                self.knowledge_base.set_embedding(intent_id, mean_embedding)
                print(f"  ✓ {intent_id} 嵌入向量已计算 ({len(embeddings)}/{len(examples)} 条)")
            else:
                print(f"  ✗ {intent_id} 嵌入向量计算失败")

        self._initialized = True
        print("意图知识库初始化完成")

    def _rule_match(self, query: str) -> Tuple[bool, str, float]:
        """
        规则匹配意图

        Returns:
            (是否匹配, 意图ID, 置信度)
        """
        query_lower = query.lower()

        for intent_id, keywords in self.rule_keywords.items():
            matched_count = sum(1 for kw in keywords if kw in query_lower)
            if matched_count > 0:
                confidence = min(0.5 + (matched_count * 0.15), 0.95)
                return True, intent_id, confidence

        return False, "", 0.0

    def _embedding_match(self, query: str) -> Tuple[str, float]:
        """
        基于Embedding的意图匹配

        Returns:
            (意图ID, 相似度得分)
        """
        if not self._initialized:
            self.initialize()

        query_embedding = self.embedding_service.embed_text(query)
        if query_embedding is None:
            return "", 0.0

        best_intent = ""
        best_score = 0.0

        for intent_id in self.knowledge_base.get_all_intents():
            intent_embedding = self.knowledge_base.get_embedding(intent_id)
            if intent_embedding is not None:
                similarity = self._cosine_similarity(query_embedding, intent_embedding)
                if similarity > best_score:
                    best_score = similarity
                    best_intent = intent_id

        return best_intent, best_score

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot_product / (norm1 * norm2))

    def classify(self, query: str) -> Dict[str, Any]:
        """
        意图分类主入口

        Args:
            query: 用户输入

        Returns:
            {
                "intent": 意图ID,
                "confidence": 置信度,
                "method": 识别方法 ("rule" 或 "embedding"),
                "all_scores": 所有意图的得分
            }
        """
        rule_matched, rule_intent, rule_confidence = self._rule_match(query)

        if rule_matched:
            return {
                "intent": rule_intent,
                "confidence": rule_confidence,
                "method": "rule",
                "query": query
            }

        embedding_intent, embedding_score = self._embedding_match(query)

        if embedding_intent and embedding_score > 0.5:
            return {
                "intent": embedding_intent,
                "confidence": embedding_score,
                "method": "embedding",
                "query": query,
                "all_scores": self._get_all_scores(query)
            }

        return {
            "intent": "general_inquiry",
            "confidence": 0.3,
            "method": "default",
            "query": query
        }

    def _get_all_scores(self, query: str) -> Dict[str, float]:
        """获取所有意图的相似度得分"""
        scores = {}
        query_embedding = self.embedding_service.embed_text(query)

        if query_embedding is None:
            return scores

        for intent_id in self.knowledge_base.get_all_intents():
            intent_embedding = self.knowledge_base.get_embedding(intent_id)
            if intent_embedding is not None:
                similarity = self._cosine_similarity(query_embedding, intent_embedding)
                scores[intent_id] = similarity

        return scores


embedding_intent_classifier = EmbeddingIntentClassifier()
