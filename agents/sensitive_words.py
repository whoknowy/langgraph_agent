"""
敏感词知识库
维护三级敏感词分类及其处理策略
"""

from typing import Dict, List, Set, Tuple, Optional
from enum import Enum

class SensitivityLevel(Enum):
    """敏感词等级枚举"""
    L1_LOW = 1  # 低危 - 普通安抚
    L2_MEDIUM = 2  # 中危 - 转普通客服
    L3_HIGH = 3  # 高危 - 转投诉专员

class SensitivityKnowledgeBase:
    """
    敏感词知识库
    维护三级敏感词分类及其匹配处理逻辑
    """

    def __init__(self):
        # 初始化三级敏感词库
        self.level1_words: Set[str] = set()  # L1 低危敏感词
        self.level2_words: Set[str] = set()  # L2 中危敏感词
        self.level3_words: Set[str] = set()  # L3 高危敏感词

        # 敏感词到等级的映射
        self.word_to_level: Dict[str, int] = {}

        # L1安抚话术模板
        self.l1_comfort_templates: List[str] = []

        # 初始化默认敏感词库
        self._initialize_default_words()

    def _initialize_default_words(self):
        """初始化默认敏感词库"""
        # L1 低危敏感词 - 表示用户不满但未升级到投诉
        self.level1_words = {
            # 通用不满表达
            "太慢了", "等待时间", "效率低", "不满意", "很失望",
            "不太满意", "希望能改进", "有点问题", "不太顺利",
            "不太舒服", "不太方便", "有点麻烦", "不太满意",

            # 快递相关低危词
            "快递慢", "快递延误", "快递晚了", "等了好久",
            "发货慢", "配送慢", "运输慢", "时效差",

            # 服务相关低危词
            "服务态度一般", "回复慢", "响应慢", "效率不高",
            "处理速度慢", "解决问题慢", "进度慢",

            # 产品相关低危词
            "质量一般", "有点瑕疵", "不太满意", "和预期有差距",
            "包装一般", "外观一般"
        }

        # L2 中危敏感词 - 需要人工介入但未到高危投诉
        self.level2_words = {
            # 明确的不满表达
            "非常不满", "强烈不满", "极其不满", "太失望了",
            "非常失望", "严重不满", "很不满意", "非常不满意",

            # 要求赔偿/退款
            "要求赔偿", "要求退款", "要求退货", "要求补偿",
            "要求赔偿损失", "要求精神损失费", "要求经济赔偿",

            # 投诉升级
            "要投诉", "必须投诉", "一定要投诉", "坚决投诉",
            "向客服投诉", "向总部投诉", "向消协投诉",

            # 快递中危词
            "快递丢失", "快递严重延误", "快递破损",
            "货物损坏", "包裹丢失", "快件丢失",

            # 服务中危词
            "服务态度差", "服务恶劣", "态度恶劣", "服务敷衍",
            "不专业", "不负责任", "互相推诿", "踢皮球",

            # 质量问题中危词
            "质量问题严重", "产品损坏", "严重质量问题",
            "产品变质", "商品破损"
        }

        # L3 高危敏感词 - 最高级别，需要投诉专员立即处理
        self.level3_words = {
            # 极端负面情绪
            "垃圾", "废物", "一无是处", "烂透了", "最差的服务",
            "丧心病狂", "毫无职业道德", "道德败坏",

            # 明确威胁
            "我要曝光", "我要媒体曝光", "我要上新闻",
            "我要发帖", "我要发微博", "我要发抖音",
            "我要举报", "我要报警", "我要起诉",
            "我要告你们", "我要找律师", "我要315投诉",

            # 涉及违法
            "诈骗", "欺诈行为", "违法经营", "非法集资",
            "偷税漏税", "虚假宣传", "商业欺诈",

            # 侮辱威胁
            "要打", "要揍", "要杀人", "要报复",
            "你给我等着", "小心点", "不会放过你",

            # 极度不满升级
            "不可原谅", "无法容忍", "忍无可忍",
            "天理不容", "良心被狗吃了", "黑心商家",
            "奸商", "骗子", "假冒伪劣"
        }

        # 更新敏感词到等级的映射
        self._update_word_mapping()

        # 初始化L1安抚话术模板
        self._initialize_comfort_templates()

    def _update_word_mapping(self):
        """更新敏感词到等级的映射"""
        self.word_to_level.clear()
        for word in self.level1_words:
            self.word_to_level[word] = SensitivityLevel.L1_LOW.value
        for word in self.level2_words:
            self.word_to_level[word] = SensitivityLevel.L2_MEDIUM.value
        for word in self.level3_words:
            self.word_to_level[word] = SensitivityLevel.L3_HIGH.value

    def _initialize_comfort_templates(self):
        """初始化L1安抚话术模板"""
        self.l1_comfort_templates = [
            "非常理解您现在的心情，您的反馈我们已经记录下来，会尽快改进相关服务。",
            "让您遇到这样的问题，我们深感抱歉。我们会认真对待您的反馈，努力提升服务质量。",
            "非常感谢您的耐心反馈，我们理解这给您带来了不便，我们会立即跟进处理。",
            "您说的问题我们高度重视，已经安排专人跟进，希望能够尽快为您解决问题。",
            "理解您焦急的心情，我们正在加紧处理中，请您耐心等待，感谢您的理解和支持。"
        ]

    def add_word(self, word: str, level: int) -> bool:
        """
        添加敏感词到指定等级

        Args:
            word: 敏感词
            level: 等级 (1=L1, 2=L2, 3=L3)

        Returns:
            是否添加成功
        """
        if level == SensitivityLevel.L1_LOW.value:
            if word not in self.level3_words and word not in self.level2_words:
                self.level1_words.add(word)
                self.word_to_level[word] = level
                return True
        elif level == SensitivityLevel.L2_MEDIUM.value:
            if word not in self.level3_words:
                self.level2_words.add(word)
                self.word_to_level[word] = level
                return True
        elif level == SensitivityLevel.L3_HIGH.value:
            self.level3_words.add(word)
            self.word_to_level[word] = level
            return True
        return False

    def remove_word(self, word: str) -> bool:
        """
        从知识库中移除敏感词

        Args:
            word: 敏感词

        Returns:
            是否移除成功
        """
        if word in self.level1_words:
            self.level1_words.remove(word)
        elif word in self.level2_words:
            self.level2_words.remove(word)
        elif word in self.level3_words:
            self.level3_words.remove(word)
        else:
            return False

        if word in self.word_to_level:
            del self.word_to_level[word]
        return True

    def get_level(self, word: str) -> Optional[int]:
        """
        获取敏感词的等级

        Args:
            word: 敏感词

        Returns:
            等级 (1, 2, 3) 或 None
        """
        return self.word_to_level.get(word)

    def check_word(self, word: str) -> Tuple[bool, int]:
        """
        检查词语是否为敏感词

        Args:
            word: 待检查的词语

        Returns:
            (是否为敏感词, 等级)
        """
        level = self.get_level(word)
        if level:
            return True, level
        return False, 0

    def get_comfort_template(self) -> str:
        """
        获取L1安抚话术模板

        Returns:
            随机选择的安抚话术
        """
        import random
        return random.choice(self.l1_comfort_templates)

    def get_all_words_by_level(self, level: int) -> Set[str]:
        """
        获取指定等级的所有敏感词

        Args:
            level: 等级

        Returns:
            敏感词集合
        """
        if level == SensitivityLevel.L1_LOW.value:
            return self.level1_words.copy()
        elif level == SensitivityLevel.L2_MEDIUM.value:
            return self.level2_words.copy()
        elif level == SensitivityLevel.L3_HIGH.value:
            return self.level3_words.copy()
        return set()

    def get_level_name(self, level: int) -> str:
        """
        获取等级名称

        Args:
            level: 等级

        Returns:
            等级名称
        """
        if level == SensitivityLevel.L1_LOW.value:
            return "L1低危"
        elif level == SensitivityLevel.L2_MEDIUM.value:
            return "L2中危"
        elif level == SensitivityLevel.L3_HIGH.value:
            return "L3高危"
        return "未知"

    def get_action_by_level(self, level: int) -> str:
        """
        获取指定等级的处理动作

        Args:
            level: 等级

        Returns:
            处理动作描述
        """
        if level == SensitivityLevel.L1_LOW.value:
            return "普通安抚，打上不满意标签"
        elif level == SensitivityLevel.L2_MEDIUM.value:
            return "跳过意图识别，转入普通客服列表"
        elif level == SensitivityLevel.L3_HIGH.value:
            return "直接终止流程，转入投诉专员列表"
        return "正常流程"


# 创建全局敏感词知识库实例
sensitivity_knowledge_base = SensitivityKnowledgeBase()