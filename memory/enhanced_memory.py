"""
增强记忆管理器
实现短期记忆的滑动窗口+总结概要，长期记忆的向量存储
"""

import time
import json
import uuid
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import numpy as np

# 尝试导入FAISS（如果没有则使用简单的相似度计算）
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

class ShortTermMemory:
    """
    短期记忆管理
    使用滑动窗口+总结概要的方式
    """

    def __init__(self, window_size: int = 5, summary_threshold: int = 10):
        """
        初始化短期记忆
        
        Args:
            window_size: 滑动窗口大小，推荐5-7
            summary_threshold: 触发总结的消息数量阈值
        """
        self.window_size = window_size  # 滑动窗口大小
        self.summary_threshold = summary_threshold  # 总结阈值
        self.messages = []  # 原始消息
        self.summaries = []  # 消息摘要

    def add_message(self, content: str, is_user: bool = True):
        """添加消息到短期记忆"""
        message = {
            "content": content,
            "is_user": is_user,
            "timestamp": time.time(),
            "message_id": str(uuid.uuid4())
        }
        
        self.messages.append(message)
        
        # 维护滑动窗口
        if len(self.messages) > self.window_size:
            # 超出窗口大小，生成摘要
            if len(self.messages) >= self.summary_threshold:
                self._generate_summary()
            # 保持窗口大小
            self.messages = self.messages[-self.window_size:]

    def _generate_summary(self):
        """生成消息摘要"""
        if not self.messages:
            return
        
        # 简单的摘要生成逻辑
        # 实际应用中可以使用LLM生成更智能的摘要
        user_msgs = [msg for msg in self.messages if msg['is_user']]
        system_msgs = [msg for msg in self.messages if not msg['is_user']]
        
        summary = {
            "user_questions": [msg['content'][:50] + "..." for msg in user_msgs[-3:]],
            "system_responses": [msg['content'][:50] + "..." for msg in system_msgs[-3:]],
            "timestamp": time.time(),
            "message_count": len(self.messages),
            "summary_id": str(uuid.uuid4())
        }
        
        self.summaries.append(summary)
        
        # 限制摘要数量
        if len(self.summaries) > 5:
            self.summaries = self.summaries[-5:]

    def get_context(self, max_tokens: int = 1000) -> str:
        """获取短期记忆上下文"""
        context_parts = []
        
        # 最近的消息（滑动窗口内）
        for msg in self.messages:
            role = "用户" if msg['is_user'] else "系统"
            context_parts.append(f"{role}: {msg['content']}")
        
        # 最近的摘要
        for summary in reversed(self.summaries[-2:]):
            context_parts.append("\n【近期对话摘要】")
            context_parts.append(f"用户问题: {', '.join(summary['user_questions'])}")
            context_parts.append(f"系统回复: {', '.join(summary['system_responses'])}")
        
        context = "\n".join(context_parts)
        
        # 控制长度
        if len(context) > max_tokens:
            context = context[-max_tokens:]
        
        return context

    def clear(self):
        """清空短期记忆"""
        self.messages = []
        self.summaries = []


