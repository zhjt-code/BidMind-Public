"""文档门禁引擎防御测试

风险映射:
- 门禁误伤正规标书(尤其只在目录页出现要素词的文档、扫描件) → 高业务风险
- 门禁放过明显非标书文档 → 门禁功能失效
- 阈值边界漂移 → 判定行为静默变化
"""

from pathlib import Path

import pytest

from app.core.parser.doc_gate.contracts import DocGateResult
from app.core.parser.doc_gate.gate_engine import (
    PASS_THRESHOLD,
    TOC_BONUS_LARGE,
    UNREADABLE_MIN_CHARS,
    evaluate_doc_gate,
)
from app.core.parser.doc_gate.sampler import GateSample, sample_pdf_bytes

pytestmark = pytest.mark.scoring


def _sample(page_texts: list[str], page_count: int | None = None) -> GateSample:
    return GateSample(
        page_count=page_count if page_count is not None else len(page_texts),
        sampled_pages=len(page_texts),
        page_texts=page_texts,
    )


def _scoring_excerpt_text() -> str:
    """构造会同时误命中招标/投标要素的评分表摘录。"""
    return (
        "评审因素 分值 评分标准\n"
        "投标人须提供投标截止时间前六个月的证明材料，供采购人核验。\n"
        "根据技术要求响应情况和技术方案完整性评分。\n"
        "投标报价以评标基准价计算，证书复印件须加盖公章。\n"
        "售后服务承诺完整得满分，未提供承诺函不得分。\n"
        + "本表仅列示评审因素、分值与评分说明。" * 8
    )


# ==================== 正常路径: 正规文档放行 ====================

class TestPassVerdict:
    def test_typical_tender_body_text_passes(self):
        """正规招标文件正文要素齐全 → pass"""
        sample = _sample([
            "XX市政务云平台建设项目 招标公告\n招标编号: ZB2026-001\n采购人: XX市大数据局",
            "一、投标人须知\n投标截止时间: 2026年8月1日9时\n开标时间: 2026年8月1日9时30分",
            "二、评标办法\n本项目采用综合评分法，评分标准如下",
            "三、资格要求\n投标人须具备有效营业执照。投标保证金: 人民币5万元",
            "四、技术需求\n本项目技术要求详见采购需求书。合同条款见第五章",
        ], page_count=80)
        result = evaluate_doc_gate(sample, "tender")
        assert result.verdict == "pass"
        assert result.score >= PASS_THRESHOLD
        assert "投标人须知" in "".join(result.hit_features)

    def test_tender_with_toc_only_keywords_passes(self):
        """要素词只出现在目录行中(正文采样页未到) → 目录1.5倍权重应使其pass

        对应真实场景: 招标文件前几页只有封面+目录，须知/评标办法正文在后面。
        """
        toc_page = "\n".join([
            "目录",
            "第一章 招标公告 ................ 1",
            "第二章 投标人须知 .............. 5",
            "第三章 评标办法 ................ 20",
            "第四章 合同条款 ................ 30",
            "第五章 技术规格 ................ 40",
            "第六章 投标文件格式 ............ 55",
        ])
        sample = _sample(["XX项目招标文件\n" + "封面页" * 30, toc_page], page_count=60)
        result = evaluate_doc_gate(sample, "tender")
        assert result.verdict == "pass"
        # 目录命中应有标记
        assert any("目录命中" in h for h in result.hit_features)
        assert result.toc_entry_count >= 5

    def test_typical_bid_document_passes(self):
        """正规投标文件 → pass"""
        sample = _sample([
            '投标文件\n项目编号: CG2026-100\n投标人: <ORG_D>(盖章)',
            "一、投标函\n致: XX采购中心\n我方决定参加贵方组织的投标，"
            "并已详细审查全部招标文件，愿意按照招标文件的要求提供服务。",
            '二、法定代表人授权委托书\n兹授权委托<PERSON_C>某为我方代理人，'
            + "以我方名义处理投标事宜。(法定代表人签字盖章)",
            "三、开标一览表\n投标报价: 人民币100万元\n投标保证金已按要求缴纳，"
            "报价包含完成本项目全部工作内容的一切费用。",
            "四、技术方案\n本方案针对项目需求进行了详细设计。\n"
            "五、资格证明文件: 营业执照、资质证书复印件加盖公章。",
        ], page_count=120)
        result = evaluate_doc_gate(sample, "bid")
        assert result.verdict == "pass"

    @pytest.mark.parametrize(
        ("page_count", "expected_verdict", "expected_fallback"),
        [
            (29, "suspect", False),
            (30, "pass", True),
            (80, "pass", True),
        ],
    )
    def test_tender_without_toc_uses_bounded_long_document_fallback(
        self,
        page_count: int,
        expected_verdict: str,
        expected_fallback: bool,
    ):
        """无可信目录仅在页数达标且强特征跨页时走兜底通道。"""
        sample = _sample([
            "XX项目招标公告\n项目编号: ZB2026-001\n" + "项目背景说明。" * 20,
            "第一章 投标人须知\n供应商应按本章要求编制文件。" + "须知正文。" * 20,
        ], page_count=page_count)

        result = evaluate_doc_gate(sample, "tender")

        assert result.score >= PASS_THRESHOLD
        assert result.toc_entry_count == 0
        assert result.verdict == expected_verdict
        assert result.has_confident_toc is False
        assert result.has_long_document_fallback is expected_fallback

    def test_tender_with_space_separated_toc_and_page_suffix_passes(self):
        """目录标题下的空格/制表符及“第N页”格式应被轻量识别。"""
        toc_page = "\n".join([
            "目 录",
            "第一章 招标公告        第 1 页",
            "第二章 投标人须知\t\t第 5 页",
            "第三章 评标办法        第 20 页",
        ])
        sample = _sample(["XX项目招标文件\n" + "封面页" * 30, toc_page], page_count=60)

        result = evaluate_doc_gate(sample, "tender")

        assert result.verdict == "pass"
        assert result.toc_entry_count == 3
        assert result.has_confident_toc is True
        assert result.has_long_document_fallback is False
        assert any("目录命中" in hit for hit in result.hit_features)

    def test_short_bid_with_bounded_toc_heading_variant_passes(self):
        """短投标文件的“投标文件目录”标题应进入可信目录主通道。"""
        toc_page = "\n".join([
            "投标文件目录",
            "第一章 投标函 ........ 1",
            "第二章 法定代表人授权委托书 ........ 5",
            "第三章 报价一览表 ........ 10",
        ])
        sample = _sample([
            toc_page + "\n" + "本目录列示投标文件各章节及对应页码。" * 10,
        ], page_count=22)

        result = evaluate_doc_gate(sample, "bid")

        assert result.verdict == "pass"
        assert result.toc_entry_count == 3
        assert result.has_confident_toc is True


