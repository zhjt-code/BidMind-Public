""" 防御测试: 评分条款跨通道(normal x scoring_item)归并 + 通道内异名去重。

映射真实问题(项目1497业绩要求):
- 同一"400万满分19分"业绩评分条款被 tender_extractor(normal,id=16213)与
 scoring_extractor(scoring_item,id=16300)双通道各提取一份 -> 确认页重复+下游重复预估。
- 同一评分段被 LLM 拆成"40万门槛(无分值)"与"400万满分(19分)"异名两条 scoring_item
 (16296/16300) -> 旧 _dedupe 按 (category,name,weight) 精确键漏过。

断言核心输出而非"能跑通": 等价判定真值、回填字段、删除编排、防误并边界。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.review_contracts import parse_json_list  # noqa: E402
from app.schemas.requirement import ExtractedScoringItem  # noqa: E402
from app.core.extractor.scoring.scoring_reconcile import (  # noqa: E402
    _SIMILARITY_THRESHOLD,
    _backfill_scoring_into_normal,
    _best_normal_match,
    bigram_similarity,
    is_equivalent_scoring_clause,
    is_same_scoring_item,
    normal_weight_compatible,
    reconcile_scoring_items,
)
pytestmark = pytest.mark.scoring


# --- 真实业绩文本 fixture(取自项目1497确认页) -------------------------------

NORMAL_PERF_400 = (
    "相关业绩要求：自2024年5月11日至2026年5月11日中国境内招投标类软件产品销售业绩，"
    "累计金额达到400万元人民币及以上得满分19分，累计金额在[40万元人民币，400万元人民币)"
    "之间线性得分。需提供合同关键页扫描件（包括合同首页、采购内容页、合同金额页、合同大签页）"
    "及相应发票在国家税务总局全国增值税发票查验平台上的查验结果截图，时间以发票开票日期为准，"
    "金额以发票不含税金额为准。"
)
SCORING_PERF_400 = (
    "根据自2024年5月11日至2026年5月11日中国境内招投标类软件产品销售业绩进行评分，"
    "累计金额达到400万元人民币及以上的得满分19分，累计金额在[40万元人民币，400万元人民币)"
    "之间的在[0,19)间线性得分，得分四舍五入保留两位小数。提供业绩证明材料，包括："
    "合同关键页扫描件（包括但不限于合同首页、采购内容页、合同金额页、合同大签页），"
    "相应发票在国家税务总局全国增值税发票查验平台上的查验结果截图。"
)
SCORING_PERF_40 = (
    "具备中国境内招投标类软件产品销售业绩，自2024年5月11日至2026年5月11日累计金额达到"
    "40万元人民币。提供业绩证明材料，包括：合同关键页扫描件（包括但不限于合同首页、"
    "采购内容页、合同金额页、合同大签页），相应发票在国家税务总局全国增值税发票查验平台上的"
    "查验结果截图，时间以发票开票日期为准，金额以发票不含税金额为准。"
)
DIFFERENT_TECH = (
    "硬件资源占用及IT能源总耗考核：应用服务器、数据库服务器IT能源总耗低于基准依据，"
    "符合相关数据中心绿色规范设计要求，包括服务器占比及计算，IT能源总耗计算公式参照"
    "2025年中国信息技术服务运营行业从业人员管理办法。满分60分。"
)
# fixture(取自项目1500 page34 技术方案表): 不同评分因素共享同一段评分细则尾巴,
# 描述 bigram 实测 0.66(>=0.55 阈值)、名称相似度仅 0.11, 且各有满分6分 -> 属不同评分因素, 不可并。
SCORING_BUILD = (
    "根据应答人提供的整体建设方案进行评分，内容包括总体架构设计、技术架构、功能架构。"
    "在上述方案的基础上，根据建设方案的详细程度、合理性、可行性等方面进行评审，评分保留至整数位。"
    "根据方案的阐述情况进行评分（得分保留至整数位）：优秀（方案阐述详尽）得6分、良好(方案阐述充分)得[4,5]分，"
    "一般(方案阐述较为充分)得[2,3]分，较差(方案阐述不够充分)得1分，未提供得0分。"
)
SCORING_PLAN = (
    "根据应答人提供的实施计划方案进行评分，内容包括研发服务进度计划、系统测试、系统上线过程管理、过程文档管理等。"
    "在上述方案的基础上，根据实施计划方案的详细程度、合理性、可行性等方面进行评审，评分保留至整数位。"
    "根据方案的阐述情况进行评分（得分保留至整数位）：优秀（方案阐述详尽）得6分、良好(方案阐述充分)得[4,5]分，"
    "一般(方案阐述较为充分)得[2,3]分，较差(方案阐述不够充分)得1分，未提供得0分。"
)


def _req(
    *,
    rid: int,
    category: str = "performance",
    description: str = "",
    score_weight=None,
    is_key_review: int = 0,
    scoring_metadata=None,
    source_pages=None,
    requirement_kind: str = "normal",
    confirmed: int = 0,
    main_requirement_id=None,
) -> SimpleNamespace:
    """构造 Requirement-like 行(category 用字符串模拟枚举 value)。"""
    return SimpleNamespace(
        id=rid,
        document_id=1,
        category=category,
        description=description,
        score_weight=score_weight,
        is_key_review=is_key_review,
        scoring_metadata=scoring_metadata,
        source_pages=source_pages if source_pages is not None else [],
        requirement_kind=requirement_kind,
        confirmed=confirmed,
        main_requirement_id=main_requirement_id,
    )


def _scoring_item(
    *,
    category: str = "PERF",
    name: str = "业绩",
    description: str = "业绩要求描述文本",
    weight: float = 19.0,
    pages=None,
    score_rules=None,
) -> ExtractedScoringItem:
    return ExtractedScoringItem(
        category=category,
        score_item_name=name,
        description=description,
        score_weight=weight,
        source_pages=pages or [],
        score_rules=score_rules or [],
    )


# --- bigram 相似度阈值(真实文本驱动) ---------------------------------------


def test_bigram_similarity_equivalent_clause_above_threshold():
    # 同一评分条款的两种措辞(normal vs scoring)必须达标
    sim = bigram_similarity(NORMAL_PERF_400, SCORING_PERF_400)
    assert sim >= _SIMILARITY_THRESHOLD, f"同条款相似度 {sim} 低于阈值 {_SIMILARITY_THRESHOLD}"


def test_bigram_similarity_different_clause_below_threshold():
    # 业绩条款 vs 技术能耗条款必须低于阈值(防误并)
    sim = bigram_similarity(NORMAL_PERF_400, DIFFERENT_TECH)
    assert sim < _SIMILARITY_THRESHOLD, f"异条款相似度 {sim} 高于阈值,存在误并风险"


def test_bigram_similarity_empty_inputs():
    assert bigram_similarity("", "x") == 0.0
    assert bigram_similarity(None, "x") == 0.0


# --- 满分相容 ---------------------------------------------------------------


def test_weight_compatible_rules():
    assert normal_weight_compatible(None, 19) is True       # normal 无满分 -> 相容
    assert normal_weight_compatible(19, 19) is True          # 相等
    assert normal_weight_compatible(19, 19.004) is True      # 容差内
    assert normal_weight_compatible(0, 19) is True           # <=0 视为无有效满分
    assert normal_weight_compatible(19, 10) is False         # 都有效且不等 -> 不相容(防误并)


# --- 跨通道等价判定 ---------------------------------------------------------


def test_equivalent_same_clause_cross_channel():
    normal = _req(rid=16213, description=NORMAL_PERF_400, score_weight=19, is_key_review=1)
    scoring = _req(
        rid=16300, description=SCORING_PERF_400, score_weight=19,
        requirement_kind="scoring_item",
    )
    assert is_equivalent_scoring_clause(normal, scoring) is True


def test_not_equivalent_when_weight_conflicts():
    normal = _req(rid=1, description=NORMAL_PERF_400, score_weight=19, is_key_review=1)
    # 描述高度相似但满分不同(19 vs 10) -> 视为不同档评分项, 不归并
    scoring = _req(
        rid=2, description=SCORING_PERF_400, score_weight=10,
        requirement_kind="scoring_item",
    )
    assert is_equivalent_scoring_clause(normal, scoring) is False


def test_not_equivalent_when_category_differs():
    normal = _req(rid=1, category="performance", description=NORMAL_PERF_400, score_weight=19)
    scoring = _req(
        rid=2, category="technical", description=NORMAL_PERF_400, score_weight=19,
        requirement_kind="scoring_item",
    )
    assert is_equivalent_scoring_clause(normal, scoring) is False


def test_not_equivalent_when_clause_topic_differs():
    normal = _req(rid=1, description=NORMAL_PERF_400, score_weight=60, is_key_review=1)
    scoring = _req(
        rid=2, description=DIFFERENT_TECH, score_weight=60,
        requirement_kind="scoring_item",
    )
    # 同满分但描述是不同评分项(业绩 vs 能耗) -> 相似度不达标 -> 不归并
    assert is_equivalent_scoring_clause(normal, scoring) is False


# --- 主行优先匹配 -----------------------------------------------------------


def test_best_match_prefers_main_row_on_tie():
    # 两条 normal 子面行描述相同(复合拆分),应优先选主行(main_requirement_id 为 None)
    sub = _req(rid=20, description=NORMAL_PERF_400, score_weight=19, is_key_review=1, main_requirement_id=10)
    main = _req(rid=10, description=NORMAL_PERF_400, score_weight=19, is_key_review=1, main_requirement_id=None)
    scoring = _req(rid=99, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item")
    match = _best_normal_match(scoring, [sub, main])
    assert match is main


def test_best_match_none_when_no_equivalent():
    normal = _req(rid=1, category="technical", description=DIFFERENT_TECH, score_weight=60, is_key_review=1)
    scoring = _req(rid=2, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item")
    assert _best_normal_match(scoring, [normal]) is None


# --- 回填 -------------------------------------------------------------------


def test_backfill_fills_metadata_and_merges_pages():
    normal = _req(
        rid=10, description=NORMAL_PERF_400, score_weight=19, is_key_review=1,
        scoring_metadata=None, source_pages=[3, 26, 27],
    )
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "max_score": 19}, source_pages=[27, 28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    assert normal.is_key_review == 1
    # 验证关键字段回填, 不做整体 dict 相等(authoritative_description 会一并写入)
    assert normal.scoring_metadata["volume"] == "technical"
    assert normal.scoring_metadata["max_score"] == 19
    assert parse_json_list(normal.source_pages) == [3, 26, 27, 28]


def test_backfill_does_not_overwrite_existing_metadata():
    existing = {"volume": "commercial", "max_score": 19}
    normal = _req(
        rid=10, description=NORMAL_PERF_400, score_weight=19, is_key_review=1,
        scoring_metadata=existing, source_pages=[26],
    )
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical"}, source_pages=[28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    # 已有字段不被覆盖(测试意图: commercial 不应变 technical, max_score 不变)
    assert normal.scoring_metadata["volume"] == "commercial"
    assert normal.scoring_metadata["max_score"] == 19


def test_backfill_fills_weight_when_normal_missing():
    normal = _req(rid=10, description=NORMAL_PERF_400, score_weight=None, is_key_review=1, source_pages=[26])
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        source_pages=[28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    assert normal.score_weight == 19


def test_backfill_fills_volume_through_inline_placeholder():
    # 追修: relevance 给评分相关 normal 标占位 {'source':'inline'}, 旧 all-or-nothing 回填
    # 因占位 truthy 整体跳过 → 卷归属丢失落入"其他"卷。逐字段合并须穿透占位补全 volume。
    normal = _req(
        rid=10, description=NORMAL_PERF_400, score_weight=None, is_key_review=1,
        scoring_metadata={"source": "inline"}, source_pages=[26],
    )
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "volume_weight": 0.4, "volume_max": 100},
        source_pages=[28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    assert normal.score_weight == 19
    assert normal.scoring_metadata["volume"] == "technical"     # 占位不再阻断卷归属回填
    assert normal.scoring_metadata["volume_weight"] == 0.4
    assert normal.scoring_metadata["volume_max"] == 100
    assert normal.scoring_metadata["source"] == "inline"        # 原占位字段保留


# 阶段3(2026-08-31): 通道内 _dedupe 用例随确定性链去重助手删除;
# is_same_scoring_item 语义判定用例保留如下。


# 共享评分细则的不同评分因素不被误并(名称可辨性 + 满分归属判别) ----------


def test_is_same_item_distinct_factors_sharing_rubric_not_merged():
    # 整体建设 vs 实施计划: 描述相似(共享评分细则)+ 各有满分6 + 名称迥异 → 不同评分因素
    assert is_same_scoring_item(
        "整体建设方案", SCORING_BUILD, "实施计划方案", SCORING_PLAN,
        weight_a=6.0, weight_b=6.0,
    ) is False


def test_is_same_item_fragment_without_weight_merged():
    # 一方无有效满分(0) → 同段档位碎片, 描述达门槛即合并
    assert is_same_scoring_item(
        "业绩要求", SCORING_PERF_40, "相关业绩", SCORING_PERF_400,
        weight_a=0, weight_b=19.0,
    ) is True


def test_is_same_item_near_identical_desc_merged_despite_name():
    # 描述近乎一致(>=0.85)即便名称不同、双方有满分也合并(跨chunk重复兜底)
    assert is_same_scoring_item(
        "甲", SCORING_BUILD, "乙", SCORING_BUILD, weight_a=6, weight_b=6,
    ) is True


def test_is_same_item_below_desc_threshold_not_merged():
    assert is_same_scoring_item(
        "x", SCORING_PERF_400, "y", DIFFERENT_TECH, weight_a=19, weight_b=60,
    ) is False


# --- reconcile 编排(fake async session) -------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.deleted: list = []
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._rows)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_reconcile_links_scoring_to_normal_without_deleting_fact():
    normal = _req(rid=16213, description=NORMAL_PERF_400, score_weight=19, is_key_review=1, source_pages=[3, 26, 27, 28])
    scoring = _req(
        rid=16300, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical"}, source_pages=[28],
    )
    session = _FakeSession([normal, scoring])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 1
    assert scoring not in session.deleted      # 权威评分事实不得被删除
    assert normal not in session.deleted       # normal 主行保留(防 回归)
    assert normal.scoring_metadata["linked_scoring_fact_keys"] == ["requirement:16300"]
    assert normal.scoring_metadata["scoring_relation_type"] == "equivalent"
    assert session.commits >= 1


@pytest.mark.asyncio
async def test_reconcile_keeps_unmatched_scoring_item():
    # normal 是技术能耗项, scoring 是业绩项 -> 无等价, scoring 保留
    normal = _req(rid=1, category="technical", description=DIFFERENT_TECH, score_weight=60, is_key_review=1)
    scoring = _req(
        rid=2, category="performance", description=SCORING_PERF_400, score_weight=19,
        requirement_kind="scoring_item",
    )
    session = _FakeSession([normal, scoring])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 0
    assert scoring not in session.deleted


@pytest.mark.asyncio
async def test_reconcile_ignores_confirmed_scoring_item():
    normal = _req(rid=1, description=NORMAL_PERF_400, score_weight=19, is_key_review=1, source_pages=[26])
    scoring = _req(
        rid=2, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        confirmed=1,  # 人工已确认 -> 不动
    )
    session = _FakeSession([normal, scoring])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 0
    assert scoring not in session.deleted


@pytest.mark.asyncio
async def test_reconcile_skips_normal_without_scoring_signal():
    # normal 业绩要求未被识别为评分条款(无 weight 无 key) -> 不作候选, scoring 保留(保守防误并)
    normal = _req(rid=1, description=NORMAL_PERF_400, score_weight=None, is_key_review=0)
    scoring = _req(
        rid=2, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
    )
    session = _FakeSession([normal, scoring])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 0
    assert scoring not in session.deleted


# 多采购包 lot 维度在归并链路中不丢失/不跨包吞分 ------------------


@pytest.mark.asyncio
async def test_bug557_reconcile_backfills_lot_into_normal():
    """(项目1553真实回归): scoring_item 带 lot=采购包1, 归并进 normal 主行后
 lot 必须随评分元数据回填——丢 lot 的回填行会被确认页 lot 分组静默丢弃,
 包1 被误报 94.5/100"漏提"(实缺的 5.5 分全在回填行上)。"""
    normal = _req(
        rid=10, description=NORMAL_PERF_400, score_weight=None, is_key_review=1,
        scoring_metadata={"source": "inline"}, source_pages=[36],
    )
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=1.5, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "lot": "采购包1", "score_item_name": "5、业绩"},
        source_pages=[36],
    )
    session = _FakeSession([normal, scoring])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 1
    assert scoring not in session.deleted
    assert normal.scoring_metadata["linked_scoring_fact_keys"] == ["requirement:20"]
    assert normal.score_weight is None


@pytest.mark.asyncio
async def test_bug557_reconcile_blocks_cross_lot_second_merge():
    """: 两包各有同名同分评分项(描述相似度达标)时, normal 主行被包1占据后,
 包2 的等价项不得再并入——否则第二次归并只删行不转分(回填不覆盖已有值),
 包2 静默丢分。包2 项必须保留为独立 scoring_item。"""
    normal = _req(
        rid=10, description=NORMAL_PERF_400, score_weight=None, is_key_review=1,
        scoring_metadata=None, source_pages=[36],
    )
    scoring_lot1 = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "lot": "采购包1"}, source_pages=[36],
    )
    scoring_lot2 = _req(
        rid=21, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "lot": "采购包2"}, source_pages=[41],
    )
    session = _FakeSession([normal, scoring_lot1, scoring_lot2])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 1
    assert scoring_lot1 not in session.deleted   # 事实保留
    assert scoring_lot2 not in session.deleted   # 包2 项不得被跨包吞掉
    assert normal.scoring_metadata["linked_scoring_fact_keys"] == ["requirement:20"]


@pytest.mark.asyncio
async def test_bug626_one_normal_can_cover_multiple_scoring_facts_without_deleting():
    """: 聚合 normal 可关联多个评分事实, 但不得删除或搬运分值。"""
    normal = _req(
        rid=10,
        category="staffing",
        description=(
            "总监理工程师及监理团队人员需持有信息系统监理师、CISP等证书，"
            "总监理工程师、技术负责人、人员实力1、2、3项同一人员不重复计分。"
        ),
        score_weight=3,
        is_key_review=1,
        scoring_metadata={"source": "inline", "lot": "采购包2"},
    )
    scoring_a = _req(
        rid=20,
        category="staffing",
        description="投标人拟派出的总监理工程师具有信息系统监理师证书等，全部证书得3分。",
        score_weight=3,
        requirement_kind="scoring_item",
        scoring_metadata={"lot": "采购包2", "volume": "technical"},
    )
    scoring_b = _req(
        rid=21,
        category="staffing",
        description="投标人拟派的监理团队人员具有信息系统监理师证书等，全部证书得3分。",
        score_weight=3,
        requirement_kind="scoring_item",
        scoring_metadata={"lot": "采购包2", "volume": "technical"},
    )
    session = _FakeSession([normal, scoring_a, scoring_b])

    report = await reconcile_scoring_items(session, document_id=1)

    assert report.merged_count == 2
    assert session.deleted == []
    assert normal.score_weight == 3
    assert normal.scoring_metadata["linked_scoring_fact_keys"] == [
        "requirement:20",
        "requirement:21",
    ]
    assert normal.scoring_metadata["scoring_relation_type"] == "covers"


# --- P0 A2: authoritative_description 保留与传递 ----------------------------


def test_backfill_preserves_normal_description_and_stores_authoritative():
    """归并后 normal.description 不得被覆盖(保分面检索语义), 且
    scoring_metadata['authoritative_description'] 应写入评分表权威原文(scoring.description)。
    """
    llm_rewritten = "LLM改写后的业绩要求描述，语义相同但用词不同，适合检索使用"
    authority_text = SCORING_PERF_400  # 评分表原始权威描述
    normal = _req(
        rid=10, description=llm_rewritten, score_weight=19, is_key_review=1,
        scoring_metadata=None, source_pages=[26],
    )
    scoring = _req(
        rid=20, description=authority_text, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical", "max_score": 19},
        source_pages=[28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    assert normal.description == llm_rewritten, "normal.description 不应被覆盖（保分面检索语义）"
    assert normal.scoring_metadata.get("authoritative_description") == authority_text


def test_backfill_authoritative_description_not_overwrite_existing():
    """若 normal 已有 authoritative_description, 回填不覆盖(防重复归并改写)。"""
    original_auth = "原有权威描述（已由首次归并写入）"
    normal = _req(
        rid=10, description="LLM改写描述", score_weight=19, is_key_review=1,
        scoring_metadata={"authoritative_description": original_auth},
        source_pages=[26],
    )
    scoring = _req(
        rid=20, description=SCORING_PERF_400, score_weight=19,
        requirement_kind="scoring_item",
        scoring_metadata={"volume": "technical"},
        source_pages=[28],
    )
    _backfill_scoring_into_normal(normal, scoring)
    assert normal.scoring_metadata["authoritative_description"] == original_auth


def test_bug557_equivalence_denied_when_lots_differ():
    """lot 双方已知且不同 → 直接判不等价(不依赖描述/满分差异)。"""
    normal = _req(
        rid=1, description=NORMAL_PERF_400, score_weight=19, is_key_review=1,
        scoring_metadata={"lot": "采购包1"},
    )
    scoring = _req(
        rid=2, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"lot": "采购包2"},
    )
    assert is_equivalent_scoring_clause(normal, scoring) is False
    # 单方无 lot 时保持既有宽松语义(单包文档不受影响)
    normal_no_lot = _req(
        rid=3, description=NORMAL_PERF_400, score_weight=19, is_key_review=1,
    )
    assert is_equivalent_scoring_clause(normal_no_lot, scoring) is True


def test_cross_group_label_equivalence_denied():
    """换轴 P2-2: 原文组标签双方已知且归一后不同 → 判不等价(守卫的标签轴推广)。

 等价表述(如 "商务标" vs "商务部分")归一后相同, 不得误拦。
 """
    normal = _req(
        rid=1, description=NORMAL_PERF_400, score_weight=19, is_key_review=1,
        scoring_metadata={"volume_label": "商务部分"},
    )
    scoring_other_group = _req(
        rid=2, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume_label": "技术部分"},
    )
    assert is_equivalent_scoring_clause(normal, scoring_other_group) is False

    scoring_same_group_variant = _req(
        rid=3, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={"volume_label": "商务标"},
    )
    assert is_equivalent_scoring_clause(normal, scoring_same_group_variant) is True

    scoring_no_label = _req(
        rid=4, description=SCORING_PERF_400, score_weight=19, requirement_kind="scoring_item",
        scoring_metadata={},
    )
    assert is_equivalent_scoring_clause(normal, scoring_no_label) is True
