"""
AC自动机敏感词匹配引擎
用于快速匹配用户输入中的敏感词
"""

from typing import List, Dict, Set, Tuple, Optional
from collections import deque
import re

class TrieNode:
    """Trie树节点"""
    def __init__(self):
        self.children: Dict[str, TrieNode] = {}
        self.is_end: bool = False
        self.word: str = ""
        self.level: int = 0  # 敏感词等级

class ACAutomaton:
    """
    AC自动机 (Aho-Corasick Automaton)
    用于高效的多模式字符串匹配
    """

    def __init__(self):
        self.root = TrieNode()
        self.fail: Dict[TrieNode, TrieNode] = {}

    def insert(self, word: str, level: int = 1) -> None:
        """
        插入敏感词到Trie树

        Args:
            word: 敏感词
            level: 敏感词等级
        """
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end = True
        node.word = word
        node.level = level

    def build(self) -> None:
        """构建fail指针，建立AC自动机"""
        queue = deque()

        # 初始化第一层fail指针
        for child in self.root.children.values():
            self.fail[child] = self.root
            queue.append(child)

        # BFS构建fail指针
        while queue:
            current = queue.popleft()

            for char, child in current.children.items():
                # 计算fail指针
                fail_node = self.fail[current]
                while fail_node != self.root and char not in fail_node.children:
                    fail_node = self.fail[fail_node]

                if char in fail_node.children:
                    self.fail[child] = fail_node.children[char]
                else:
                    self.fail[child] = self.root

                # 如果fail节点是结束节点，则当前节点也是结束节点
                if self.fail[child].is_end:
                    child.is_end = True
                    child.level = max(child.level, self.fail[child].level)
                    if not child.word:
                        child.word = self.fail[child].word

                queue.append(child)

    def search(self, text: str) -> List[Tuple[str, int]]:
        """
        在文本中搜索所有敏感词

        Args:
            text: 待搜索文本

        Returns:
            敏感词列表 [(敏感词, 等级), ...]
        """
        results: List[Tuple[str, int]] = []
        node = self.root

        for char in text:
            # 沿着fail指针寻找匹配
            while node != self.root and char not in node.children:
                node = self.fail[node]

            if char in node.children:
                node = node.children[char]
            else:
                node = self.root

            # 检查当前节点及其fail链上的所有结束节点
            temp = node
            while temp != self.root:
                if temp.is_end:
                    results.append((temp.word, temp.level))
                temp = self.fail[temp]

        return results

    def search_highest_level(self, text: str) -> Tuple[int, List[Tuple[str, int]]]:
        """
        搜索文本中敏感词并返回最高等级

        Args:
            text: 待搜索文本

        Returns:
            (最高等级, [(敏感词, 等级), ...])
        """
        matches = self.search(text)
        if not matches:
            return 0, []

        highest_level = max(match[1] for match in matches)
        return highest_level, matches


class SensitiveWordMatcher:
    """
    敏感词匹配器
    使用AC自动机进行高效匹配
    """

    def __init__(self):
        self.ac_automaton = ACAutomaton()
        self.patterns: Dict[int, List[str]] = {
            1: [],  # L1敏感词
            2: [],  # L2敏感词
            3: []   # L3敏感词
        }

    def add_pattern(self, word: str, level: int) -> None:
        """
        添加敏感词模式

        Args:
            word: 敏感词
            level: 敏感词等级
        """
        self.patterns[level].append(word)
        self.ac_automaton.insert(word, level)

    def build(self) -> None:
        """构建AC自动机"""
        self.ac_automaton.build()

    def match(self, text: str) -> Tuple[bool, int, List[Tuple[str, int]]]:
        """
        匹配文本中的敏感词

        Args:
            text: 待匹配文本

        Returns:
            (是否有敏感词, 最高等级, [(敏感词, 等级), ...])
        """
        matches = self.ac_automaton.search(text)
        if not matches:
            return False, 0, []

        highest_level = max(match[1] for match in matches)
        return True, highest_level, matches

    def match_first(self, text: str) -> Tuple[bool, int, str]:
        """
        匹配文本中第一个敏感词

        Args:
            text: 待匹配文本

        Returns:
            (是否有敏感词, 等级, 敏感词)
        """
        node = self.ac_automaton.root

        for i, char in enumerate(text):
            while node != self.ac_automaton.root and char not in node.children:
                node = node.fail[node]

            if char in node.children:
                node = node.children[char]
            else:
                node = self.ac_automaton.root

            temp = node
            while temp != self.ac_automaton.root:
                if temp.is_end:
                    return True, temp.level, temp.word
                temp = temp.fail[temp]

        return False, 0, ""


class SimpleSensitiveWordMatcher:
    """
    简化版敏感词匹配器
    使用正则表达式进行匹配，适用于小规模敏感词库
    """

    def __init__(self):
        self.patterns: Dict[int, str] = {
            1: "",  # L1敏感词
            2: "",  # L2敏感词
            3: ""   # L3敏感词
        }
        self.compiled_patterns: Dict[int, re.Pattern] = {}

    def add_pattern(self, word: str, level: int) -> None:
        """添加敏感词模式"""
        if self.patterns[level]:
            self.patterns[level] += "|"
        self.patterns[level] += re.escape(word)

    def build(self) -> None:
        """编译正则表达式"""
        for level, pattern in self.patterns.items():
            if pattern:
                self.compiled_patterns[level] = re.compile(pattern)

    def match(self, text: str) -> Tuple[bool, int, List[Tuple[str, int]]]:
        """
        匹配文本中的敏感词

        Args:
            text: 待匹配文本

        Returns:
            (是否有敏感词, 最高等级, [(敏感词, 等级), ...])
        """
        if not self.compiled_patterns:
            return False, 0, []

        all_matches: List[Tuple[str, int]] = []
        highest_level = 0

        for level, pattern in self.compiled_patterns.items():
            matches = pattern.findall(text)
            for match in matches:
                all_matches.append((match, level))
                highest_level = max(highest_level, level)

        if not all_matches:
            return False, 0, []

        return True, highest_level, all_matches

    def match_first(self, text: str) -> Tuple[bool, int, str]:
        """
        匹配文本中第一个敏感词

        Args:
            text: 待匹配文本

        Returns:
            (是否有敏感词, 等级, 敏感词)
        """
        if not self.compiled_patterns:
            return False, 0, ""

        first_match = None
        first_level = 0
        first_pos = len(text)

        for level, pattern in self.compiled_patterns.items():
            match = pattern.search(text)
            if match:
                if match.start() < first_pos:
                    first_pos = match.start()
                    first_match = match.group()
                    first_level = level

        if first_match:
            return True, first_level, first_match

        return False, 0, ""


# 创建全局匹配器实例
def create_matcher_from_knowledge_base(kb) -> SimpleSensitiveWordMatcher:
    """
    从知识库创建匹配器

    Args:
        kb: SensitivityKnowledgeBase实例

    Returns:
        编译好的匹配器
    """
    matcher = SimpleSensitiveWordMatcher()

    # 添加L1敏感词
    for word in kb.level1_words:
        matcher.add_pattern(word, 1)

    # 添加L2敏感词
    for word in kb.level2_words:
        matcher.add_pattern(word, 2)

    # 添加L3敏感词
    for word in kb.level3_words:
        matcher.add_pattern(word, 3)

    # 编译正则表达式
    matcher.build()

    return matcher