class LongTermMemory:
    """
    长期记忆管理
    使用BM25和向量存储进行混合检索
    """

    def __init__(self, dimension: int = 768):
        """
        初始化长期记忆
        
        Args:
            dimension: 向量维度
        """
        self.dimension = dimension
        self.memories = []  # 记忆存储
        self.embeddings = []  # 向量存储
        
        # 初始化FAISS索引（如果可用）
        if FAISS_AVAILABLE:
            self.index = faiss.IndexFlatL2(dimension)
        else:
            self.index = None
        
        # BM25相关数据结构
        self.documents = []  # 文档内容
        self.doc_ids = []  # 文档ID映射
        self.term_freqs = []  # 词频
        self.doc_lengths = []  # 文档长度
        self.avg_doc_length = 0  # 平均文档长度
        self.total_docs = 0  # 总文档数
        self.idf = {}  # 逆文档频率

    def add_memory(self, content: str, memory_type: str, metadata: Dict = None):
        """添加长期记忆"""
        memory = {
            "content": content,
            "memory_type": memory_type,  # user_preference, common_info, complaint, special_need
            "metadata": metadata or {},
            "timestamp": time.time(),
            "memory_id": str(uuid.uuid4())
        }
        
        # 添加到记忆存储
        memory_id = len(self.memories)
        self.memories.append(memory)
        
        # 生成向量（这里使用简单的哈希，实际应用中应使用embedding模型）
        embedding = self._generate_embedding(content)
        self.embeddings.append(embedding)
        
        # 更新FAISS索引
        if self.index is not None:
            self.index.add(np.array([embedding]))
        
        # 更新BM25相关数据
        self._update_bm25(content, memory_id)

    def _generate_embedding(self, text: str) -> List[float]:
        """生成文本的向量表示"""
        # 简单的哈希实现，实际应用中应使用真实的embedding模型
        # 例如：使用OpenAI API或本地模型
        import hashlib
        hash_value = hashlib.md5(text.encode()).hexdigest()
        # 生成固定维度的向量
        embedding = []
        for i in range(self.dimension):
            embedding.append(float(int(hash_value[i*2:i*2+2], 16)) / 255.0)
        return embedding

    def search(self, query: str, top_k: int = 3, threshold: float = 0.5, alpha: float = 0.5) -> List[Dict]:
        """
        搜索相关的长期记忆（BM25+向量检索融合）
        
        Args:
            query: 查询文本
            top_k: 返回的最大结果数
            threshold: 相似度阈值
            alpha: 融合权重（0-1），alpha越大向量检索权重越高
        
        Returns:
            相关记忆列表
        """
        if not self.memories:
            return []
        
        # 1. BM25搜索
        bm25_results = self._bm25_search(query, top_k * 2)
        bm25_scores = {doc_id: score for score, doc_id in bm25_results}
        
        # 2. 向量搜索
        query_embedding = self._generate_embedding(query)
        vector_scores = {}
        
        if self.index is not None:
            # 使用FAISS搜索
            distances, indices = self.index.search(np.array([query_embedding]), top_k * 2)
            for i, idx in enumerate(indices[0]):
                if idx < len(self.memories):
                    # 转换距离为相似度
                    similarity = 1 / (1 + distances[0][i])
                    if similarity > threshold:
                        vector_scores[idx] = similarity
        else:
            # 使用简单的余弦相似度
            for i, embedding in enumerate(self.embeddings):
                similarity = self._cosine_similarity(query_embedding, embedding)
                if similarity > threshold:
                    vector_scores[i] = similarity
        
        # 3. 线性融合
        all_doc_ids = set(bm25_scores.keys()) | set(vector_scores.keys())
        fused_scores = {}
        
        for doc_id in all_doc_ids:
            # 归一化BM25得分
            bm25_score = bm25_scores.get(doc_id, 0)
            if bm25_scores:
                max_bm25 = max(bm25_scores.values())
                if max_bm25 > 0:
                    bm25_score = bm25_score / max_bm25
            
            # 向量得分（已经是0-1范围）
            vector_score = vector_scores.get(doc_id, 0)
            
            # 线性融合
            fused_score = alpha * vector_score + (1 - alpha) * bm25_score
            fused_scores[doc_id] = fused_score
        
        # 4. 排序并返回结果
        sorted_docs = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        
        for doc_id, score in sorted_docs[:top_k]:
            if doc_id < len(self.memories) and score > threshold:
                memory = self.memories[doc_id]
                # 添加融合得分
                memory['fused_score'] = score
                memory['bm25_score'] = bm25_scores.get(doc_id, 0)
                memory['vector_score'] = vector_scores.get(doc_id, 0)
                results.append(memory)
        
        return results

    def _tokenize(self, text: str) -> List[str]:
        """分词"""
        # 简单的分词实现，实际应用中应使用更复杂的分词器
        import re
        tokens = re.findall(r'\w+', text.lower())
        return tokens
    
    def _update_bm25(self, content: str, doc_id: int):
        """更新BM25数据结构"""
        # 分词
        tokens = self._tokenize(content)
        
        # 计算词频
        term_freq = {}
        for token in tokens:
            term_freq[token] = term_freq.get(token, 0) + 1
        
        # 更新BM25数据
        self.documents.append(content)
        self.doc_ids.append(doc_id)
        self.term_freqs.append(term_freq)
        self.doc_lengths.append(len(tokens))
        
        # 更新统计信息
        self.total_docs += 1
        self.avg_doc_length = sum(self.doc_lengths) / self.total_docs
        
        # 更新IDF
        self._calculate_idf()
    
    def _calculate_idf(self):
        """计算逆文档频率"""
        # 统计每个词出现的文档数
        doc_freq = {}
        for term_freq in self.term_freqs:
            for term in term_freq:
                doc_freq[term] = doc_freq.get(term, 0) + 1
        
        # 计算IDF
        for term, freq in doc_freq.items():
            self.idf[term] = math.log((self.total_docs - freq + 0.5) / (freq + 0.5) + 1)
    
    def _bm25_score(self, query: str, doc_id: int) -> float:
        """计算BM25得分"""
        tokens = self._tokenize(query)
        term_freq = self.term_freqs[doc_id]
        doc_length = self.doc_lengths[doc_id]
        
        # BM25参数
        k1 = 1.5
        b = 0.75
        
        score = 0.0
        for token in tokens:
            if token not in self.idf:
                continue
            
            idf = self.idf[token]
            tf = term_freq.get(token, 0)
            
            # BM25公式
            numerator = idf * tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_length / self.avg_doc_length)
            score += numerator / denominator
        
        return score
    
    def _bm25_search(self, query: str, top_k: int = 3) -> List[Tuple[float, int]]:
        """BM25搜索"""
        scores = []
        for i, doc_id in enumerate(self.doc_ids):
            score = self._bm25_score(query, i)
            if score > 0:
                scores.append((score, doc_id))
        
        # 按得分排序
        scores.sort(key=lambda x: x[0], reverse=True)
        return scores[:top_k]
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a*a for a in vec1) ** 0.5
        norm2 = sum(a*a for a in vec2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)

    def get_by_type(self, memory_type: str) -> List[Dict]:
        """根据类型获取长期记忆"""
        return [mem for mem in self.memories if mem['memory_type'] == memory_type]

    def clear(self):
        """清空长期记忆"""
        self.memories = []
        self.embeddings = []
        if self.index is not None:
            self.index.reset()
        
        # 清空BM25相关数据
        self.documents = []
        self.doc_ids = []
        self.term_freqs = []
        self.doc_lengths = []
        self.avg_doc_length = 0
        self.total_docs = 0
        self.idf = {}


