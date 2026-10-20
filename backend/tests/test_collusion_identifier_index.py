from tests.synthetic_data import synthetic_text

"""主体标识符索引单元测试"""

from app.core.collusion.identifier_index import (
    FIELD_COMPANY_NAME,
    FIELD_LEGAL_REP,
    FIELD_PDF_AUTHOR,
    FIELD_PERSON_NAME,
    FIELD_SUBCONTRACTOR,
    build_index,
)


def test_index_extracts_company_basics() -> None:
    extraction = {
        "company_info": {
            "company_name": "<ORG_23>",
            "legal_representative": '<PERSON_A>',
            "contact_phone": synthetic_text('138', '0000', '0001'),
            "bank_account": "6228 4800 0000 0000",
            "unified_social_credit_code": synthetic_text('914403', '00ABC1', '234567'),
            "source_page": "1",
        },
        "personnel_list": [],
        "metadata_info": {"author": '<PERSON_B>', "creator_tool": "Microsoft Word"},
    }
    idx = build_index(1, "测试.pdf", extraction)
    assert idx.doc_id == 1
    assert idx.doc_name == "测试.pdf"

    # 公司名归一化（去掉"有限公司"后缀）
    assert any("甲建设" in k for k in idx.company_names.keys())
    # 法定代表人同时进入 person_names 索引
    assert any('<PERSON_A>' in k for k in idx.legal_representatives.keys())
    assert any('<PERSON_A>' in k for k in idx.person_names.keys())
    # 电话归一化
    assert synthetic_text('138', '0000', '0001') in idx.phones
    # 信用代码归一化
    assert synthetic_text('914403', '00ABC1', '234567') in idx.credit_codes
    # PDF 作者
    assert any('<PERSON_B>' in k for k in idx.pdf_authors.keys())


def test_index_field_ref_paths() -> None:
    extraction = {
        "company_info": {
            "company_name": "A公司",
            "legal_representative": "<PERSON_3>",
        },
        "personnel_list": [
            {"name": "<PERSON_G>", "role": "项目经理"},
        ],
        "subcontractors": ["X分包公司"],
        "metadata_info": {"author": "<PERSON_F>"},
    }
    idx = build_index(1, "A.pdf", extraction)

    # 法定代表人字段路径正确
    lr_refs = idx.legal_representatives.get("<PERSON_3>") or []
    assert lr_refs and lr_refs[0].field_path == FIELD_LEGAL_REP

    # 公司名字段路径
    company_refs = next(iter(idx.company_names.values()))
    assert company_refs[0].field_path == FIELD_COMPANY_NAME

    # 项目人员字段路径
    person_refs = idx.person_names.get("<PERSON_G>") or []
    assert person_refs and person_refs[0].field_path == FIELD_PERSON_NAME

    # 分包商字段路径
    sub_refs = next(iter(idx.subcontractors.values()))
    assert sub_refs[0].field_path == FIELD_SUBCONTRACTOR

    # PDF 作者字段路径
    author_refs = next(iter(idx.pdf_authors.values()))
    assert author_refs[0].field_path == FIELD_PDF_AUTHOR


def test_index_handles_invalid_person_name() -> None:
    """非真实人名（如岗位名）不应进入 person_names"""
    extraction = {
        "personnel_list": [
            {"name": "项目经理", "role": "项目经理"},  # name == 岗位
            {"name": "<PERSON_C>", "role": "项目经理"},
        ],
    }
    idx = build_index(1, "A.pdf", extraction)
    # 仅"<PERSON_C>"应进入索引
    assert any("<PERSON_C>" in k for k in idx.person_names.keys())
    assert not any("项目经理" in k for k in idx.person_names.keys())


def test_index_handles_empty_extraction() -> None:
    idx = build_index(1, "empty.pdf", {})
    assert idx.doc_id == 1
    assert not idx.company_names
    assert not idx.person_names
    assert not idx.pdf_authors


def test_index_handles_none_extraction() -> None:
    idx = build_index(1, "x.pdf", None)  # type: ignore
    assert idx.doc_id == 1
    assert not idx.company_names
