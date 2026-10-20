""": 串标 PDF 对N / pair_id 替换与结果页 buildPairRefReplacer 同口径。"""

from app.core.report.collusion_generator import _build_pair_ref_replacer
from app.schemas.collusion import CollusionPairResult


def _pair(
    *,
    pair_id: str,
    doc_a_id: int,
    doc_b_id: int,
    doc_a_name: str,
    doc_b_name: str,
    overall_risk: str,
    risk_level: str | None = None,
) -> CollusionPairResult:
    return CollusionPairResult(
        pair_id=pair_id,
        doc_a_id=doc_a_id,
        doc_b_id=doc_b_id,
        doc_a_name=doc_a_name,
        doc_b_name=doc_b_name,
        overall_risk=overall_risk,
        risk_level=risk_level,
    )


def test_pair_id_replaced_with_bracket_label() -> None:
    replacer = _build_pair_ref_replacer(
        [
            _pair(
                pair_id="1:2",
                doc_a_id=1,
                doc_b_id=2,
                doc_a_name="甲文件.pdf",
                doc_b_name="乙文件.pdf",
                overall_risk="medium",
            ),
        ]
    )
    text = replacer("交叉比对见 1:2，请复核。")
    assert "【甲文件×乙文件】" in text
    assert "1:2" not in text
    assert "(甲文件 vs 乙文件)" not in text


def test_risk_level_outranks_overall_risk_for_dui_index() -> None:
    """risk_level=high 即使 overall_risk=low，也应排在 medium 之前成为对1。"""
    high_pair = _pair(
        pair_id="1:3",
        doc_a_id=1,
        doc_b_id=3,
        doc_a_name="甲文件.pdf",
        doc_b_name="丙文件.pdf",
        overall_risk="low",
        risk_level="high",
    )
    medium_pair = _pair(
        pair_id="1:2",
        doc_a_id=1,
        doc_b_id=2,
        doc_a_name="甲文件.pdf",
        doc_b_name="乙文件.pdf",
        overall_risk="medium",
        risk_level="medium",
    )
    replacer = _build_pair_ref_replacer([medium_pair, high_pair])
    text = replacer("对1 与对2 均需关注。")
    assert text.index("【甲文件×丙文件】") < text.index("【甲文件×乙文件】")


def test_orphan_dui_ref_becomes_invalid() -> None:
    replacer = _build_pair_ref_replacer(
        [
            _pair(
                pair_id="1:2",
                doc_a_id=1,
                doc_b_id=2,
                doc_a_name="甲文件.pdf",
                doc_b_name="乙文件.pdf",
                overall_risk="medium",
            ),
        ]
    )
    text = replacer("对99 不存在。")
    assert "（引用无效）" in text
    assert "对99" not in text


def test_file_pair_doc_ids_replaced_with_uploaded_filenames() -> None:
    replacer = _build_pair_ref_replacer(
        [
            _pair(
                pair_id="2517:2518",
                doc_a_id=2517,
                doc_b_id=2518,
                doc_a_name="甲公司投标文件.pdf",
                doc_b_name="乙公司技术方案.pdf",
                overall_risk="medium",
            ),
        ]
    )
    text = replacer("针对文件对2517与2518的分析显示，元数据时间戳接近。")
    assert "引用无效" not in text
    assert "2517" not in text
    assert "2518" not in text
    assert "甲公司投标文件" in text
    assert "乙公司技术方案" in text


def test_document_span_doc_ids_replaced_with_uploaded_filenames() -> None:
    """同形态第二例：文档{id}与{id} / doc_id= 同样必须变成上传文件名。"""
    replacer = _build_pair_ref_replacer(
        [
            _pair(
                pair_id="101:205",
                doc_a_id=101,
                doc_b_id=205,
                doc_a_name="华南路桥施工组织设计.pdf",
                doc_b_name="华北市政投标文件.pdf",
                overall_risk="low",
            ),
        ]
    )
    text = replacer("针对文档101与205的分析显示，doc_id=101 已复核。")
    assert "101" not in text
    assert "205" not in text
    assert "华南路桥施工组织设计" in text
    assert "华北市政投标文件" in text


def test_year_and_percent_not_treated_as_doc_ids() -> None:
    replacer = _build_pair_ref_replacer(
        [
            _pair(
                pair_id="2018:12",
                doc_a_id=2018,
                doc_b_id=12,
                doc_a_name="年份碰撞.pdf",
                doc_b_name="百分碰撞.pdf",
                overall_risk="low",
            ),
        ]
    )
    text = replacer("合同签约于2018年，下浮12%。")
    assert "2018年" in text
    assert "12%" in text


def test_invalid_evidence_source_parenthetical_stripped() -> None:
    replacer = _build_pair_ref_replacer([])
    text = replacer("结论成立（证据来源： （引用无效））。")
    assert "证据来源" not in text
    assert "引用无效" not in text
    assert "结论成立" in text
