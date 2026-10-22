"""W2 防御测试：free_writing 章节安全改写（我方替换 + 段首套话删除）。

职责: 验证 ``DeAIFilter.filter`` 的安全改写规则、例外保护与 HTML 结构保持；
 防止"我们 -> 公司名"误改写（公司名不可得场景）、段首套话注入
 ``[!AI:xxx]`` 污染 Word、误删事实点等回归。
不做: 真实 LLM 调用、Gate Pipeline、metrics 计数路径（``get_ai_phrase_count``
 已被 test_section_composer_progress_logging 等覆盖）。

历史教训映射:
 写标人员配置表杜撰真实姓名 + 段首编号丢失。
 本测试守护"改写不杜撰、不破坏 HTML 结构"两条底线。
"""

from __future__ import annotations

from app.core.writer.de_ai_filter import (
    AI_PHRASES,
    DeAIFilter,
    _replace_we_with_party,
)

# ---------------------------------------------------------------------------
# 1. 我们 → 我方 替换 + 例外保护
# ---------------------------------------------------------------------------


def test_replace_we_to_party_basic() -> None:
    text = "我们承诺按时交付项目。"
    assert _replace_we_with_party(text) == "我方承诺按时交付项目。"


def test_replace_we_preserves_exception_let_us() -> None:
    """让我们 / 使我们 等合法搭配中"我们"不应替换。"""
    text = "让我们共同推进项目，使我们的方案落地。"
    out = _replace_we_with_party(text)
    assert "让我们" in out
    assert "使我们" in out
    # 应保留两个例外搭配，且无替换
    assert "让我方" not in out
    assert "使我方" not in out


def test_replace_we_mixed_exception_and_normal() -> None:
    text = "我们将按计划推进，让我们一起努力，我们承诺质量。"
    out = _replace_we_with_party(text)
    assert "我方将按计划推进" in out
    assert "让我们一起" in out
    assert "我方承诺质量" in out


def test_replace_we_returns_text_when_no_match() -> None:
    text = "本公司承诺按时交付。"
    assert _replace_we_with_party(text) == text


def test_replace_we_idempotent() -> None:
    """对已经处理过的文本再次处理不应再改动。"""
    text = "我方承诺按时交付。"
    assert _replace_we_with_party(text) == text


# ---------------------------------------------------------------------------
# 2. 段首套话删除
# ---------------------------------------------------------------------------


def test_filter_removes_paragraph_lead_phrase() -> None:
    html = "<p>综上所述，本方案覆盖全流程。</p>"
    out = DeAIFilter().filter(html)
    assert "综上所述" not in out
    assert "<p>本方案覆盖全流程。</p>" == out


def test_filter_removes_lead_phrase_with_attributes() -> None:
    """<p class="x"> 形式的开标签也应支持。"""
    html = '<p class="indent">综上所述，本方案完善。</p>'
    out = DeAIFilter().filter(html)
    assert "综上所述" not in out
    assert '<p class="indent">' in out


def test_filter_keeps_mid_paragraph_lead_phrases() -> None:
    """段中出现的"综上所述"等不应删除（语义可能合法）。"""
    html = "<p>项目分三阶段实施，综上所述，整体周期 6 个月。</p>"
    out = DeAIFilter().filter(html)
    # 段首没有，段中保留
    assert "综上所述" in out


def test_filter_handles_multiple_paragraphs() -> None:
    html = (
        "<p>综上所述，方案完整。</p>"
        "<p>众所周知，质量第一。</p>"
        "<p>本方案覆盖。</p>"
    )
    out = DeAIFilter().filter(html)
    assert "综上所述" not in out
    assert "众所周知" not in out
    assert "<p>方案完整。</p>" in out
    assert "<p>质量第一。</p>" in out
    assert "<p>本方案覆盖。</p>" in out


# ---------------------------------------------------------------------------
# 3. HTML 结构与事实点保护（不污染 Word 交付物）
# ---------------------------------------------------------------------------


def test_filter_does_not_inject_marker_tokens() -> None:
    """不应再使用 [!AI:xxx] 标记污染输出。"""
    html = "<p>我方将切实保障项目质量，全面提升服务水平。</p>"
    out = DeAIFilter().filter(html)
    assert "[!AI:" not in out
    # 段中套话保留原样（仅 metrics 计数 + Gate warn，不删）
    assert "切实保障" in out
    assert "全面提升" in out


def test_filter_preserves_html_structure_tags() -> None:
    html = (
        "<h4>方案概述</h4>"
        "<p>我方承诺按时交付。</p>"
        "<ul><li>第一项</li><li>第二项</li></ul>"
        "<table><tr><td>A</td><td>B</td></tr></table>"
    )
    out = DeAIFilter().filter(html)
    assert "<h4>方案概述</h4>" in out
    assert "<ul>" in out and "</ul>" in out
    assert "<table>" in out and "</table>" in out
    assert "<li>第一项</li>" in out


def test_filter_preserves_placeholders_and_figures() -> None:
    """[待补充:xxx] 占位符与 figure 图片引用必须原样保留。"""
    html = (
        "<p>项目经理为 [待补充:姓名]，工作 [待补充:年限] 年。</p>"
        '<figure data-image-id="42"><figcaption>图1</figcaption></figure>'
    )
    out = DeAIFilter().filter(html)
    assert "[待补充:姓名]" in out
    assert "[待补充:年限]" in out
    assert 'data-image-id="42"' in out


def test_filter_returns_empty_for_empty_input() -> None:
    assert DeAIFilter().filter("") == ""
    assert DeAIFilter().filter(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. metrics 通道向后兼容（AI_PHRASES 与 get_ai_phrase_count）
# ---------------------------------------------------------------------------


def test_ai_phrases_list_unchanged_for_metrics() -> None:
    """AI_PHRASES 长度保持原样，避免 metrics 阈值漂移。"""
    assert "综上所述" in AI_PHRASES
    assert "切实保障" in AI_PHRASES
    # 长度 22 来自 P1.4 原实现
    assert len(AI_PHRASES) == 22


def test_get_ai_phrase_count_still_works() -> None:
    text = "<p>我方将切实保障质量，致力于高效协同。</p>"
    assert DeAIFilter().get_ai_phrase_count(text) >= 3
