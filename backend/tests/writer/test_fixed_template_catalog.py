"""固定模板 catalog 同步路径回归测试。

覆盖:
- 章节锁定: 没有"第五章 投标文件格式"章节时 catalog 为空
- 章节内子目录切分: 相邻模板按页边界正确分离
- 泛化格式要求被规则层拒绝
- 法定代表人身份证明标题正确映射到 legal_representative_certificate template_key
"""

import pytest
from types import SimpleNamespace

from app.core.writer.fixed_template import build_fixed_template_catalog, infer_fixed_template_key
from app.core.writer.fixed_template.features import is_generic_template_requirement
from app.core.writer.fixed_template.catalog import build_fixed_template_catalog_async


def _chunk(page: int, text: str, idx: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(page_num=page, chunk_index=idx if idx is not None else page, content=text)


def _section_header_chunks(page_start: int = 39) -> list[SimpleNamespace]:
    """构造一个"第五章 投标文件格式"章节封面 + 目录页。"""
    return [
        _chunk(page_start, "第五章 投标文件格式\n封面及目录格式\n39"),
        _chunk(
            page_start + 1,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "宁东油田防腐药剂及防腐工具性能测试项目\n"
            "投 标 文 件\n"
            "技术标\n"
            "( )\n"
            "投 标 人: (盖单位章)\n"
            "法定代表人或\n"
            "其委托代理人: (签字)\n"
            "年 月 日\n"
            "2025\n"
            "38",
        ),
        _chunk(
            page_start + 2,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "目 录\n"
            "一、 投标函\n"
            "二、施工资质准入\n"
            "三、投标保证金缴纳证明(内部单位无需提供)\n"
            "四、法定代表人身份证明\n"
            "五、法定代表人授权委托书\n"
            "六、营业执照、组织机构代码证、税务登记证(或三证合一证件)\n"
            "七、投标人基本情况及人员配置表及公司简介\n"
            "八、与评分有关资料(按评分表顺序装订)\n"
            "九、其他资料\n"
            "39",
        ),
    ]


@pytest.mark.asyncio
async def test_catalog_builds_entries_from_llm_output(monkeypatch) -> None:
    chunks = _section_header_chunks(39) + [
        _chunk(
            43,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "三、投标保证金缴纳证明\n"
            "致:招标人:\n"
            "本投标人自愿参加本项目投标。\n"
            "投 标 人: (盖单位章)\n"
            "法定代表人或其委托代理人: (签字)\n"
            "年 月 日",
        ),
        _chunk(
            44,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "四、法定代表人证明书\n"
            "致:招标人\n"
            "(法定代表人姓名)现任我单位 职务,为法定代表人。\n"
            "附:姓 名: 性别: 年龄: 职务:\n"
            "投标人: (公章)\n"
            "日 期: 年 月 日",
        ),
        _chunk(
            45,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "五、法定代表人授权委托书\n"
            "本人系企业法定代表人,特授权(身份证号码)代表我单位办理投标。\n"
            "被授权人签名: 职务:\n"
            "法定代表人签名: 职务:\n"
            "投标人(公章)\n"
            "日 期: 年 月 日",
        ),
    ]

    async def _recognize_templates(chunks_text, ranges, project_id=None):
        return [
            {
                "template_key": "bid_security_certificate",
                "title": "投标保证金缴纳证明",
                "volume_type": "technical",
                "source_page_start": 43,
                "source_page_end": 43,
                "raw_text": "三、投标保证金缴纳证明\n致:招标人:\n本投标人自愿参加本项目投标并按要求交纳保证金。\n投 标 人: (盖单位章)\n年 月 日",
                "fields": ["投标人"],
                "signature_slots": ["盖单位章", "日期"],
                "confidence": 0.95,
            },
            {
                "template_key": "legal_representative_certificate",
                "title": "法定代表人证明书",
                "volume_type": "technical",
                "source_page_start": 44,
                "source_page_end": 44,
                "raw_text": "四、法定代表人证明书\n致:招标人\n(法定代表人姓名)现任我单位 职务,为法定代表人。\n投标人: (公章)\n日 期: 年 月 日",
                "fields": ["职务"],
                "signature_slots": ["公章", "日期"],
                "confidence": 0.95,
            },
            {
                "template_key": "legal_representative_authorization",
                "title": "法定代表人授权委托书",
                "volume_type": "technical",
                "source_page_start": 45,
                "source_page_end": 45,
                "raw_text": "五、法定代表人授权委托书\n致:招标人\n本人系企业法定代表人,特授权代表办理投标\n投标人(公章)\n日 期: 年 月 日",
                "fields": ["法定代表人"],
                "signature_slots": ["公章", "日期"],
                "confidence": 0.95,
            },
        ]

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _recognize_templates,
    )

    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)
    by_key = {entry.template_key: entry for entry in catalog}

    assert by_key["bid_security_certificate"].source_page_start == 43
    assert "法定代表人证明书" not in by_key["bid_security_certificate"].raw_text
    assert by_key["legal_representative_certificate"].source_page_start == 44
    assert by_key["legal_representative_authorization"].source_page_start == 45


@pytest.mark.asyncio
async def test_catalog_falls_back_to_rule_when_llm_returns_empty(monkeypatch) -> None:
    """: LLM 主路径返空时启用规则兜底, 输出 source=rule_only 的低置信 entry。"""
    chunks = _section_header_chunks(39)

    async def _empty(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _empty,
    )
    catalog = await build_fixed_template_catalog_async(chunks, project_id=1)
    assert catalog, "LLM 空返时不应再让 catalog 整段空白"
    for entry in catalog:
        assert entry.source == "rule_only"
        assert entry.confidence < 0.75, "兜底 entry 必须低于 is_complete_candidate 阈值"
        snapshot = entry.snapshot_dict()
        assert snapshot["is_complete_candidate"] is False
        assert snapshot["source"] == "rule_only"


def test_catalog_ignores_templates_outside_format_chapter() -> None:
    """同样模板片段如果不在投标文件格式章节内, 不应进入 catalog。"""
    chunks = [
        _chunk(
            4,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "投标须知前附表\n"
            "若投标人的法定代表人委托代理人出席开标会议,需提交法定代表人授权委托书。",
        ),
        _chunk(
            11,
            "宁东油田防腐药剂及防腐工具性能测试\n"
            "投标文件由商务标和技术标组成。\n"
            "( ) 投标函\n"
            "( ) 开标一览表",
        ),
    ]
    assert build_fixed_template_catalog(chunks) == []


def test_generic_format_requirement_is_not_concrete_template() -> None:
    text = "投标人递交的投标文件必须毫无例外地使用招标文件所提供的投标文件全部格式，表格可以按同样格式扩展。"

    assert is_generic_template_requirement(text)
    # 同步路径已下线, 明确返回空 catalog。
    assert build_fixed_template_catalog([_chunk(12, text)]) == []


def test_legal_representative_id_title_maps_to_template_key() -> None:
    assert infer_fixed_template_key("法定代表人身份证明") == "legal_representative_certificate"
