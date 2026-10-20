"""项目1382 招标 PDF 黄金样本回放。

覆盖:
- 用项目1382 招标 PDF 第 37-55 页的真实 chunk 文本片段
- 期望 catalog 只输出技术标 + 商务标内的真实模板, 不再包含投标须知/合同条款/目录页的伪条目
- page_start 集合 ⊆ {41, 43, 44, 45, 47, 52, 53}
"""

from types import SimpleNamespace

import pytest

from app.core.writer.fixed_template import build_fixed_template_catalog_async


def _chunk(page: int, text: str, idx: int) -> SimpleNamespace:
    return SimpleNamespace(page_num=page, chunk_index=idx, content=text)


# 来源: 项目1382 招标.pdf 真实 chunk (经 pdfplumber 解析后, 已规整化空白)
_GOLDEN_CHUNKS = [
    # ── 章节1-4: 非模板正文; 含"投标函/法定代表人授权委托书"提及, 但不应入选 ──
    _chunk(
        4,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "投标邀请书\n"
        "若投标人的法定代表人委托代理人出席开标会议,在开标会议开始前向开\n"
        "标会议监督人员提交法定代表人授权委托书、本人身份证复印件。",
        1,
    ),
    _chunk(
        11,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "第二章 投标人须知\n"
        "投标文件由商务标和技术标组成。\n"
        "( ) 投标函\n"
        "( ) 开标一览表\n"
        "投标人递交的投标文件必须毫无例外地使用招标文件所提供的投标文件全部格式。",
        2,
    ),
    _chunk(
        21,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "第三章 评标办法\n"
        "中标通知书具有法律效力。",
        3,
    ),
    _chunk(
        28,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "第四章 合同条款\n"
        "第六条 合同价款、验收及结算方式\n"
        "受托方(乙方)汇票账户信息: 开户银行: 账户名称: 账号:",
        4,
    ),
    # ── 第五章 投标文件格式: 封面 + 目录 + 模板正文 ──
    _chunk(
        39,
        "第五章 投标文件格式\n"
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "投 标 文 件\n技术标\n( )\n投 标 人: (盖单位章)\n"
        "法定代表人或\n其委托代理人: (签字)",
        5,
    ),
    _chunk(
        40,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "目 录\n"
        "一、 投标函\n"
        "二、施工资质准入\n"
        "三、投标保证金缴纳证明(内部单位无需提供)\n"
        "四、法定代表人身份证明\n"
        "五、法定代表人授权委托书\n"
        "六、营业执照、组织机构代码证、税务登记证(或三证合一证件)\n"
        "七、投标人基本情况及人员配置表及公司简介",
        6,
    ),
    _chunk(
        41,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "一、投标函\n"
        "致:<ORG_ENERGY_1>\n"
        "根据贵方投标邀请书,本人正式代表投标方(投标方名称)提交下述投标文件。\n"
        "本人代表投标方保证,所提供的全部资料是真实的。\n"
        "地址: 电子邮箱: 电话: 传真:\n"
        "投标方名称: (盖章)\n"
        "投标方法定代表人或授权委托人:(签字)\n"
        "职务: 日期: 年 月 日",
        7,
    ),
    _chunk(
        43,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "三、投标保证金缴纳证明\n"
        "致:<ORG_ENERGY_1>:\n"
        "本投标人自愿参加宁东油田防腐药剂及防腐工具性能测试项目的投标。\n"
        "投 标 人: (盖单位章)\n"
        "法定代表人或其委托代理人: (签字)\n"
        "年 月 日",
        8,
    ),
    _chunk(
        44,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "四、法定代表人证明书\n"
        "致:<ORG_ENERGY_1>\n"
        "(法定代表人姓名)现任我单位 职务,为法定代表人(负责人)。\n"
        "附:姓 名: 性别: 年龄: 职务:\n"
        "特此证明\n"
        "投标人: (公章)\n"
        "日 期: 年 月 日\n"
        "附法定代表人身份证复印件",
        9,
    ),
    _chunk(
        45,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "五、法定代表人授权委托书\n"
        "致:<ORG_ENERGY_1>\n"
        "本人系企业法定代表人,特授权代表我单位全权办理投标、谈判、签约等具体工作。\n"
        "被授权人签名: 职务:\n"
        "法定代表人签名: 职务:\n"
        "投标人(公章)\n"
        "日 期: 年 月 日",
        10,
    ),
    _chunk(
        46,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "六、 营业执照\n(复印件盖单位章)",
        11,
    ),
    _chunk(
        47,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "七、 投标人基本情况及人员配置表及公司简介\n"
        "单位名称 单位地址\n联系电话 联系人\n注册资金\n成立时间\n万元\n"
        "法人代表\n营业执照号\n职称等级\n职工人数 总人数: 人",
        12,
    ),
    # ── 第六章 商务标的投标文件格式 ──
    _chunk(
        50,
        "第六章 商务标的投标文件格式\n"
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "投 标 文 件\n商务标\n投 标 人: (盖单位章)",
        13,
    ),
    _chunk(
        51,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "目 录\n一、投标函\n二、开标一览表\n三、开户许可证\n四、投标报价\n五、投标人认为必要其它证明材料",
        14,
    ),
    _chunk(
        52,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "一、投标函\n"
        "致:<ORG_ENERGY_1>\n"
        "本人正式代表投标方(投标方名称)提交下述投标文件,内容包括商务标。\n"
        "投标方名称: (盖章)\n"
        "投标方法定代表人或授权委托人:(签字)\n"
        "职务: 日期: 年 月 日",
        15,
    ),
    _chunk(
        53,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "二、开标一览表\n"
        "(招标人名称):<ORG_ENERGY_1>\n"
        "项目名称 报价 备注\n"
        "投标人名称(盖章):\n"
        "法定代表人(法定授权代表人)签名:\n"
        "日期: 年 月 日",
        16,
    ),
    _chunk(
        55,
        "宁东油田防腐药剂及防腐工具性能测试\n"
        "四、投 标 报 价 书\n"
        "(招标人):<ORG_ENERGY_1>\n"
        "我方已仔细研究了招标文件全部内容,愿意在最高限价的基础上整体下浮 (不含增值税)。\n"
        "投 标 人: (盖单位章)\n"
        "法定代表人或其委托代理人: (签字)\n"
        "日 期: 年 月 日",
        17,
    ),
]

