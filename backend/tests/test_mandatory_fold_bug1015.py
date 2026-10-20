""": 否决项双确认降级的规则翼泛化为全部规则地板。

形态: 低置信上游旗标(is_mandatory)被下游当不可推翻覆盖轴。
夹具: 程序性(FORMAT 物理形态) / 告知性(招标人资金信息) / 行为性(串通投标, 既有路径);
 反例: 真实否决项在 LLM 误判载体或规则未命中时保持 actionable。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.relevance.relevance_classifier import _decide_updates  # noqa: E402
from app.schemas.requirement import ExtractedRequirement  # noqa: E402


def _mandatory(description: str, category: str = "QUAL") -> ExtractedRequirement:
    return ExtractedRequirement(
        category=category,
        description=description,
        is_mandatory=True,
        keywords=[],
        source_pages=[6],
    )


def test_mandatory_procedural_form_folds_with_rule_floor_and_llm_locus():
    """FORMAT 物理形态 + LLM 判程序载体 -> procedural/rule_floor(此前只认行为性词表)。"""
    item = _mandatory("投标文件须胶装并密封包装递交，否则投标无效", category="FORMAT")
    update = _decide_updates(item, {"locus": "procedure", "relevance": "procedural"})
    assert update == {"review_relevance": "procedural", "relevance_reason": "rule_floor"}


def test_mandatory_informational_form_folds_second_entity():
    """同形态第二例: 招标人资金信息被误标否决 + LLM 判告知载体 -> informational。"""
    item = _mandatory("项目资金来源为成本，出资比例100%，资金已落实", category="COMM")
    update = _decide_updates(item, {"locus": "notice", "relevance": "informational"})
    assert update == {"review_relevance": "informational", "relevance_reason": "rule_floor"}


def test_mandatory_behavioral_path_unchanged():
    item = _mandatory("投标人不得相互串通投标，一经查实否决其投标", category="COMM")
    update = _decide_updates(item, {"locus": "behavior"})
    assert update == {
        "review_relevance": "procedural",
        "relevance_reason": "behavioral_mandatory",
    }


def test_real_mandatory_stays_actionable_without_rule_floor():
    """LLM 单翼(误判载体)不足以折叠真实否决项。"""
    item = _mandatory("投标人须提供有效营业执照副本，否则投标无效")
    assert _decide_updates(item, {"locus": "procedure"}) == {"review_relevance": "actionable"}


def test_content_kernel_blocks_every_rule_floor_not_only_behavioral():
    """混合条款: 程序外壳词命中噪音规则, 但含可核验内核(未提交材料)则不折叠(例外泛化)。"""
    item = _mandatory("投标文件须胶装密封递交；未提交营业执照副本的为无效投标", category="FORMAT")
    assert _decide_updates(item, {"locus": "procedure", "relevance": "procedural"}) == {
        "review_relevance": "actionable"
    }


def test_real_mandatory_stays_actionable_without_llm_wing():
    """规则单翼(物理形态词命中)但 LLM 判投标文件载体, 仍不折叠。"""
    item = _mandatory("投标文件须胶装并密封包装递交，否则投标无效", category="FORMAT")
    assert _decide_updates(item, {"locus": "bid_document"}) == {"review_relevance": "actionable"}
    assert _decide_updates(item, None) == {"review_relevance": "actionable"}
