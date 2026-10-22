""": 提取单位收口与上文承接。

形态: 检索粒度分块被当提取单位, 同一表文双份进 LLM 且单行无主体; 跨块列表续项丢引导句。
夹具: 前附表行(招标人信息) / 初步评审表行(评委动作) / 废标列举跨页 / 以冒号引导的材料清单。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost:5432/testdb",
)
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.extractor.tender_extractor_parts.extraction_units import (  # noqa: E402
    DERIVED_CHUNK_TYPES,
    build_lead_context_map,
    extract_lead_sentence,
    prepend_lead_context,
    select_extraction_chunks,
)


def _chunk(cid: int, page: int, content: str, chunk_type: str = "paragraph") -> SimpleNamespace:
    return SimpleNamespace(id=cid, page_num=page, content=content, chunk_type=chunk_type)


def test_derived_table_chunks_are_not_extraction_units():
    """前附表行与评审表行是同页正文的复制品, 不再单独送提取。"""
    chunks = [
        _chunk(1, 6, "投标须知前附表\n5 资金来源 成本\n6 出资比例 100%\n7 资金落实情况 已落实"),
        _chunk(2, 6, "表格第7行：条款号=6；条款名称=出资比例；编列内容=100%", "table_row_with_header"),
        _chunk(3, 13, "初步评审表\n报价唯一性 只能有一个有效报价"),
        _chunk(4, 13, "表格第4行：评议内容=报价唯一性；评议标准=只能有一个有效报价", "table_row_with_header"),
        _chunk(5, 15, "[评分表 第15-15页]\n| 分组 | 评分因素 | 分值 |", "scoring_table"),
        _chunk(6, 20, "扫描件: 营业执照", "image_evidence"),
        _chunk(7, 21, "第三条 投标人资格", "clause"),
    ]
    kept = select_extraction_chunks(chunks)
    assert [c.id for c in kept] == [1, 3, 6, 7]
    assert DERIVED_CHUNK_TYPES == {"table_row_with_header", "scoring_table"}


def test_lead_context_carries_consequence_lead_across_pages():
    """3560 夹具: P15 块首「(11)…」拿到 P14 的「其投标将被拒绝:」引导句。"""
    chunks = [
        _chunk(
            10,
            14,
            "4、实质上没有响应招标文件要求的投标将被拒绝。如发现下列情况之一的,\n"
            "其投标将被拒绝:\n(1)不满足本次招标文件要求的投标人资格条件的。\n"
            "(10)投标文件技术要求中的响应与事实不符的。",
        ),
        _chunk(11, 15, "(11)一年内受过地方行政监督部门行政处罚或罚款。\n(12)投标文件中存在对甲方配合不合理的要求。"),
        _chunk(12, 16, "十一、定标及中标通知书\n1、招标人按照法定程序对评标结果进行公示。"),
    ]
    leads = build_lead_context_map(chunks)
    assert set(leads) == {11}
    assert leads[11] == "其投标将被拒绝:"
    prompt_text = prepend_lead_context(chunks[1].content, leads[11])
    assert prompt_text.startswith("[上文承接")
    assert "其投标将被拒绝:" in prompt_text
    assert prompt_text.endswith(chunks[1].content)


def test_lead_context_second_form_colon_led_material_list():
    """同形态第二例: 材料清单以冒号引导, 续页块首「(4)…」拿到清单引导句而非上一项。"""
    chunks = [
        _chunk(
            20,
            30,
            "投标文件应包括以下资料：\n(1)投标函；\n(2)法定代表人身份证明；\n(3)授权委托书；",
        ),
        _chunk(21, 31, "(4)营业执照副本复印件；\n(5)近三年财务审计报告。"),
    ]
    leads = build_lead_context_map(chunks)
    assert leads == {21: "投标文件应包括以下资料："}


def test_lead_context_absent_when_chunk_does_not_continue_a_list():
    chunks = [
        _chunk(30, 3, "第一章 招标公告\n招标人：某公司"),
        _chunk(31, 4, "第二章 投标须知\n投标人应仔细阅读本须知。"),
    ]
    assert build_lead_context_map(chunks) == {}
    assert prepend_lead_context("正文", "") == "正文"


def test_extract_lead_sentence_falls_back_to_tail_without_lead_marker():
    tail = extract_lead_sentence("A" * 300 + "\n没有引导标记的普通结尾句")
    assert tail.endswith("没有引导标记的普通结尾句")
    assert len(tail) <= 120