# ==================== 边界/异常路径: 非标书文档识别 ====================

class TestSuspectVerdict:
    def test_random_article_is_suspect(self):
        """随便找的文章类PDF → suspect, 且给出可读理由"""
        sample = _sample([
            "人工智能技术发展综述\n近年来深度学习技术取得了显著进展，"
            "在图像识别、自然语言处理等领域得到广泛应用。" * 5,
            "本文首先回顾了神经网络的发展历史，然后分析了当前的技术瓶颈。" * 5,
        ], page_count=8)
        result = evaluate_doc_gate(sample, "tender")
        assert result.verdict == "suspect"
        assert result.reasons, "suspect必须给出怀疑理由"
        assert any("关键要素" in r for r in result.reasons)
        assert any("目录" in r for r in result.reasons)
        assert any("8页" in r for r in result.reasons)

    def test_manual_with_toc_alone_cannot_pass(self):
        """带目录的产品手册: 目录结构加成不足以单独放行

        防御: PASS_THRESHOLD 必须高于最大目录结构加成，
        否则任意带目录的文档都会被放行。
        """
        assert PASS_THRESHOLD > TOC_BONUS_LARGE

        toc_page = "\n".join([
            "目录",
            "第一章 产品概述 ................ 1",
            "第二章 安装指南 ................ 5",
            "第三章 使用说明 ................ 12",
            "第四章 故障排查 ................ 30",
            "第五章 保修条款 ................ 45",
            "附录A 规格参数 ................. 50",
            "附录B 联系方式 ................. 52",
            "附录C 版本历史 ................. 53",
            "附录D 常见问题 ................. 54",
            "附录E 术语表 ................... 56",
        ])
        sample = _sample([
            "XX路由器用户手册\n感谢您购买本产品，使用前请仔细阅读本手册。" * 3,
            toc_page,
        ], page_count=60)
        result = evaluate_doc_gate(sample, "tender")
        assert result.verdict == "suspect"

    def test_contract_document_is_suspect_as_bid(self):
        """普通合同文档当投标文件传 → suspect(要素零散不达阈值)"""
        sample = _sample([
            '房屋租赁合同\n甲方: <PERSON_A>\n乙方: <PERSON_B>\n双方签字盖章后生效。\n'
            + "租金按月支付，逾期支付的按日加收违约金。租赁期满后乙方应"
            "将房屋恢复原状并交还甲方，水电费用由乙方自行承担结清。" * 3,
        ], page_count=3)
        result = evaluate_doc_gate(sample, "bid")
        assert result.verdict == "suspect"

    @pytest.mark.parametrize("doc_type", ["tender", "bid"])
    def test_short_scoring_excerpt_is_suspect_despite_high_feature_score(
        self,
        doc_type: str,
    ):
        """: 评分表摘录不能仅凭招投标词面累计超过阈值而放行。"""
        sample = _sample([_scoring_excerpt_text()], page_count=14)

        result = evaluate_doc_gate(sample, doc_type)

        assert result.score >= PASS_THRESHOLD
        assert result.toc_entry_count == 0
        assert result.verdict == "suspect"
        assert result.has_confident_toc is False
        assert result.has_long_document_fallback is False
        assert any("完整" in reason for reason in result.reasons)

    def test_long_keyword_dump_without_cross_page_distribution_is_suspect(self):
        """长文档兜底要求强特征跨页，单页关键词堆积不能穿透。"""
        sample = _sample([
            (
                "招标公告 投标人须知 评标办法 投标截止时间 "
                "项目编号 采购人 投标保证金 资格要求 技术要求 合同条款\n"
            )
            + "评分表说明。" * 30,
        ], page_count=80)

        result = evaluate_doc_gate(sample, "tender")

        assert result.score >= PASS_THRESHOLD
        assert result.toc_entry_count == 0
        assert result.verdict == "suspect"
        assert result.has_long_document_fallback is False

    def test_numbered_scoring_rows_without_toc_heading_are_not_toc(self):
        """编号评分表即使分值非递减，也不得被灵活目录格式误识别。"""
        sample = _sample([
            _scoring_excerpt_text()
            + "\n1.技术方案        5"
            + "\n2.实施团队        5"
            + "\n3.售后服务        5"
            + "\n4.质量保障        5"
            + "\n5.应急预案        5",
        ], page_count=40)

        result = evaluate_doc_gate(sample, "tender")

        assert result.toc_entry_count == 0
        assert result.verdict == "suspect"

    def test_dotted_scoring_rows_without_toc_heading_are_not_toc(self):
        """点线/破折号评分表占位行也不能绕过可信目录上下文。"""
        sample = _sample([
            _scoring_excerpt_text()
            + "\n技术方案 ...... 10"
            + "\n实施团队 —— 20"
            + "\n售后服务 ...... 30",
        ], page_count=40)

        result = evaluate_doc_gate(sample, "tender")

        assert result.toc_entry_count == 0
        assert result.has_confident_toc is False
        assert result.verdict == "suspect"


