# -*- coding: utf-8 -*-
"""
敏感词过滤：输入守卫（三级）+ 输出打码（一次性/流式）。

词库两层：
- 业务情绪词 L1/L2/L3（agents/sensitive_words.py 手工维护）——L1/L2 是抱怨/诉求信号，放行给业务智能体；
- 内容合规词库（skills/lexicons/*.txt，源自开源 konsheng/Sensitive-lexicon，见该目录 SOURCE.md）
  ——政治/暴恐/色情/广告导流，一律按 L3 处理。

匹配引擎：优先 pyahocorasick（C 实现的 AC 自动机，pip 依赖）；
未安装时自动降级为纯 Python 的 str.find 扫描，接口不变（演示环境缺包也能跑）。

职责：
1. 输入侧：L3 命中 → 直接拦截（零 LLM 调用）；L1/L2 → 放行；
2. 输出侧：智能体回复中的 L3/合规词打码（mask_sensitive / StreamMasker），
   支持跨 token 的流式场景（词被拆在多个 token 里也能完整打码）。
"""

from pathlib import Path
from typing import Dict, List, Tuple

from agents.sensitive_words import sensitivity_knowledge_base

try:
    import ahocorasick  # pyahocorasick
    _HAS_AHOCORASICK = True
except ImportError:  # pragma: no cover - 演示环境缺包时降级
    _HAS_AHOCORASICK = False

LEXICON_DIR = Path(__file__).parent / "lexicons"
MASK_CHAR = "*"


# ---------------------------------------------------------------- 词库装载

def _load_compliance_words() -> Dict[str, str]:
    """加载合规词库文件（词 → 类别）。跳过纯 ASCII 单字符与含空白的脏数据。"""
    words: Dict[str, str] = {}
    if not LEXICON_DIR.exists():
        return words
    for f in sorted(LEXICON_DIR.glob("*.txt")):
        category = f.stem
        for line in f.read_text(encoding="utf-8").splitlines():
            w = line.strip()
            if not w or len(w) > 50 or (" " in w or "\t" in w):
                continue
            if len(w) == 1 and w.isascii():
                continue
            words[w] = category
    return words


_COMPLIANCE_WORDS = _load_compliance_words()

# word -> 等级（1/2/3）；合规词一律 L3，且覆盖同形情绪词
_LEVEL_BY_WORD: Dict[str, int] = dict(sensitivity_knowledge_base.word_to_level)
_LEVEL_BY_WORD.update({w: 3 for w in _COMPLIANCE_WORDS})

# 打码只针对 L3 及以上；缓冲保留长度 = 最长 L3 词长 - 1
_MASK_HOLDBACK = max((len(w) for w, lvl in _LEVEL_BY_WORD.items() if lvl >= 3), default=1) - 1


# ---------------------------------------------------------------- 匹配引擎

if _HAS_AHOCORASICK:
    _automaton = ahocorasick.Automaton()
    for _w, _lvl in _LEVEL_BY_WORD.items():
        _automaton.add_word(_w, (_w, _lvl))
    _automaton.make_automaton()


def _find_spans(text: str) -> List[Tuple[int, int, int]]:
    """返回全部命中 [(start, end_exclusive, level), ...]。"""
    if not text:
        return []
    spans: List[Tuple[int, int, int]] = []
    if _HAS_AHOCORASICK:
        for end, (w, lvl) in _automaton.iter(text):
            spans.append((end - len(w) + 1, end + 1, lvl))
    else:  # 降级：逐词 str.find，O(词数×文本长)，演示规模可接受
        for w, lvl in _LEVEL_BY_WORD.items():
            start = 0
            while True:
                i = text.find(w, start)
                if i < 0:
                    break
                spans.append((i, i + len(w), lvl))
                start = i + 1
    return spans


# ---------------------------------------------------------------- 输入守卫

_BLOCK_RESPONSE = (
    "非常抱歉，您的消息中包含我们不便于处理的内容。"
    "如果您遇到了服务问题，请换一种方式描述具体问题，我们会认真对待并尽快为您解决。"
)


def run_sensitive_guard(query: str) -> Dict:
    """对用户输入做敏感词匹配，返回守卫结果。

    Returns:
        {
            "has_sensitive": bool,     # 是否命中敏感词（任意等级）
            "level": int,              # 最高命中等级（0~3）
            "matched_words": [str],
            "blocked": bool,           # L3 高危 → 拦截
            "response": str,           # 拦截时的话术，放行为 ""
        }
    """
    if not query:
        return {"has_sensitive": False, "level": 0, "matched_words": [],
                "blocked": False, "response": ""}

    spans = _find_spans(query)
    matched_words = []
    highest = 0
    for _s, _e, lvl in spans:
        highest = max(highest, lvl)
    for s, e, _lvl in spans:
        w = query[s:e]
        if w not in matched_words:
            matched_words.append(w)
    blocked = highest >= 3

    return {
        "has_sensitive": bool(spans),
        "level": highest,
        "matched_words": matched_words,
        "blocked": blocked,
        "response": _BLOCK_RESPONSE if blocked else "",
    }


# ---------------------------------------------------------------- 输出打码

def mask_sensitive(text: str, min_level: int = 3) -> str:
    """一次性打码：把 text 中命中的 min_level 及以上敏感词替换为等长 MASK_CHAR。"""
    if not text:
        return text
    chars = list(text)
    for s, e, lvl in _find_spans(text):
        if lvl >= min_level:
            for i in range(s, e):
                chars[i] = MASK_CHAR
    return "".join(chars)


class StreamMasker:
    """流式打码器：跨 token 缓冲，敏感词命中打码，普通文本近似实时透出。

    原理：每次 feed 把上轮保留的尾部（最长 L3 词长-1 个字符）与新 chunk 拼接后
    全量扫描，已确定的打码标记随字符向后传递，保证跨 token 的词也能整体命中；
    flush 时输出全部残留。holdback 只带来几个字符的显示延迟，肉眼无感。
    """

    def __init__(self):
        self._tail = ""          # 未发出的原始尾部
        self._tail_flags = []    # 与 _tail 对齐的打码标记

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        buf = self._tail + chunk
        flags = self._tail_flags + [False] * len(chunk)
        for s, e, lvl in _find_spans(buf):
            if lvl >= 3:
                for i in range(max(s, 0), min(e, len(flags))):
                    flags[i] = True
        if len(buf) <= _MASK_HOLDBACK:
            self._tail, self._tail_flags = buf, flags
            return ""
        cut = len(buf) - _MASK_HOLDBACK
        out = "".join(MASK_CHAR if m else c for c, m in zip(buf[:cut], flags[:cut]))
        self._tail = buf[cut:]
        self._tail_flags = flags[cut:]
        return out

    def flush(self) -> str:
        out = "".join(MASK_CHAR if m else c for c, m in zip(self._tail, self._tail_flags))
        self._tail, self._tail_flags = "", []
        return out
