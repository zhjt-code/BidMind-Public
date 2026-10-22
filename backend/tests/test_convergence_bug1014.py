""": 多提取源汇入同一收敛栈; 等价与页码落在不可伪造轴。

形态: 后置写入源各自落库 + 跨源等价靠 LLM 改写文本的字符相似度 + 页码信任模型自报。
夹具: 否决召回(联合体 / 评审段) 、评分归并(技术路线 / 人员职称) 、展示折叠(equivalent / covers)。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.scoring

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.mandatory.apply_coverage import recall_mandatory_conditions  # noqa: E402
from app.core.extractor.mandatory.atomic_extract import anchor_pages_to_text  # noqa: E402
from app.core.extractor.mandatory.contracts import PageText  # noqa: E402
from app.core.extractor.scoring import scoring_reconcile  # noqa: E402
from app.models.requirement import Requirement, RequirementCategory  # noqa: E402
from app.schemas.requirement import ExtractedRequirement  # noqa: E402
from app.services.requirement_grouping import group_requirements_for_display  # noqa: E402

# ----------------------------------------------------------------------------
# 原子条件页码锚定: 模型自报页码必须被原文命中推翻
# ----------------------------------------------------------------------------

_P6 = PageText(page_num=6, text="投标须知前附表\n5 资金来源 成本\n6 出资比例 100%\n15 投标人资格条件 独立法人")
_P13 = PageText(
    page_num=13,
    text="评标委员会将审查其投标文件是否完整,是否有计算错误,要求的保证金是否已提供,"
    "文件是否恰当地签署,投标文件是否符合规定。不符合上述规定的投标一律作为废标处理。",
)
_P11 = PageText(page_num=11, text="六、投标保证金 该项目不需要缴纳投标保证金。七、业绩考察 本项目不进行业绩考察。")
_PAGE_MAP = {p.page_num: p for p in (_P6, _P11, _P13)}


def test_anchor_overrides_llm_page_when_text_lives_elsewhere():
    """3560 夹具: 「文件须恰当地签署」自报 P6, 原文在 P13。"""
    assert anchor_pages_to_text("文件须恰当地签署", (6,), (6, 11, 13), _PAGE_MAP) == (13,)


def test_anchor_second_entity_keeps_claimed_page_when_it_matches():
    """同形态第二例(保证金): 自报页与原文一致时保留自报页。"""
    assert anchor_pages_to_text("该项目不需要缴纳投标保证金", (11,), (6, 11, 13), _PAGE_MAP) == (11,)


def test_anchor_falls_back_to_window_when_no_page_matches():
    assert anchor_pages_to_text("完全不存在于任何页的条件描述文本", (6,), (6, 11), _PAGE_MAP) == (6,)
    assert anchor_pages_to_text("完全不存在于任何页的条件描述文本", (99,), (6, 11), _PAGE_MAP) == (6, 11)


# ----------------------------------------------------------------------------
# 否决召回改为内存态、合并前: 补提项是 ExtractedRequirement, 不落库
# ----------------------------------------------------------------------------


class _FakeLLM:
    """按窗口原文应答: 只有看到关键词的窗口才产出该条(模拟真实模型), 页码故意自报错。"""

    def __init__(self, keyword: str, items):
        self._keyword = keyword
        self._items = items

    async def chat_json(self, *, user_message: str = "", **_kwargs):
        return self._items if self._keyword in user_message else []


def _chunk(cid, page, content, chunk_type="paragraph", section=""):
    return SimpleNamespace(id=cid, page_num=page, content=content, chunk_type=chunk_type, section_path=section)


def _extracted(description, *, mandatory=False, category="QUAL", pages=(4,)):
    return ExtractedRequirement(
        category=category,
        description=description,
        is_mandatory=mandatory,
        keywords=["投标"],
        source_pages=list(pages),
    )


def test_recall_returns_in_memory_candidates_for_the_shared_merge_stack():
    chunks = [
        _chunk(1, 8, "第三章 投标无效\n有下列情形之一的，投标无效：\n（1）投标报价超过最高限价；", section="投标无效"),
        _chunk(2, 40, "★项目负责人须具备高级工程师职称，未提供职称证书的按废标处理。", section="技术规格"),
    ]
    extracted = [_extracted("投标报价超过最高限价的，投标无效", pages=(8,))]
    llm = _FakeLLM("高级工程师", [
        {"description": "项目负责人须具备高级工程师职称", "category": "STAFF", "source_pages": [8], "keywords": ["职称"]},
    ])
    seen: list[str] = []

    def _cb(stage, current, total, message, extra=None):
        seen.append(stage)

    updated, report = asyncio.run(
        recall_mandatory_conditions(chunks=chunks, extracted=extracted, llm_client=llm, progress_callback=_cb)
    )
    assert report.promoted_count == 1 and report.inserted_count == 1
    assert updated[0].is_mandatory is True
    inserted = updated[-1]
    assert isinstance(inserted, ExtractedRequirement)
    assert inserted.is_mandatory is True and inserted.category == "STAFF"
    assert inserted.source_pages == [40], "自报 P8 被原文命中推翻为 P40"
    assert set(seen) == {"mandatory_coverage"}
    assert len(extracted) == 1, "输入列表不被原地修改"


def test_recall_failure_is_fail_visible_and_keeps_input():
    """召回内部异常不阻断主流程: 原列表原样返回 + 告警可见(单窗 LLM 失败仍由窗内吞掉并继续)。"""
    chunks = [_chunk(1, 8, "投标无效\n有下列情形之一的，投标无效：\n（1）报价超过最高限价；", section="投标无效")]
    broken_input = [object()]  # 缺 description 等字段 -> 视图构建抛错 -> 守卫接管
    updated, report = asyncio.run(
        recall_mandatory_conditions(chunks=chunks, extracted=broken_input, llm_client=None)  # type: ignore[arg-type]
    )
    assert updated is broken_input
    assert report.inserted_count == 0 and report.promoted_count == 0
    assert report.warnings and "自动核验未完成" in report.warnings[0]


def test_recall_without_pages_is_noop():
    extracted = [_extracted("投标人须为独立法人")]
    updated, report = asyncio.run(recall_mandatory_conditions(chunks=[], extracted=extracted, llm_client=None))
    assert updated == extracted and report.inserted_count == 0


# ----------------------------------------------------------------------------
# 具体豁免优先于引用型模板句: 同对象的「按招标文件要求提交 X」折进「不要求 X」
# ----------------------------------------------------------------------------

from app.core.extractor.tender_extractor_parts.waiver_fold import (  # noqa: E402
    fold_delegations_into_waivers,
    is_delegating_clause,
    waiver_object,
)


def test_waiver_absorbs_delegating_template_clause_same_object():
    """3560 夹具(保证金): P11「不要求」吸收 P12-36 模板句, 来源页并集, 不再并列矛盾。"""
    waiver = _extracted("本项目不要求提交投标保证金。", mandatory=True, category="COMM", pages=(11,))
    template = _extracted(
        "须按招标文件要求提交投标保证金（或投标保函）及缴纳凭证/银行回单；未按要求提交的投标将被否决。",
        mandatory=True, category="COMM", pages=(12, 13, 14, 35, 36),
    )
    other = _extracted("投标报价须不高于最高限价271000元", mandatory=True, category="COMM", pages=(6,))
    result = fold_delegations_into_waivers([template, waiver, other])
    assert [r.description for r in result] == [waiver.description, other.description]
    assert result[0].source_pages == [11, 12, 13, 14, 35, 36]
    assert result[0].merged_count == 2
    assert result[0].is_mandatory is True, "折叠只并来源, 不改豁免项自身标记"


def test_waiver_second_entity_performance_inspection():
    """同形态第二例(业绩考察): 「不进行业绩考察」吸收「按招标文件要求接受业绩考察」。"""
    waiver = _extracted("本项目招标阶段不进行投标人业绩考察。", category="PERF", pages=(11,))
    template = _extracted("投标人须按招标文件要求接受招标人组织的业绩考察。", category="PERF", pages=(13,))
    assert waiver_object(waiver.description) == "业绩考察"
    result = fold_delegations_into_waivers([waiver, template])
    assert len(result) == 1 and result[0].source_pages == [11, 13]


def test_waiver_fold_guards_prohibition_numbers_and_category():
    """不接受(禁止极性)不是豁免; 带数字事实的引用句不折; 跨类别不折。"""
    assert waiver_object("本项目不接受联合体投标") == ""
    assert not is_delegating_clause("须按招标文件要求提交投标保证金50000元")
    waiver = _extracted("本项目不要求提交投标保证金。", category="COMM", pages=(11,))
    numeric = _extracted("须按招标文件要求提交投标保证金50000元", category="COMM", pages=(12,))
    cross = _extracted("须按招标文件要求提交投标保证金缴纳凭证", category="FORMAT", pages=(12,))
    assert len(fold_delegations_into_waivers([waiver, numeric, cross])) == 3


# ----------------------------------------------------------------------------
# 评分归并等价轴: bigram 失配时由共享 embedding 余弦兜底, 守卫不放宽
# ----------------------------------------------------------------------------


def _row(rid, category, description, *, kind="normal", weight=None, meta=None):
    return SimpleNamespace(
        id=rid,
        category=category,
        description=description,
        requirement_kind=kind,
        score_weight=weight,
        is_key_review=1 if kind == "normal" else 1,
        scoring_metadata=meta,
        source_pages=json.dumps([15]),
        main_requirement_id=None,
        confirmed=0,
    )


_NORMAL_ROUTE = _row(1, "TECH", "技术路线须正确完整，研究方法须切实可行。评标委员会将根据方案质量按三档评分：优秀（8-10分）、良好（5-7分）、一般或有缺项（2-4分）。")
_SCORING_ROUTE = _row(2, "TECH", "技术路线正确且完整，研究方法可行得 8-10分；技术路线基本正确，主要内容完整，研究方法基本可行得 5-7分；技术路线一般，内容有缺项得 2-4分。", kind="scoring_item", weight=10)
_NORMAL_STAFF = _row(3, "STAFF", "科研团队主要人员不少于6人，须提供相应职称证书；高级职称每人加1分，中级每人加0.5分，上限5分。")
_SCORING_STAFF = _row(4, "STAFF", "科研团队主要人员具有 6人以上，得 3分，每少 1人扣 0.5分；具有相关专业高级职称每人加 1 分，具有中级职称每人加 0.5分，最高加 5分，并提供相应的职称证书。", kind="scoring_item", weight=8)


def test_paraphrase_pair_is_equivalent_only_with_semantic_axis():
    """3560 夹具(技术路线): bigram 不达标, 余弦达标即等价。"""
    assert scoring_reconcile.bigram_similarity(_NORMAL_ROUTE.description, _SCORING_ROUTE.description) < 0.55
    assert not scoring_reconcile.is_equivalent_scoring_clause(_NORMAL_ROUTE, _SCORING_ROUTE)
    assert scoring_reconcile.is_equivalent_scoring_clause(_NORMAL_ROUTE, _SCORING_ROUTE, semantic_similarity=0.86)


def test_semantic_axis_second_entity_and_guards_still_apply():
    """同形态第二例(人员职称)等价; 类别不同的高余弦对不得等价。"""
    assert scoring_reconcile.is_equivalent_scoring_clause(_NORMAL_STAFF, _SCORING_STAFF, semantic_similarity=0.9)
    assert not scoring_reconcile.is_equivalent_scoring_clause(_NORMAL_STAFF, _SCORING_ROUTE, semantic_similarity=0.95)


def test_best_match_ranks_by_semantic_when_bigram_ties_low():
    lookup = {(1, 2): 0.86, (3, 2): 0.30}
    assert scoring_reconcile._best_normal_match(_SCORING_ROUTE, [_NORMAL_ROUTE, _NORMAL_STAFF], lookup) is _NORMAL_ROUTE
    assert scoring_reconcile._best_normal_match(_SCORING_ROUTE, [_NORMAL_ROUTE, _NORMAL_STAFF], {}) is None


# ----------------------------------------------------------------------------
# 展示折叠: equivalent 关联的 normal 改写行折进评分表原文行
# ----------------------------------------------------------------------------


def _db_row(rid, category, description, *, kind="normal", meta=None):
    row = Requirement(
        project_id=1,
        document_id=1,
        category=category,
        description=description,
        is_mandatory=0,
        source_pages=json.dumps([15]),
        keywords=json.dumps([]),
        sub_requirements=json.dumps([]),
        requirement_kind=kind,
        scoring_metadata=meta,
        confirmed=0,
    )
    row.id = rid
    return row


def test_equivalent_linked_paraphrase_folds_under_scoring_item():
    normal = _db_row(1, RequirementCategory.TECH, _NORMAL_ROUTE.description, meta={
        "linked_scoring_fact_keys": ["requirement:2"],
        "scoring_relation_type": "equivalent",
    })
    scoring = _db_row(2, RequirementCategory.TECH, _SCORING_ROUTE.description, kind="scoring_item")
    items = group_requirements_for_display([normal, scoring])
    assert len(items) == 1
    assert items[0]["id"] == 2 and items[0]["requirement_kind"] == "scoring_item"
    assert items[0]["description"] == _SCORING_ROUTE.description, "代表项取评分表原文"
    assert items[0]["member_ids"] == [1, 2]


def test_covers_relation_and_missing_target_keep_rows_apart():
    covers = _db_row(1, RequirementCategory.STAFF, _NORMAL_STAFF.description, meta={
        "linked_scoring_fact_keys": ["requirement:2", "requirement:5"],
        "scoring_relation_type": "covers",
    })
    scoring = _db_row(2, RequirementCategory.STAFF, _SCORING_STAFF.description, kind="scoring_item")
    dangling = _db_row(3, RequirementCategory.TECH, _NORMAL_ROUTE.description, meta={
        "linked_scoring_fact_keys": ["requirement:999"],
        "scoring_relation_type": "equivalent",
    })
    items = group_requirements_for_display([covers, scoring, dangling])
    assert [item["id"] for item in items] == [1, 2, 3]
