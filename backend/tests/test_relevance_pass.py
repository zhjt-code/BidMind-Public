"""P3/P4 防御测试: 评审意义独立判定 pass。

覆盖合成优先级(评分项铁律 > 否决项双确认降级 > LLM/规则并集折叠)、分值正则、
LLM 失败回退(否决项保守保持 actionable)。 起否决项不再无条件豁免:
LLM 证据载体判非投标文件 且 行为性词表双确认时折叠为 procedural(风险告知)。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from tests.synthetic_data import synthetic_text

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.relevance.behavioral_disqualification import (  # noqa: E402
    matches_behavioral_disqualification,
)
from app.core.extractor.relevance.relevance_classifier import (  # noqa: E402
    _parse_inline_score,
    classify_review_relevance,
)
from app.schemas.requirement import ExtractedRequirement  # noqa: E402

pytestmark = pytest.mark.scoring


# ---------------------------------------------------------------------------
# 行为性否决词表地板
# ---------------------------------------------------------------------------


def test_behavioral_floor_covers_1527_vocabulary_gap():
    # 既有词表缺口: 挂靠/转包/行贿/虚报业绩/代为投标 必须命中
    assert matches_behavioral_disqualification("禁止挂靠、转包") is True
    assert matches_behavioral_disqualification("以行贿手段谋取中标") is True
    assert matches_behavioral_disqualification("虚报业绩、代为投标") is True
    assert matches_behavioral_disqualification("以他人名义投标") is True


def test_behavioral_floor_content_exemption_wins():
    # 内容性信号强制保留: ★/未提供/超限价/负偏离/资料不齐
    assert matches_behavioral_disqualification("★禁止串通投标,未提供承诺函的废标") is False
    assert matches_behavioral_disqualification("串通投标或报价超过最高限价的无效") is False
    assert matches_behavioral_disqualification("弄虚作假;资料不齐全的为无效投标") is False


def test_behavioral_floor_negative_on_normal_requirements():
    assert matches_behavioral_disqualification("投标人须具备一级资质") is False
    assert matches_behavioral_disqualification("") is False
    assert matches_behavioral_disqualification(None) is False


class FakeLLM:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        result = self.scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _req(category, description, is_mandatory=False):
    return ExtractedRequirement(
        category=category, description=description, is_mandatory=is_mandatory
    )


# ---------------------------------------------------------------------------
# 分值正则
# ---------------------------------------------------------------------------


def test_parse_inline_score_picks_max_band():
    ok, mx = _parse_inline_score("工期90天内完成得6分,90-120天得3分,120天以上得1分")
    assert ok is True and mx == 6.0


def test_parse_inline_score_none_when_no_score():
    assert _parse_inline_score("投标报价不得超过最高限价") == (False, None)


def test_parse_inline_score_ignores_large_amounts():
    # "得分四舍五入" 无数字+分; 金额"元"不计
    assert _parse_inline_score("评分结果四舍五入保留两位小数") == (False, None)


def test_parse_inline_score_rejects_duration_words_bug607():
    """: "20分钟"是时长不是分值, "加密"不是评分动作, 词级双误判必须根除。"""
    # 项目1584 id=23907 真实误标句(CA证书/不见面开标条款)
    assert _parse_inline_score(
        "供应商须通过日照市公共资源交易网凭CA数字证书或标证通APP下载采购文件，"
        "使用响应文件制作工具制作加密电子响应文件并上传；开标采取不见面方式，"
        "须在投标截止时间后20分钟内使用对应CA锁或标证通APP完成远程解密，"
        "否则视为放弃并按无效投标处理。"
    ) == (False, None)
    # 时长/单位复合词不是分值
    assert _parse_inline_score("响应时间不超过30分钟") == (False, None)
    assert _parse_inline_score("噪声不高于60分贝") == (False, None)
    assert _parse_inline_score("成功率不低于百分之95，处理时限15分钟") == (False, None)
    # 数字与评分动作词分离在不相干位置时不得拼接判定
    assert _parse_inline_score("须加盖公章并于开标后20分钟内送达") == (False, None)


def test_parse_inline_score_accepts_sentence_level_score_bands_bug607():
    """句子级评分表达(动作词紧邻数字分)必须保持命中。"""
    assert _parse_inline_score("每提供一项符合要求的证书得10分，本条满分30分。") == (True, 30.0)
    assert _parse_inline_score("每出现一处瑕疵扣2分，扣完为止") == (True, 2.0)
    assert _parse_inline_score("列入名录的加5分") == (True, 5.0)
    assert _parse_inline_score("本项最高15分") == (True, 15.0)
    assert _parse_inline_score("技术评审采用百分制，价格部分30分封顶") == (True, 30.0)


def test_pass_scoring_zero_max_score_falls_back_to_null():
    # LLM 判 is_scoring 但 max_score=0(资格性/无分值说明项) -> score_weight 落 null 而非 0
    items = [_req("QUAL", "供应商资格: 提供符合招标文件要求的承诺函")]
    llm = FakeLLM([[{"idx": 0, "relevance": "actionable", "is_scoring": True, "max_score": 0}]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].is_key_review is True
    assert out[0].score_weight is None
    assert _parse_inline_score("罚款50000元") == (False, None)


# ---------------------------------------------------------------------------
# 合成优先级
# ---------------------------------------------------------------------------


def test_pass_combines_priority():
    items = [
        _req("QUAL", "投标人须具备建筑工程施工总承包一级资质"),       # 0 actionable
        _req("COMM", synthetic_text('招标人信息:某公司,联系电话', '029-', '1234', '5678')),       # 1 LLM informational
        _req("COMM", "投标人不得串通投标围标陪标", is_mandatory=True), # 2 否决项双确认折叠
        _req("TECH", "工期90天内完成得6分,90-120天得3分"),            # 3 评分项
        _req("COMM", "本项目专门面向中小企业采购"),                   # 4 规则地板
    ]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "actionable", "locus": "bid_content", "is_scoring": False, "max_score": None},
        {"idx": 1, "relevance": "informational", "locus": "notice", "is_scoring": False, "max_score": None},
        {"idx": 2, "relevance": "procedural", "locus": "behavior", "is_scoring": False, "max_score": None},
        {"idx": 3, "relevance": "actionable", "locus": "bid_content", "is_scoring": True, "max_score": 6},
        {"idx": 4, "relevance": "actionable", "locus": "bid_content", "is_scoring": False, "max_score": None},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))

    assert out[0].review_relevance == "actionable"
    assert out[1].review_relevance == "informational"
    # 否决项双确认降级: LLM 载体 behavior + 词表命中"串通" -> 折叠为风险告知
    assert out[2].review_relevance == "procedural"
    assert out[2].is_mandatory is True  # 否决语义保留, 前端进独立风险分组
    # 评分相关: actionable + 重点; 起正文不再赋 score_weight(满分仅来自评分表)
    assert out[3].review_relevance == "actionable"
    assert out[3].is_key_review is True
    assert out[3].score_weight is None
    assert out[3].scoring_metadata and out[3].scoring_metadata.get("source") == "inline"
    # 规则地板: LLM 判 actionable, 但"面向中小企业"命中规则 -> informational
    assert out[4].review_relevance == "informational"


def test_mandatory_fold_requires_both_wings():
    # 单翼不折叠: LLM 判 bid_content(可评审) 时, 即使词表命中也保持 actionable
    items = [_req("COMM", "投标人不得串通投标", is_mandatory=True)]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "actionable", "locus": "bid_content", "is_scoring": False, "max_score": None},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "actionable"


def test_mandatory_fold_blocked_by_content_exemption():
    # 内容性例外防误杀: 行为性词 + "未提供材料"内核 -> 保持 actionable
    items = [
        _req(
            "QUAL",
            "★禁止弄虚作假,投标人未按要求提供资格证明材料的作废标处理",
            is_mandatory=True,
        )
    ]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "procedural", "locus": "behavior", "is_scoring": False, "max_score": None},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "actionable"


def test_mandatory_fold_disabled_by_flag(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TENDER_BEHAVIORAL_MANDATORY_FOLD_ENABLED", False)
    items = [_req("COMM", "投标人不得串通投标围标", is_mandatory=True)]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "procedural", "locus": "behavior", "is_scoring": False, "max_score": None},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "actionable"


def test_mandatory_scoring_item_never_folds():
    # 评分项铁律: 含分值的否决项走评分分支, 即使词表/载体双命中也不折叠
    items = [
        _req("TECH", "禁止串通投标;工期评分:90天内完成得6分", is_mandatory=True)
    ]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "procedural", "locus": "behavior", "is_scoring": True, "max_score": 6},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "actionable"
    assert out[0].is_key_review is True


def test_mandatory_fold_llm_wing_without_locus_falls_back_to_relevance():
    # 兼容旧版 Prompt 无 locus: LLM relevance 判折叠即视为 LLM 翼成立
    items = [_req("COMM", "投标人不得围标、陪标", is_mandatory=True)]
    llm = FakeLLM([[
        {"idx": 0, "relevance": "procedural", "is_scoring": False, "max_score": None},
    ]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "procedural"


def test_pass_scoring_detected_by_regex_even_if_llm_misses():
    items = [_req("TECH", "实验方案介绍详细得15分,缺一项扣2分")]
    llm = FakeLLM([[{"idx": 0, "relevance": "actionable", "is_scoring": False, "max_score": None}]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    # 正文正则仍识别"含分值评分相关"→标记重点; 但 起不再赋 score_weight
    assert out[0].is_key_review is True
    assert out[0].score_weight is None


def test_pass_llm_failure_falls_back_to_rules():
    items = [
        _req("COMM", "投标人不得串通投标", is_mandatory=True),  # 否决豁免
        _req("COMM", "本项目专门面向中小企业采购"),            # 规则 informational
        _req("TECH", "工期完成得6分"),                          # 正则评分项
        _req("QUAL", "投标人须具备一级资质"),                   # 默认 actionable
    ]
    llm = FakeLLM([RuntimeError("llm down")])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    assert out[0].review_relevance == "actionable"
    assert out[1].review_relevance == "informational"
    assert out[2].is_key_review is True and out[2].score_weight is None
    assert out[3].review_relevance == "actionable"


def test_pass_format_category_rule_floor():
    items = [_req("FORMAT", "投标文件正文字体为小四号宋体,标题为小三号黑体")]
    llm = FakeLLM([[{"idx": 0, "relevance": "actionable", "is_scoring": False, "max_score": None}]])
    out = asyncio.run(classify_review_relevance(items, llm, batch_size=10))
    # FORMAT 字体排版 -> procedural(按类别规则地板)
    assert out[0].review_relevance == "procedural"


def test_pass_reports_progress_per_completed_batch():
    """: 评审意义按批完成回调, 顺序为完成序而非启动序。"""
    items = [_req("QUAL", f"投标人须满足招标要求条目{i:02d}") for i in range(4)]
    llm = FakeLLM([
        [{"idx": 0, "relevance": "actionable"}, {"idx": 1, "relevance": "actionable"}],
        [{"idx": 0, "relevance": "actionable"}, {"idx": 1, "relevance": "actionable"}],
    ])
    seen: list[tuple[int, int]] = []

    def spy(done: int, total: int) -> None:
        seen.append((done, total))

    out = asyncio.run(
        classify_review_relevance(
            items, llm, batch_size=2, concurrency=1, progress_callback=spy,
        )
    )
    assert len(out) == 4
    assert seen == [(1, 2), (2, 2)]


def test_pass_uses_settings_batch_size_and_concurrency(monkeypatch):
    """批大小与并发从 settings 回落；批大小下限为 5。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TENDER_RELEVANCE_BATCH_SIZE", 5)
    monkeypatch.setattr(settings, "TENDER_RELEVANCE_CONCURRENCY", 2)
    items = [_req("QUAL", f"投标人须满足招标要求条目{i:02d}") for i in range(10)]
    llm = FakeLLM([
        [{"idx": i, "relevance": "actionable"} for i in range(5)],
        [{"idx": i, "relevance": "actionable"} for i in range(5)],
    ])
    seen: list[tuple[int, int]] = []

    def spy(done: int, total: int) -> None:
        seen.append((done, total))

    out = asyncio.run(classify_review_relevance(items, llm, progress_callback=spy))
    assert len(out) == 10
    assert seen == [(1, 2), (2, 2)]
    assert len(llm.calls) == 2