_ALLOWED_PAGES = {41, 43, 44, 45, 47, 52, 53, 55}
_FORBIDDEN_PAGES = {4, 11, 21, 28, 40, 51}


async def _mock_recognize_templates(chunks_text, ranges, project_id=None):
    """PoC 实测返回的核心模板条目（压缩版），用于回放真实 chunk 场景。"""
    return [
        {"template_key": "bid_letter", "title": "投标函", "volume_type": "technical", "source_page_start": 41, "source_page_end": 41, "raw_text": "一、投标函\n致:<ORG_ENERGY_1>\n投标方名称: (盖章)\n投标方法定代表人或授权委托人:(签字)\n日期: 年 月 日", "fields": ["投标方名称"], "signature_slots": ["盖章", "签字", "日期"], "confidence": 1.0},
        {"template_key": "bid_security_certificate", "title": "投标保证金缴纳证明", "volume_type": "technical", "source_page_start": 43, "source_page_end": 43, "raw_text": "三、投标保证金缴纳证明\n致:<ORG_ENERGY_1>:\n本投标人自愿参加本项目投标。\n投 标 人: (盖单位章)\n法定代表人或其委托代理人: (签字)\n年 月 日", "fields": ["投标人"], "signature_slots": ["盖单位章", "签字", "日期"], "confidence": 1.0},
        {"template_key": "legal_representative_certificate", "title": "法定代表人证明书", "volume_type": "technical", "source_page_start": 44, "source_page_end": 44, "raw_text": "四、法定代表人证明书\n致:<ORG_ENERGY_1>\n(法定代表人姓名)现任我单位 职务,为法定代表人。\n投标人: (公章)\n日 期: 年 月 日", "fields": ["法定代表人姓名", "职务"], "signature_slots": ["公章", "日期"], "confidence": 1.0},
        {"template_key": "legal_representative_authorization", "title": "法定代表人授权委托书", "volume_type": "technical", "source_page_start": 45, "source_page_end": 45, "raw_text": "五、法定代表人授权委托书\n致:<ORG_ENERGY_1>\n本人系企业法定代表人,特授权代表办理投标。\n被授权人签名:\n投标人(公章)\n日 期: 年 月 日", "fields": ["法定代表人"], "signature_slots": ["公章", "日期"], "confidence": 1.0},
        {"template_key": "bidder_basic_info", "title": "投标人基本情况及人员配置表", "volume_type": "technical", "source_page_start": 47, "source_page_end": 47, "raw_text": "七、投标人基本情况及人员配置表及公司简介\n单位名称 单位地址\n联系电话 联系人\n注册资金\n成立时间\n法人代表\n营业执照号", "fields": ["单位名称", "联系电话"], "signature_slots": [], "confidence": 1.0},
        {"template_key": "bid_letter", "title": "投标函", "volume_type": "commercial", "source_page_start": 52, "source_page_end": 52, "raw_text": "一、投标函\n致:<ORG_ENERGY_1>\n投标方名称: (盖章)\n投标方法定代表人或授权委托人:(签字)\n日期: 年 月 日", "fields": ["投标方名称"], "signature_slots": ["盖章", "签字", "日期"], "confidence": 1.0},
        {"template_key": "bid_opening_table", "title": "开标一览表", "volume_type": "commercial", "source_page_start": 53, "source_page_end": 53, "raw_text": "二、开标一览表\n(招标人名称):<ORG_ENERGY_1>\n项目名称 报价 备注\n投标人名称(盖章):\n法定代表人(法定授权代表人)签名:\n日期: 年 月 日", "fields": ["项目名称", "报价"], "signature_slots": ["盖章", "签名", "日期"], "confidence": 1.0},
        {"template_key": "bid_quote", "title": "投标报价书", "volume_type": "commercial", "source_page_start": 55, "source_page_end": 55, "raw_text": "四、投 标 报 价 书\n(招标人):<ORG_ENERGY_1>\n我方已仔细研究了招标文件全部内容。\n投 标 人: (盖单位章)\n法定代表人或其委托代理人: (签字)\n日 期: 年 月 日", "fields": ["投标人"], "signature_slots": ["盖单位章", "签字", "日期"], "confidence": 1.0},
    ]