class TestUnreadableVerdict:
    def test_scanned_pdf_without_text_is_unreadable(self):
        """扫描件(无文本层) → unreadable, 文案区别于suspect"""
        sample = _sample(["", "", ""], page_count=50)
        result = evaluate_doc_gate(sample, "bid")
        assert result.verdict == "unreadable"
        assert any("扫描件" in r or "图片型" in r for r in result.reasons)
        assert all("正规" not in reason for reason in result.reasons)

    def test_near_empty_text_boundary(self):
        """文本量恰好低于阈值 → unreadable; 高于阈值 → 进入正常打分"""
        below = _sample(["a" * (UNREADABLE_MIN_CHARS - 1)])
        assert evaluate_doc_gate(below, "tender").verdict == "unreadable"

        above = _sample(["a" * (UNREADABLE_MIN_CHARS + 1)])
        assert evaluate_doc_gate(above, "tender").verdict != "unreadable"


# ==================== 契约与落库兼容 ====================

class TestContract:
    def test_result_is_json_serializable(self):
        """结果必须可model_dump落库(documents.gate_result JSON列)"""
        sample = _sample(["招标公告 投标人须知 评标办法 招标编号 开标时间"])
        result = evaluate_doc_gate(sample, "tender")
        payload = result.model_dump()
        assert payload["verdict"] in ("pass", "suspect", "unreadable", "encrypted", "corrupt", "empty")
        assert isinstance(payload["reasons"], list)
        # 可从落库JSON还原
        assert DocGateResult.model_validate(payload).verdict == result.verdict

    def test_pass_has_no_reasons(self):
        sample = _sample([
            "\n".join([
                "目录",
                "第一章 招标公告 ........ 1",
                "第二章 投标人须知 ........ 5",
                "第三章 评标办法 ........ 20",
                "第四章 技术要求 ........ 30",
                "第五章 合同条款 ........ 40",
            ])
            + "\n"
            + "本目录用于说明招标文件正文的章节结构与对应页码。" * 8,
        ], page_count=60)
        result = evaluate_doc_gate(sample, "tender")
        assert result.verdict == "pass"
        assert result.reasons == []


# ==================== 采样器: 真实PDF冒烟(依赖本地样本, 缺失则跳过) ====================

_REAL_TENDER_PDF = Path(__file__).resolve().parents[2] / "storage" / "uploads" / "2233" / "tender_招标.pdf"


@pytest.mark.skipif(not _REAL_TENDER_PDF.exists(), reason="本地真实招标样本不存在")
def test_real_tender_pdf_passes_gate():
    """真实招标文件全链路(采样+打分) → 必须pass, 防止门禁误伤正规文档"""
    content = _REAL_TENDER_PDF.read_bytes()
    sample = sample_pdf_bytes(content)
    assert sample.page_count > 0
    result = evaluate_doc_gate(sample, "tender")
    assert result.verdict == "pass", f"真实招标文件被误判: {result.model_dump()}"
