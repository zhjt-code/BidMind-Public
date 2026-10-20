"""W2: 去AI味后处理（free_writing 章节生成后安全改写交付物）。

职责: 对 free_writing 章节正文做"安全改写" — 第一人称统一为"我方"、
      删除段首语篇引导套话；保持 HTML 结构与事实点不变；其余套话维持
      metrics 计数 + Gate warn。
不做: 标记式注入 [!AI:xxx]（会污染 Word 交付物）、跨段落语义改写、
      事实/数据/承诺类删改、data_fill / fixed_template 路径处理。

设计修正 (相对 P1.4 原 docstring):
    - 原方案"我们 -> 公司名称"在写标流程不可得（``{公司名称}`` 本身即为
      待补充占位符），改为"我们 -> 我方"，与标书规范称谓对齐，无外部依赖；
    - 不再注入 ``[!AI:xxx]`` 文本标记，避免污染 Word 交付物；
    - 段首套话仅在 ``<p>`` 段起始位置删除，正文中间出现的不动以防误伤。
"""

import re

from loguru import logger

# 用于 metrics 计数的完整套话清单（保持向后兼容）
AI_PHRASES = [
    "综上所述", "切实保障", "高度重视", "全面提升", "高效协同",
    "令人瞩目", "致力于", "不仅...而且", "首先...其次...最后",
    "旨在", "本方案", "通过本方案", "确保了", "充分体现",
    "总而言之", "值得注意的是", "显而易见", "毋庸置疑",
    "不言而喻", "有目共睹", "众所周知", "与此同时",
]

# W2: 段首语篇引导套话清单（仅段首位置安全删除）
_PARAGRAPH_LEAD_PHRASES: tuple[str, ...] = (
    "综上所述", "总而言之", "一言以蔽之", "由此可见",
    "值得注意的是", "众所周知", "毋庸置疑", "显而易见",
    "不言而喻", "有目共睹", "不难看出",
)

# W2: 段首套话删除正则：匹配 <p> / <p ...> / <h4> / <h5> 起始位置紧跟套话+逗号/、/。
# 示例: "<p>综上所述，本方案……" -> "<p>本方案……"
_LEAD_PHRASE_RE = re.compile(
    r"(<(?:p|h[1-6])(?:\s[^>]*)?>)(?:" + "|".join(re.escape(p) for p in _PARAGRAPH_LEAD_PHRASES) + r")[，。、,;:：]\s*",
)

# W2: "我们" 例外模式 — 这些组合中的"我们"是合法搭配，不做替换。
_WE_EXCEPTIONS = ("让我们", "使我们", "为我们", "对我们", "我们国家", "我们党")

_RHYTHM_WINDOW = 3
_RHYTHM_TOLERANCE = 0.1


def _replace_we_with_party(text: str) -> str:
    """把"我们"安全替换为"我方"，保留例外搭配。"""
    if "我们" not in text:
        return text

    result_chars: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("我们", i):
            # 例外检测：以"我们"为后缀的搭配（让/使/为/对 + 我们）
            hit_exception = False
            for excp in _WE_EXCEPTIONS:
                start = i + 2 - len(excp)
                if start >= 0 and text.startswith(excp, start):
                    hit_exception = True
                    break
            if hit_exception:
                result_chars.append(text[i:i + 2])
            else:
                result_chars.append("我方")
            i += 2
        else:
            result_chars.append(text[i])
            i += 1
    return "".join(result_chars)


class DeAIFilter:
    """W2: free_writing 章节生成后的安全改写器 + 套话 metrics。

    生产闭环用法：
        - ``filter()`` 在 free_writing_route 接线，受
          ``settings.WRITING_DE_AI_REWRITE_ENABLED`` 开关控制；
        - ``get_ai_phrase_count`` 继续作为 metrics 入口，由
          ``section_composer._observe_ai_phrases`` 调用，与 Gate 联动。
    """

    def filter(self, text: str) -> str:
        """W2: 安全改写 — 我们→我方 + 段首套话删除。

        Args:
            text: free_writing 生成并 normalize_section_html 后的 HTML 内容。

        Returns:
            改写后的 HTML；输入为空或开关关闭时调用方应跳过本函数。
        """
        if not text:
            return text

        result = _replace_we_with_party(text)

        # 删除段首语篇引导套话（保留 HTML 起始标签）
        result, removed = _LEAD_PHRASE_RE.subn(r"\1", result)
        if removed:
            logger.debug(f"[去AI味] 删除 {removed} 处段首套话")

        return result

    def _check_paragraph_rhythm(self, text: str) -> list[str]:
        """检查连续段落长度是否过于均匀

        连续 _RHYTHM_WINDOW 段以上长度差异 < _RHYTHM_TOLERANCE 则标记。
        """
        paragraphs = re.split(r"\n\s*\n|</?p>|<br\s*/?>", text)
        paragraphs = [p.strip() for p in paragraphs if len(p.strip()) > 20]

        if len(paragraphs) < _RHYTHM_WINDOW:
            return []

        marks: list[str] = []
        for i in range(len(paragraphs) - _RHYTHM_WINDOW + 1):
            window = paragraphs[i:i + _RHYTHM_WINDOW]
            lengths = [len(p) for p in window]
            avg = sum(lengths) / len(lengths)
            if avg == 0:
                continue
            max_deviation = max(abs(length - avg) / avg for length in lengths)
            if max_deviation < _RHYTHM_TOLERANCE:
                marks.append(
                    f"段落{i + 1}-{i + _RHYTHM_WINDOW}: "
                    f"长度{lengths}，差异{max_deviation:.1%}"
                )

        return marks

    def get_ai_phrase_count(self, text: str) -> int:
        """统计文本中的AI套话数量（不修改文本）"""
        if not text:
            return 0
        return sum(1 for phrase in AI_PHRASES if phrase in text)
