"""主体标识符索引

从 CollusionExtraction（含 metadata_info）中抽取统一格式的主体标识符，
为跨字段串联引擎（cross_field_correlator）提供输入。

设计原则：
- 仅做数据抽取与归一化，不做匹配判断
- 每个标识符值都附带 FieldRef（字段路径 + 页码 + 原值），便于证据回溯
- 复用 normalizers 模块的归一化函数，保持与 GlobalFeatureAnalyzer 一致

字段范围对应业内权威方法论（财政部令第87号 / 招标投标法实施条例第40条）：
- 主体类: 公司名、人名（含法人）
- 通讯类: 电话、邮箱
- 凭证类: 统一信用代码、资格证书号、银行账号
- 元数据类: PDF author / creator / producer
- 关联实体: 分包商
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.collusion.normalizers import (
    email_local_part,
    normalize_bank_account,
    normalize_cert_code,
    normalize_company_name,
    normalize_credit_code,
    normalize_email,
    normalize_person_name,
    normalize_phone,
)
from app.schemas.collusion import is_valid_person_name


# 字段路径常量（用于跨字段命中时定位证据来源）
FIELD_COMPANY_NAME = "company_info.company_name"
FIELD_LEGAL_REP = "company_info.legal_representative"
FIELD_REG_ADDRESS = "company_info.registered_address"
FIELD_CONTACT_PHONE = "company_info.contact_phone"
FIELD_BANK = "company_info.bank_account"
FIELD_CREDIT = "company_info.unified_social_credit_code"
FIELD_PERSON_NAME = "personnel_list[].name"
FIELD_PERSON_CERT = "personnel_list[].certificate_numbers"
FIELD_PERSON_PHONE = "personnel_list[].phone"
FIELD_PERSON_EMAIL = "personnel_list[].email"
FIELD_SUBCONTRACTOR = "subcontractors"
FIELD_PDF_AUTHOR = "metadata_info.author"
FIELD_PDF_CREATOR = "metadata_info.creator_tool"
FIELD_PDF_PRODUCER = "metadata_info.producer"
FIELD_PDF_TITLE = "metadata_info.title"


@dataclass
class FieldRef:
    """字段引用：标识符值的来源定位"""
    field_path: str
    raw_value: str
    page: Optional[str] = None


@dataclass
class IdentifierIndex:
    """单份投标文件的主体标识符索引

    每个字典：归一化后的值 -> 来源引用列表（同一值可能在多个字段出现）
    """
    doc_id: int
    doc_name: str

    # 主体类
    company_names: dict[str, list[FieldRef]] = field(default_factory=dict)
    person_names: dict[str, list[FieldRef]] = field(default_factory=dict)
    legal_representatives: dict[str, list[FieldRef]] = field(default_factory=dict)

    # 通讯类
    phones: dict[str, list[FieldRef]] = field(default_factory=dict)
    emails: dict[str, list[FieldRef]] = field(default_factory=dict)
    email_locals: dict[str, list[FieldRef]] = field(default_factory=dict)

    # 凭证类
    credit_codes: dict[str, list[FieldRef]] = field(default_factory=dict)
    cert_numbers: dict[str, list[FieldRef]] = field(default_factory=dict)
    bank_accounts: dict[str, list[FieldRef]] = field(default_factory=dict)

    # 元数据类
    pdf_authors: dict[str, list[FieldRef]] = field(default_factory=dict)
    pdf_creators: dict[str, list[FieldRef]] = field(default_factory=dict)
    pdf_producers: dict[str, list[FieldRef]] = field(default_factory=dict)

    # 关联实体
    subcontractors: dict[str, list[FieldRef]] = field(default_factory=dict)

    def add(
        self,
        bucket: dict[str, list[FieldRef]],
        normalized: str,
        ref: FieldRef,
    ) -> None:
        if not normalized:
            return
        bucket.setdefault(normalized, []).append(ref)


def _add(
    bucket: dict[str, list[FieldRef]],
    normalized: str,
    field_path: str,
    raw_value: str,
    page: Optional[str] = None,
) -> None:
    if not normalized:
        return
    raw = str(raw_value or "").strip()
    if not raw:
        return
    bucket.setdefault(normalized, []).append(
        FieldRef(field_path=field_path, raw_value=raw, page=page)
    )


def build_index(
    doc_id: int,
    doc_name: str,
    extraction: dict,
) -> IdentifierIndex:
    """从 CollusionExtraction（dict 形态）抽取主体标识符索引。

    Args:
        doc_id: 文档 ID
        doc_name: 文档显示名（如"BJ投标1.pdf"）
        extraction: CollusionExtraction.to_dict() 形态，
            含 company_info / personnel_list / subcontractors / metadata_info 等

    Returns:
        IdentifierIndex 对象
    """
    idx = IdentifierIndex(doc_id=doc_id, doc_name=doc_name)
    if not isinstance(extraction, dict):
        return idx

    # ── company_info ──
    company_info = extraction.get("company_info") or {}
    if isinstance(company_info, dict):
        company_page = _get_source_page(company_info)
        cname_raw = (company_info.get("company_name") or "").strip()
        if cname_raw:
            _add(
                idx.company_names,
                normalize_company_name(cname_raw),
                FIELD_COMPANY_NAME, cname_raw, company_page,
            )

        lr_raw = (company_info.get("legal_representative") or "").strip()
        if lr_raw and is_valid_person_name(lr_raw):
            normalized_lr = normalize_person_name(lr_raw)
            _add(idx.legal_representatives, normalized_lr,
                 FIELD_LEGAL_REP, lr_raw, company_page)
            _add(idx.person_names, normalized_lr,
                 FIELD_LEGAL_REP, lr_raw, company_page)

        phone_raw = (company_info.get("contact_phone") or "").strip()
        if phone_raw:
            _add(idx.phones, normalize_phone(phone_raw),
                 FIELD_CONTACT_PHONE, phone_raw, company_page)

        bank_raw = (company_info.get("bank_account") or "").strip()
        if bank_raw:
            _add(idx.bank_accounts, normalize_bank_account(bank_raw),
                 FIELD_BANK, bank_raw, company_page)

        credit_raw = (company_info.get("unified_social_credit_code") or "").strip()
        if credit_raw:
            _add(idx.credit_codes, normalize_credit_code(credit_raw),
                 FIELD_CREDIT, credit_raw, company_page)

    # ── personnel_list ──
    personnel = extraction.get("personnel_list") or []
    if isinstance(personnel, list):
        for person in personnel:
            if not isinstance(person, dict):
                continue
            name_raw = str(person.get("name") or "").strip()
            page = _coerce_page(person.get("source_page"))
            if name_raw and is_valid_person_name(name_raw):
                _add(idx.person_names, normalize_person_name(name_raw),
                     FIELD_PERSON_NAME, name_raw, page)

            certs = person.get("certificate_numbers") or []
            if isinstance(certs, list):
                for c in certs:
                    cert_raw = str(c or "").strip()
                    if cert_raw:
                        _add(idx.cert_numbers, normalize_cert_code(cert_raw),
                             FIELD_PERSON_CERT, cert_raw, page)

            phone_raw = str(person.get("phone") or "").strip()
            if phone_raw:
                _add(idx.phones, normalize_phone(phone_raw),
                     FIELD_PERSON_PHONE, phone_raw, page)

            email_raw = str(person.get("email") or "").strip()
            if email_raw:
                normalized_email = normalize_email(email_raw)
                if normalized_email:
                    _add(idx.emails, normalized_email,
                         FIELD_PERSON_EMAIL, email_raw, page)
                    local = email_local_part(normalized_email)
                    if local:
                        _add(idx.email_locals, local,
                             FIELD_PERSON_EMAIL, email_raw, page)

    # ── subcontractors ──
    subs = extraction.get("subcontractors") or []
    if isinstance(subs, list):
        for s in subs:
            sub_raw = str(s or "").strip()
            if sub_raw:
                _add(idx.subcontractors, normalize_company_name(sub_raw),
                     FIELD_SUBCONTRACTOR, sub_raw, None)

    # ── metadata_info ──
    meta = extraction.get("metadata_info") or {}
    if isinstance(meta, dict):
        author_raw = str(meta.get("author") or "").strip()
        if author_raw:
            normalized_author = _normalize_metadata_actor(author_raw)
            if normalized_author:
                _add(idx.pdf_authors, normalized_author,
                     FIELD_PDF_AUTHOR, author_raw, None)

        creator_raw = str(meta.get("creator_tool") or "").strip()
        if creator_raw:
            normalized_creator = _normalize_metadata_actor(creator_raw)
            if normalized_creator:
                _add(idx.pdf_creators, normalized_creator,
                     FIELD_PDF_CREATOR, creator_raw, None)

        producer_raw = str(meta.get("producer") or "").strip()
        if producer_raw:
            normalized_producer = _normalize_metadata_actor(producer_raw)
            if normalized_producer:
                _add(idx.pdf_producers, normalized_producer,
                     FIELD_PDF_PRODUCER, producer_raw, None)

    return idx


def _normalize_metadata_actor(value: str) -> str:
    """归一化 PDF metadata 中的 author/creator/producer 字段。

    保持与 normalize_person_name 一致的策略，让"作者=<PERSON_1>"能与
    "法定代表人=<PERSON_1>"匹配。但对工具字符串（如"Microsoft Word"）也保留。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # 与 normalize_person_name 走相同管线，确保跨字段比对一致
    return normalize_person_name(text)


def _get_source_page(company_info: dict) -> Optional[str]:
    page = company_info.get("source_page")
    return _coerce_page(page)


def _coerce_page(page) -> Optional[str]:
    if page is None:
        return None
    text = str(page).strip()
    return text or None