class EnhancedMemoryManager:
    """
    增强的记忆管理器
    整合短期记忆和长期记忆
    """

    def __init__(self, session_id: str, window_size: int = 5):
        """
        初始化增强记忆管理器
        
        Args:
            session_id: 会话ID
            window_size: 滑动窗口大小
        """
        self.session_id = session_id
        self.short_term = ShortTermMemory(window_size=window_size)
        self.long_term = LongTermMemory()

    def add_message(self, content: str, is_user: bool = True):
        """添加消息到记忆"""
        self.short_term.add_message(content, is_user)

    def add_long_term_memory(self, content: str, memory_type: str, metadata: Dict = None):
        """添加长期记忆"""
        self.long_term.add_memory(content, memory_type, metadata)

    def get_context(self, query: str = None, max_tokens: int = 2000) -> str:
        """
        获取综合上下文
        
        Args:
            query: 当前查询（用于检索相关长期记忆）
            max_tokens: 最大上下文长度
        
        Returns:
            综合上下文
        """
        context_parts = []
        
        # 短期记忆
        short_term_context = self.short_term.get_context(max_tokens // 2)
        context_parts.append("【近期对话】")
        context_parts.append(short_term_context)
        
        # 相关长期记忆
        if query:
            relevant_memories = self.long_term.search(query, top_k=3)
            if relevant_memories:
                context_parts.append("\n【相关历史信息】")
                for mem in relevant_memories:
                    context_parts.append(f"{mem['memory_type']}: {mem['content']}")
        
        context = "\n".join(context_parts)
        
        # 控制长度
        if len(context) > max_tokens:
            context = context[-max_tokens:]
        
        return context

    def get_user_preferences(self) -> List[Dict]:
        """获取用户偏好"""
        return self.long_term.get_by_type("user_preference")

    def get_common_info(self) -> List[Dict]:
        """获取常用信息"""
        return self.long_term.get_by_type("common_info")

    def clear(self):
        """清空所有记忆"""
        self.short_term.clear()
        self.long_term.clear()


# 全局记忆管理器字典
memory_managers = {}


def get_memory_manager(session_id: str) -> EnhancedMemoryManager:
    """获取或创建记忆管理器"""
    if session_id not in memory_managers:
        memory_managers[session_id] = EnhancedMemoryManager(session_id)
    return memory_managers[session_id]


def add_message(session_id: str, content: str, is_user: bool = True):
    """添加消息到记忆"""
    manager = get_memory_manager(session_id)
    manager.add_message(content, is_user)


def add_long_term_memory(session_id: str, content: str, memory_type: str, metadata: Dict = None):
    """添加长期记忆"""
    manager = get_memory_manager(session_id)
    manager.add_long_term_memory(content, memory_type, metadata)


def get_context(session_id: str, query: str = None, max_tokens: int = 2000) -> str:
    """获取综合上下文"""
    manager = get_memory_manager(session_id)
    return manager.get_context(query, max_tokens)


def clear_memory(session_id: str):
    """清空记忆"""
    if session_id in memory_managers:
        memory_managers[session_id].clear()
        del memory_managers[session_id]