@pytest.mark.asyncio
async def test_golden_catalog_only_picks_real_templates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _mock_recognize_templates,
    )
    catalog = await build_fixed_template_catalog_async(_GOLDEN_CHUNKS, project_id=1383)
    assert catalog, "catalog 不应为空"

    template_keys = {entry.template_key for entry in catalog}
    page_starts = {entry.source_page_start for entry in catalog}

    # 必须包含的真实模板
    assert "bid_letter" in template_keys
    assert "legal_representative_certificate" in template_keys
    assert "legal_representative_authorization" in template_keys
    assert "bid_security_certificate" in template_keys
    assert "bid_opening_table" in template_keys
    assert "bidder_basic_info" in template_keys

    # page_start 必须只来自真实模板页, 不应包含投标须知 / 合同条款 / 目录页
    assert page_starts.issubset(_ALLOWED_PAGES), (
        f"page_starts={sorted(page_starts)} forbidden={_FORBIDDEN_PAGES & page_starts}"
    )
    assert not page_starts & _FORBIDDEN_PAGES


@pytest.mark.asyncio
async def test_golden_catalog_excludes_generic_format_requirement(monkeypatch) -> None:
    """第二章的'投标函/开标一览表'仅作为材料清单出现, 不应混入。"""
    monkeypatch.setattr(
        "app.core.writer.fixed_template.catalog.recognize_templates",
        _mock_recognize_templates,
    )
    catalog = await build_fixed_template_catalog_async(_GOLDEN_CHUNKS, project_id=1383)
    for entry in catalog:
        # 模板正文必须真实包含模板字段, 不应来自 page=11 的目录性引用
        assert entry.source_page_start != 11
        assert "评标委员会" not in entry.raw_text
        assert "投标准备会" not in entry.raw_text
