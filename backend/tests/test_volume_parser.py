""" 第1层防御测试: 卷定义行解析与剥离。

映射真实风险:
- 卷说明行"技术总分100分，按40%权重折算"被 LLM 误提为 score_weight=100 评分项(项目1496 id=16132/16133)。
- 普通子项(含"满分8分"/"低于5%得4分")不得被误判为卷定义。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from app.core.extractor.scoring.volume_parser import (  # noqa: E402
    VolumeDefinition,
    apply_volume_definitions,
    parse_volume_definition,
    split_definitions_and_items,
)

pytestmark = pytest.mark.scoring


def _item(description, volume=None, volume_weight=None, volume_max=None):
    return SimpleNamespace(
        description=description,
        volume=volume,
        volume_weight=volume_weight,
        volume_max=volume_max,
    )


def test_parse_volume_definition_technical_40pct():
    it = _item(
        "技术总分100分，按40%权重进行折算。技术得分低于60分的应答人不具备入选资格...",
        volume="technical",
    )
    vdef = parse_volume_definition(it)
    assert vdef is not None
    assert vdef.volume == "technical"
    assert vdef.weight == 0.4
    assert vdef.volume_max == 100


def test_parse_volume_definition_price_60pct():
    it = _item("价格总分100分，按60%折算。价格评审采用向下引导的中间价评分法...", volume="price")
    vdef = parse_volume_definition(it)
    assert vdef is not None
    assert vdef.weight == 0.6
    assert vdef.volume_max == 100


def test_parse_regular_item_not_misidentified():
    # 普通子项(有"满分8分"但无"总分N分按M%折算")不得识别为卷定义
    it = _item("提供产品DEMO在线演示，每项全功能演示一次满分8分；知识库...", volume="technical")
    assert parse_volume_definition(it) is None


def test_parse_flat_volume_composition_1547_two_groups():
    """: 平铺"分值构成"行 → 组基准(商务技术20/价格80), 最终得分制。"""
    from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

    text = "2.2.1 分值构成（总分100分） 商务技术评审部分： 20 分； 价格评审部分： 80 分。"
    defs = parse_flat_volume_composition(text)

    assert set(defs.keys()) == {"commercial", "price"}
    assert defs["commercial"].volume_max == 20.0
    assert defs["commercial"].weight == 0.2
    assert defs["price"].volume_max == 80.0
    assert defs["price"].weight == 0.8


def test_parse_flat_volume_composition_1521_three_groups():
    """: 1521 形态"商务部分：25分 技术服务部分：45 分 投标报价：30分"。"""
    from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

    text = "分值构成 (总分100分) 商务部分：25分 技术服务部分：45 分 投标报价：30分"
    defs = parse_flat_volume_composition(text)

    assert defs["commercial"].volume_max == 25.0
    assert defs["technical"].volume_max == 45.0
    assert defs["price"].volume_max == 30.0
    assert round(sum(d.weight for d in defs.values()), 4) == 1.0


def test_parse_flat_volume_composition_rejects_prose_and_single_pair():
    """护栏: 无"分值构成"触发词或不足两组的文本不得产出组基准。"""
    from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

    assert parse_flat_volume_composition("投标报价：30分，等于评标基准价时得满分。") == {}
    assert parse_flat_volume_composition("分值构成（总分100分）") == {}
    # 总分/合计标签不得当组
    assert parse_flat_volume_composition("分值构成 总分：100分 商务部分：20分") == {}
    assert parse_flat_volume_composition("分值构成 信誉：10分 评标价：10分") == {}


def test_parse_flat_volume_composition_1550_each_part_100_sentence():
    """: 1550 句式卷定义——三部分每个部分满分均为100分。"""
    from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

    text = "四、分值构成(权重)(详见公告中附件) 分技术、商务、价格三个部分分别进行量化评审,每个部分满分均为100分,并按分值合计计算最终量化评审得分。"
    defs = parse_flat_volume_composition(text)

    assert set(defs.keys()) == {"technical", "commercial", "price"}
    assert defs["technical"].volume_max == 100.0
    assert defs["commercial"].volume_max == 100.0
    assert defs["price"].volume_max == 100.0
    assert defs["technical"].weight == round(1 / 3, 4)
    assert round(sum(v.weight for v in defs.values()), 4) == 1.0


def test_parse_flat_volume_composition_each_part_requires_part_list():
    """护栏: 只有“每个部分满分”但没有明确列出部分名称时, 不猜卷定义。"""
    from app.core.extractor.scoring.volume_parser import parse_flat_volume_composition

    assert parse_flat_volume_composition("分值构成：每个部分满分均为100分。") == {}


def test_extract_volume_composition_sentence_1550():
    """: 从长段落中定位可解析的卷定义声明句, 供 builder 注入 prelude。"""
    from app.core.extractor.scoring.volume_parser import (
        extract_volume_composition_sentence,
        parse_flat_volume_composition,
    )

    text = (
        "三、资质业绩凭证(此表为样例,实际操作时可为一张表或多张表)\n"
        "四、分值构成(权重)(详见公告中附件)\n"
        "分技术、商务、价格三个部分分别进行量化评审,每个部分满分均为100分,并按分值合计计算最终量化评审得分。\n"
        "五、其他说明。"
    )
    sentence = extract_volume_composition_sentence(text)

    assert sentence is not None
    assert "分技术、商务、价格三个部分" in sentence
    assert parse_flat_volume_composition(sentence)


def test_extract_volume_composition_sentence_rejects_unparseable_text():
    """护栏: 文本含“分值构成”但没有可解析卷定义时不返回句子。"""
    from app.core.extractor.scoring.volume_parser import extract_volume_composition_sentence

    assert extract_volume_composition_sentence("分值构成详见附件。价格部分得分按公式计算。") is None


def test_parse_no_false_positive_on_percentage_in_rules():
    # "低于5%的得4分"含百分号但非卷权重折算 → 不误判
    it = _item("维护费用占应答报价的比例，低于5%的得4分；[10%,5%)之间得2分。", volume="commercial")
    assert parse_volume_definition(it) is None


def test_split_separates_definitions_from_items():
    items = [
        _item("技术总分100分，按40%权重折算", volume="technical"),
        _item("价格总分100分，按60%折算", volume="price"),
        _item("技术方案完整性，满分6分", volume="technical"),
    ]
    defs, scoring = split_definitions_and_items(items)
    assert set(defs.keys()) == {"technical", "price"}
    assert len(scoring) == 1
    assert defs["technical"].weight == 0.4
    assert defs["price"].weight == 0.6


def test_apply_backfills_weight_only_to_matching_volume():
    defs = {"technical": VolumeDefinition("technical", 0.4, 100.0)}
    items = [_item("技术子项", volume="technical"), _item("资格项", volume="qualification")]
    apply_volume_definitions(items, defs)
    assert items[0].volume_weight == 0.4
    assert items[0].volume_max == 100.0
    # 无价格卷(非经典加权制)→ 孤儿项不归并, 保持 None(聚合时该卷视为无权重 → 折算降级)
    assert items[1].volume == "qualification"
    assert items[1].volume_weight is None
    assert items[1].volume_max is None


# ---------------------------------------------------------------------------
# B: 孤儿卷归并(技术综合+价格公式加权制下, 子段被误判的独立卷归并到技术卷)
# ---------------------------------------------------------------------------


def test_apply_remaps_orphan_volume_to_sole_non_price_volume():
    # 加权制(技术+价格)下, 孤儿卷(commercial 维保 16568/16569)归并到唯一非价格定义卷(technical)
    defs = {
        "technical": VolumeDefinition("technical", 0.4, 100.0),
        "price": VolumeDefinition("price", 0.6, 100.0),
    }
    items = [_item("维保费用评分", volume="commercial")]
    apply_volume_definitions(items, defs)
    assert items[0].volume == "technical"
    assert items[0].volume_weight == 0.4
    assert items[0].volume_max == 100.0


def test_apply_no_remap_without_price_volume():
    # 无价格卷(非经典加权制): 孤儿卷不归并, 保持原样降级
    defs = {"technical": VolumeDefinition("technical", 0.4, 100.0)}
    items = [_item("孤儿商务项", volume="commercial")]
    apply_volume_definitions(items, defs)
    assert items[0].volume == "commercial"
    assert items[0].volume_weight is None


def test_apply_no_remap_with_multiple_non_price_volumes():
    # 多个非价格定义卷(真三卷加权制): 归并目标不唯一 → 不归并(防误并)
    defs = {
        "technical": VolumeDefinition("technical", 0.3, 100.0),
        "commercial": VolumeDefinition("commercial", 0.3, 100.0),
        "price": VolumeDefinition("price", 0.4, 100.0),
    }
    items = [_item("孤儿人员项", volume="staffing")]
    apply_volume_definitions(items, defs)
    assert items[0].volume == "staffing"
    assert items[0].volume_weight is None


def test_apply_defined_volume_not_remapped():
    # 框架已定义的卷(commercial 真为加权卷)不被当孤儿, 保留自身卷权重
    defs = {
        "technical": VolumeDefinition("technical", 0.4, 100.0),
        "commercial": VolumeDefinition("commercial", 0.2, 100.0),
        "price": VolumeDefinition("price", 0.4, 100.0),
    }
    items = [_item("商务子项", volume="commercial")]
    apply_volume_definitions(items, defs)
    assert items[0].volume == "commercial"
    assert items[0].volume_weight == 0.2
